"""Provides connection utilities for communicating with a Trinnov device."""

# pylint: disable=too-many-arguments
# pylint: disable=too-many-positional-arguments
# pylint: disable=too-many-instance-attributes

import asyncio
import errno
import inspect
import logging
from asyncio import AbstractEventLoop
from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Any

import ucapi
from const import EntityPrefix
from pyee.asyncio import AsyncIOEventEmitter
from pytrinnov.models.base import EthernetStatus
from pytrinnov.models.constants import WS_ETHERNET, ConnectionStatus, EventType
from pytrinnov.trinnov.config import DEFAULT_PROTOCOL_PORT
from pytrinnov.trinnov.device import DeviceManager
from pytrinnov.trinnov.executor import CommandExecutor, PowerControl
from pytrinnov.trinnov.websocket import DeviceError, WebSocketClient
from ucapi import StatusCodes
from ucapi.media_player import Attributes as MediaAttr
from ucapi.media_player import States as MediaStates
from ucapi.remote import Attributes as RemoteAttr
from ucapi.remote import States as RemoteStates
from ucapi.sensor import Attributes as SensorAttr
from ucapi.sensor import States as SensorStates

ParamType = str | int | float | bool
ParamTuple = tuple[ParamType, ...]
ParamDict = dict[str, ParamType]

_LOG = logging.getLogger(__name__)

class Events(IntEnum):
    """Internal driver events."""

    CONNECTING = auto()
    CONNECTED = auto()
    DISCONNECTED = auto()
    UPDATE = auto()


@dataclass
class TrinnovInfo:
    """Represents Trinnov info including identity, network, and metadata."""
    id: str
    name: str
    ip: str
    mac: str
    model_name: str | None = None
    software_version: str | None = None

    def __repr__(self) -> str:
        return (
            f"<TrinnovDevice id='{self.id}' name='{self.name}' "
            f"address='{self.ip}' "
            f"model='{self.model_name}' version='{self.software_version}'>"
        )


class TrinnovDevice:
    """Handles communication with a Trinnov audio processor over TCP."""

    def __init__(
        self,
        ip: str,
        mac: str,
        device_id: str | None = None,
        loop: AbstractEventLoop | None = None,
    ):
        # Identifiers and connection info
        self.device_id = device_id or "unknown"
        self.ip = ip
        self.port = DEFAULT_PROTOCOL_PORT
        self.mac = mac

        # Internal device manager and event loop
        self._device = DeviceManager()
        self._event_loop = loop or asyncio.get_running_loop()

        # Connection state
        self._connected: bool = False
        self._connecting: bool = False
        self._was_intentional_disconnect: bool = False
        self._wait_task: asyncio.Task | None = None

        # Attribute state
        self._attr_state = MediaStates.OFF
        self._attr_volume: int | float = 0
        self._attr_audio_sync: bool = False
        self._attr_srate: str = "unknown"
        self._attr_input_labels: dict[str, str] = self._device.audio_settings.labels
        self._attr_sound_modes: dict[str, str] = self._device.audio_settings.sound_mode_list
        self._attr_muted: bool = False

        # Event system
        self.events = AsyncIOEventEmitter(loop=self._event_loop)

        # Subscribe to device state updates
        self._subscribe_device_state_events()

    @property
    def audio_sync(self) -> bool:
        """Return the cached audio sync of the device."""
        return self._attr_audio_sync

    @property
    def executor(self) -> CommandExecutor:
        """Return the command executor of the device."""
        return self._device.executor

    @property
    def source_list(self) -> dict[str, str]:
        """Return the cached input labels of the device."""
        return self._attr_input_labels

    @property
    def sound_modes(self) -> dict[str, str]:
        """Return the cached sound modes of the device."""
        return self._attr_sound_modes

    @property
    def muted(self) -> bool:
        """Return the cached mute state of the device."""
        return self._attr_muted

    @property
    def srate(self) -> str:
        """Return the cached srate of the device."""
        return self._attr_srate

    @property
    def state(self) -> MediaStates:
        """Return the cached state of the device."""
        return self._attr_state

    @property
    def volume(self) -> int:
        """Return the cached volume of the device."""
        if self._attr_volume is None:
            return 0
        return self._attr_volume

    @property
    def volume_percent(self) -> int:
        """Return current volume as percentage (0 to 100%) based on dB scale (-50 to 0.0 dB)."""
        min_db = -100.0
        max_db = 0.0
        raw_db = self._attr_volume

        percent = ((raw_db - min_db) / (max_db - min_db)) * 100
        return int(max(0.0, min(100.0, percent)))

    def percent_to_db(self, percent: int) -> float:
        """
        Convert a volume percentage (0 to 100) to a dB value (-100.0 to 0.0 dB).

        Args:
            percent (int): Volume percentage (0 to 100).

        Returns:
            float: The corresponding volume in dB.
        """
        min_db = -100.0
        max_db = 0.0
        percent = max(0, min(100, percent))  # Clamp to 0100
        db = ((percent / 100) * (max_db - min_db)) + min_db
        return db

    @property
    def attributes(self) -> dict[str, any]:
        """Return device attributes dictionary."""
        updated_data = {
            MediaAttr.MUTED: self.muted,
            MediaAttr.STATE: self.state,
            MediaAttr.VOLUME: self.volume,
            MediaAttr.SOURCE_LIST: self.source_list,
            MediaAttr.SOUND_MODE_LIST: self.sound_modes
        }

        return updated_data

    async def connect(self):
        """Establish and maintain a connection to the Trinnov device."""
        _LOG.debug("Connecting to Trinnov Processor")

        if self._device.context.connection.handler is not None:
            _LOG.debug("Already connected to Trinnov at %s:%d, disconnecting first", self.ip, self.port)
            await self.disconnect()

        self._connected = False

        try:
            await asyncio.wait_for(self._device.open(host=self.ip, port=self.port), timeout=3.0)
            return
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            if isinstance(e, OSError) and e.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
                _LOG.warning("Network unreachable check local connectivity (Wi-Fi off?)")
            else:
                _LOG.warning("Immediate connect failed: %s. Falling back to wait routine.", e)

        # Retry loop always runs after failure
        async def wait_loop():
            while self._wait_task and not self._wait_task.done():
                ready = await self.wait_for_device_ready()
                if not ready:
                    _LOG.info("Aborting wait_loop due to cancellation or disconnect")
                    return

                try:
                    await self._device.open(host=self.ip, port=self.port)
                    return
                except OSError as e:
                    if e.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
                        _LOG.warning("Network unreachable  retrying in 10s...")
                    else:
                        _LOG.debug("Failed to connect to Trinnov: %s", e)
                    await asyncio.sleep(10)

        self._wait_task = asyncio.create_task(wait_loop())
        await self._wait_task

    async def disconnect(self):
        """Close the connection cleanly, if not already disconnecting."""
        if self._connected or self._wait_task:
            _LOG.info("Disconnecting from Trinnov at %s:%d", self.ip, self.port)

            # Cancel the wait loop task if running
            if self._wait_task and not self._wait_task.done():
                _LOG.info("Cancelling device wait loop task...")
                self._wait_task.cancel()
                try:
                    await self._wait_task
                except asyncio.CancelledError:
                    _LOG.debug("Wait loop task cancelled")
                self._wait_task = None

            await self._device.close()

    async def send_command(self, command: str, parms = "") -> StatusCodes:
        """Send a named command to the device executor."""
        if not self._connected:
            _LOG.error("Connection not established.")
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        return await self._execute_command(command, parms)

    async def _execute_command(
        self,
        command: str,
        parms: ParamType | ParamTuple | ParamDict | None = None
    ) -> ucapi.StatusCodes:
        """Dynamically invoke a command method from executor."""
        method = getattr(self._device.executor, command, None)
        if not callable(method):
            _LOG.warning("No executor method found for command: %s", command)
            return ucapi.StatusCodes.NOT_IMPLEMENTED

        _LOG.debug(method)
        try:
            sig = inspect.signature(method)
            # Handle methods with no parameters
            if len(sig.parameters) == 0:
                result = method()
            # Unpack parameters based on type
            elif isinstance(parms, (list, tuple)):
                result = method(*parms)
            elif isinstance(parms, dict):
                result = method(**parms)
            else:
                result = method(parms)

            # Await result if it's a coroutine
            if asyncio.iscoroutine(result):
                await result

            return ucapi.StatusCodes.OK
        except ValueError as err:
            _LOG.exception("Error executing command %s: %s", command, str(err))
            return ucapi.StatusCodes.BAD_REQUEST

    async def power_on(self) -> StatusCodes:
        """Power on the Trinnov device if it is off."""

        if self.state == MediaStates.OFF:
            await PowerControl.power_on(self.mac)
        else:
            _LOG.debug("Power on skipped: Device is already %s.", self.state.value)
        return StatusCodes.OK

    async def power_off(self) -> StatusCodes:
        """Power off the Trinnov device if it is currently active."""
        if self.state == MediaStates.ON:
            await self.executor.power_off()
        else:
            _LOG.debug("Power off skipped: Device is already %s.", self.state.value)
        return StatusCodes.OK

    async def select_source(self, source: str) -> StatusCodes:
        """
        Select a video input source on the Trinnov device.

        :param source: Source input as a string, e.g., "HDMI1".
        :return: Status code
        """

        if not source:
            return StatusCodes.BAD_REQUEST
        _LOG.debug("Set input: %s", source)

        await self.executor.select_source(source)
        _LOG.info("Sent source select command for input %02d", source)
        return StatusCodes.OK

    def _subscribe_device_state_events(self):
        """Subscribe to device state updates from the dispatcher."""

        attr_handlers = {
            "audiosync_status": self._handle_audio_sync,
            "input_connector": self._handle_source,
            "input_labels": self._handle_input_labels,
            "sound_mode_list": self._handle_sound_mode_list,
            "srate": self._handle_srate,
            "upmixer": self._handle_upmixer,
            "volume": self._handle_volume,
            "mute" : self._handle_mute,
        }

        async def _handle_state_change(attr_name: str, _, event_data: dict):
            value = event_data.get("value")
            _LOG.debug("State changed: %s = %s", attr_name, value)

            handler = attr_handlers.get(attr_name)
            if handler:
                await handler(value)

        for attr_name in attr_handlers:
            self._device.dispatcher.register_listener(
                attr_name,
                lambda et,
                ed,
                attr=attr_name: asyncio.create_task(_handle_state_change(attr, et, ed))
            )

        self._device.dispatcher.register_listener(
            EventType.CONNECTION_STATE, self._handle_connection_state
        )

    async def _handle_connection_state(self, _, event_data: dict) -> None:
        """Handle connection state change events from the device."""
        state = event_data.get("state")

        if state == ConnectionStatus.DISCONNECTED:
            _LOG.debug("Connection state: DISCONNECTED")
            self._connected = False
            self._attr_state = MediaStates.OFF

            try:
                self.events.emit(Events.DISCONNECTED.name, self.device_id)
            except Exception as exc:
                _LOG.exception("Unhandled exception during DISCONNECTED event: %s", exc)

            updates = {
                EntityPrefix.MEDIA_PLAYER: (MediaAttr.STATE, MediaStates.OFF),
                EntityPrefix.REMOTE: (RemoteAttr.STATE, RemoteStates.OFF),
                EntityPrefix.SAMPLE_RATE: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
                EntityPrefix.AUDIO_SYNC: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
                EntityPrefix.VOLUME: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
                EntityPrefix.MUTED: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
            }
            for prefix, (attr, value) in updates.items():
                await self._emit_update(prefix.value, attr, value)

            # Only reconnect if the disconnect was NOT intentional
            if self._was_intentional_disconnect:
                _LOG.info("Intentional disconnect - not reconnecting")
            else:
                _LOG.warning("Unexpected disconnect - device may have been powered down")
                await asyncio.sleep(20)
                _LOG.info("Triggering reconnect after unexpected disconnect")
                asyncio.create_task(self.connect())

        elif state == ConnectionStatus.CONNECTED:
            _LOG.info("Connected to Trinnov at %s:%d", self.ip, self.port)
            self._connected = True
            self._attr_state = MediaStates.ON

            self._was_intentional_disconnect = False

            self.events.emit(Events.CONNECTED.name, self.device_id)
            updates = {
                EntityPrefix.MEDIA_PLAYER: (MediaAttr.STATE, MediaStates.ON),
                EntityPrefix.REMOTE: (RemoteAttr.STATE, RemoteStates.ON),
                EntityPrefix.SAMPLE_RATE: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.AUDIO_SYNC: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.VOLUME: (SensorAttr.STATE, SensorStates.ON),
            }
            for prefix, (attr, value) in updates.items():
                await self._emit_update(prefix.value, attr, value)

        else:
            _LOG.warning("Unknown connection state: %s", state)
            return

    async def _handle_audio_sync(self, audio_sync: str) -> None:
        if audio_sync != self._attr_audio_sync:
            self._attr_audio_sync = audio_sync
            await self._emit_update(EntityPrefix.AUDIO_SYNC.value, SensorAttr.VALUE, audio_sync)

    async def _handle_input_labels(self, labels: dict) -> None:
        values = list(labels.values())
        self._attr_input_labels = labels
        await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOURCE_LIST, values)

    async def _handle_sound_mode_list(self, sound_modes: str) -> None:
        await self._emit_update(
            EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOUND_MODE_LIST, sound_modes)

    async def _handle_source(self, source: str) -> None:
        await self._emit_update(
            EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOURCE, source)

    async def _handle_srate(self, value: str) -> None:
        srate = f"{float(value)/1000}"
        if srate != self._attr_srate:
            self._attr_srate = srate
            await self._emit_update(EntityPrefix.SAMPLE_RATE.value, SensorAttr.VALUE, srate)

    async def _handle_upmixer(self, upmixer: str) -> None:
        await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOUND_MODE, upmixer)

    async def _handle_volume(self, volume: float) -> None:
        if volume != self._attr_volume:
            self._attr_volume = volume
            await self._emit_update(EntityPrefix.VOLUME.value, SensorAttr.VALUE, volume)
            await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.VOLUME, self.volume_percent)

    async def _handle_mute(self, muted: bool) -> None:
        if muted != self._attr_muted:
            self._attr_muted = muted
            await self._emit_update(EntityPrefix.MUTED.value, SensorAttr.VALUE, muted)
            await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.MUTED, self.muted)

    async def _emit_update(self, prefix: str, attr: str, value: Any) -> None:
        entity_id = f"{prefix}.{self.device_id}"
        self.events.emit(Events.UPDATE.name, entity_id, {attr: value})

    async def wait_for_device_ready(self) -> bool:
        """Wait for Device Ready."""
        loop_cnt: int = 0
        first_attempt = True

        try:
            while True:
                loop_cnt += 1
                if await self._check_tcp_port(80):
                    _LOG.info("After %i tries, Port 80 is open, waiting 2 seconds...", loop_cnt)
                    await asyncio.sleep(2)
                    break
                if loop_cnt == 1:
                    _LOG.debug("Port 80 still closed, retrying every 10 seconds...")
                await asyncio.sleep(10)

            while True:
                if await self._check_tcp_port(44100):
                    _LOG.info("Port 44100 is open, checking WebSocket...")
                    if await self.get_ws_ethernet():
                        _LOG.info("Device is fully ready")
                        return True
                    _LOG.debug("WebSocket/Ethernet check failed, retrying in 2 seconds...")
                else:
                    if first_attempt:
                        _LOG.debug("Port 44100 still closed, waiting 18 seconds...")
                        await asyncio.sleep(18)
                        first_attempt = False
                    else:
                        _LOG.debug("Retrying port 44100 in 2 seconds...")
                        await asyncio.sleep(2)
        except asyncio.CancelledError:
            _LOG.info("wait_for_device_ready() cancelled")
            return False

    async def _check_tcp_port(self, port: int, timeout: float = 2.0) -> bool:
        """Check if a TCP port is open."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.exceptions.TimeoutError, ConnectionRefusedError, OSError):
            return False

    async def get_ws_ethernet(self) -> bool:
        """Send WebSocket messages and process responses."""

        try:
            async with WebSocketClient(self.ip) as client:
                responses = await client.send_and_receive(
                    [
                        (WS_ETHERNET, 1, None),
                    ]
                )

                ethernet_status = EthernetStatus.model_validate(responses[WS_ETHERNET])
                _LOG.debug("Ethernet MAC Address: %s", ethernet_status.macaddr)
                return True

        except DeviceError:
            return False
