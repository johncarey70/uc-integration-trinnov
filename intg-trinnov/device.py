"""Provides connection utilities for communicating with a Trinnov device."""

# pylint: disable=too-many-arguments
# pylint: disable=too-many-positional-arguments
# pylint: disable=too-many-instance-attributes

import asyncio
import inspect
import logging
from asyncio import AbstractEventLoop
from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Any

import ucapi
from const import EntityPrefix
from pyee.asyncio import AsyncIOEventEmitter
from pytrinnov.models.constants import ConnectionStatus, EventType
from pytrinnov.trinnov.config import DEFAULT_PROTOCOL_PORT
from pytrinnov.trinnov.device import DeviceManager
from pytrinnov.trinnov.executor import CommandExecutor, PowerControl
from ucapi import StatusCodes
from ucapi.media_player import Attributes as MediaAttr
from ucapi.media_player import States as MediaStates
from ucapi.remote import Attributes as RemoteAttr
from ucapi.remote import States as RemoteStates
from ucapi.select import Attributes as SelectAttr
from ucapi.select import States as SelectStates
from ucapi.sensor import Attributes as SensorAttr
from ucapi.sensor import States as SensorStates

ParamType = str | int | float | bool
ParamTuple = tuple[ParamType, ...]
ParamDict = dict[str, ParamType]

_LOG = logging.getLogger(__name__)

class Events(IntEnum):
    """Internal driver events."""

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
        """Return a concise, human-readable representation of this Trinnov device."""
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
        """Initialize the Trinnov device wrapper and subscribe to state events."""

        # Identifiers
        self.device_id = device_id or "unknown"
        self.ip = ip
        self.port = DEFAULT_PROTOCOL_PORT
        self.mac = mac

        # Internal device manager
        self._device = DeviceManager()
        self._event_loop = loop or asyncio.get_running_loop()

        # Connection state
        self._connected: bool = False
        self._was_intentional_disconnect: bool = False
        self._wait_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay_task: asyncio.Task | None = None

        # Cached attributes
        self._attr_audio_sync: bool = False
        self._attr_codec: str = "-"
        self._attr_listening_format_key: str | None = None
        self._attr_listening_format_label: str = "-"
        self._attr_muted: bool | None = None
        self._attr_preset_index: int | None = None
        self._attr_preset_options: list[str] = []
        self._attr_remapping_mode: str = ""
        self._attr_source_index: int | None = None
        self._attr_source_options: list[str] = []
        self._attr_srate: int = 0
        self._attr_state: MediaStates = MediaStates.OFF
        self._attr_volume: float = -100.0

        # Event system
        self.events = AsyncIOEventEmitter(loop=self._event_loop)

        # Subscribe to device state updates
        self._subscribe_device_state_events()

        self._volume_seen: bool = False
        self._srate_seen: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def audio_sync(self) -> bool:
        """Return the cached audio sync status."""
        return self._attr_audio_sync

    @property
    def executor(self) -> CommandExecutor:
        """Return the pytrinnov command executor."""
        return self._device.executor

    @property
    def preset_index(self) -> int | None:
        """Return the cached current preset index."""
        return self._attr_preset_index

    @property
    def preset_list(self) -> dict[int, str]:
        """Return the cached preset list mapping."""
        return self._device.audio_settings.preset_list or {}

    @property
    def source_list(self) -> dict[int, str]:
        """Return the cached source list mapping."""
        return self._device.audio_settings.source_list or {}

    @property
    def listening_formats(self) -> dict[str, str]:
        """Return key->label listening formats."""
        return self._device.audio_settings.listening_formats or {}

    @property
    def muted(self) -> bool:
        """Return the cached mute state."""
        return bool(self._attr_muted)

    @property
    def srate(self) -> int:
        """Return the cached sample rate in kHz."""
        return self._attr_srate

    @property
    def codec(self) -> str:
        """Return the cached decoder codec label."""
        return self._attr_codec

    @property
    def listening_format_key(self) -> str | None:
        """Current upmixer key (e.g. 'dolby')."""
        return self._attr_listening_format_key

    @property
    def listening_format_label(self) -> str:
        """Current upmixer label (e.g. 'Dolby Surround')."""
        return self._attr_listening_format_label

    @property
    def remapping_mode(self) -> str:
        """Return the cached remapping mode."""
        return self._attr_remapping_mode

    @property
    def state(self) -> MediaStates:
        """Return the cached media-player state."""
        return self._attr_state

    @property
    def volume(self) -> float:
        """Return the cached volume in dB."""
        return self._attr_volume

    @property
    def volume_percent(self) -> int:
        """Return current volume as percentage (0 to 100%) based on dB scale."""
        min_db = -100.0
        max_db = 0.0
        raw_db = float(self._attr_volume)

        percent = ((raw_db - min_db) / (max_db - min_db)) * 100
        return int(max(0.0, min(100.0, percent)))

    @staticmethod
    def percent_to_db(percent: float) -> float:
        """Convert 0-100% volume into dB using the configured range."""
        min_db = -100.0
        max_db = 0.0
        percent = max(0.0, min(100.0, percent))  # Clamp to 0-100
        return min_db + (percent / 100) * (max_db - min_db)

    @property
    def attributes(self) -> dict[str, Any]:
        """Return device attributes dictionary."""
        updated_data = {
            MediaAttr.MUTED: self.muted,
            MediaAttr.STATE: self.state,
            MediaAttr.VOLUME: self.volume_percent,
            MediaAttr.SOURCE_LIST: list(self.source_list.values()),
            MediaAttr.SOUND_MODE_LIST: list(self.listening_formats.values())
        }

        return updated_data

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------

    async def connect(self):
        """Establish a connection to the Trinnov device, retrying until it becomes reachable."""
        if self._device.context.connection.handler is not None:
            await self.disconnect()

        self._was_intentional_disconnect = False

        try:
            await asyncio.wait_for(
                self._device.open(host=self.ip, port=self.port),
                timeout=3.0,
            )
            return
        except (asyncio.TimeoutError, OSError, ConnectionError):
            pass

        async def wait_loop():
            while True:
                ready = await self.wait_for_device_ready()
                if not ready:
                    return
                try:
                    await self._device.open(host=self.ip, port=self.port)
                    return
                except OSError:
                    await asyncio.sleep(10)

        self._wait_task = self._event_loop.create_task(wait_loop())
        await self._wait_task

    async def disconnect(self):
        """Close the active device connection and cancel any pending reconnect tasks."""
        self._was_intentional_disconnect = True

        if self._wait_task and not self._wait_task.done():
            self._wait_task.cancel()
            try:
                await self._wait_task
            except asyncio.CancelledError:
                pass
            self._wait_task = None

        if self._reconnect_delay_task and not self._reconnect_delay_task.done():
            self._reconnect_delay_task.cancel()
            try:
                await self._reconnect_delay_task
            except asyncio.CancelledError:
                pass
            self._reconnect_delay_task = None

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        if self._device.context.connection.handler is not None:
            await self._device.close()

        self._connected = False

    # ------------------------------------------------------------------
    # Command Execution
    # ------------------------------------------------------------------

    async def send_command(self, command: str, parms: ParamType | ParamTuple | ParamDict | None = None,) -> StatusCodes:
        """Send a named command to the Trinnov executor if the device is connected."""
        if not self._connected:
            _LOG.error("Connection not established.")
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        return await self._execute_command(command, parms)

    async def _execute_command(
        self,
        command: str,
        parms: ParamType | ParamTuple | ParamDict | None = None
    ) -> ucapi.StatusCodes:
        """Resolve and invoke a command method on the executor with dynamic arguments."""

        method = getattr(self._device.executor, command, None)
        if not callable(method):
            _LOG.warning("No executor method found for command: %s", command)
            return ucapi.StatusCodes.NOT_IMPLEMENTED

        try:
            sig = inspect.signature(method)

            if len(sig.parameters) == 0:
                result = method()

            elif isinstance(parms, (list, tuple)):
                result = method(*parms)
            elif isinstance(parms, dict):
                result = method(**parms)
            else:
                if parms is None:
                    result = method()
                else:
                    result = method(parms)

            if asyncio.iscoroutine(result):
                await result

            return ucapi.StatusCodes.OK
        except ValueError as err:
            _LOG.exception("Error executing command %s: %s", command, str(err))
            return ucapi.StatusCodes.BAD_REQUEST
        except Exception as err:
            _LOG.exception("Error executing command %s: %s", command, err)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

    async def power_on(self) -> StatusCodes:
        """Wake the Trinnov device using Wake-on-LAN if it is currently powered off."""

        if self.state == MediaStates.OFF:
            await PowerControl.power_on(self.mac)
        else:
            _LOG.debug("Power on skipped: Device is already %s.", self.state.value)
        return StatusCodes.OK

    async def power_off(self) -> StatusCodes:
        """Send the power-off command to the Trinnov device if it is currently on."""
        if self.state == MediaStates.ON:
            await self.executor.power_off()
        else:
            _LOG.debug("Power off skipped: Device is already %s.", self.state.value)
        return StatusCodes.OK

    async def select_preset(self, preset: int) -> StatusCodes:
        """Load a preset by index on the Trinnov device."""

        if preset is None:
            return StatusCodes.BAD_REQUEST
        _LOG.debug("Load preset : %i", preset)

        await self.executor.load_preset(preset)
        _LOG.info("Sent loadp command for %i", preset)
        return StatusCodes.OK

    async def select_remapping_mode(self, mode: str) -> StatusCodes:
        """Set the requested remapping mode and trigger a refresh of the effective mode."""

        if not mode:
            return StatusCodes.BAD_REQUEST

        mapping = {
            "Disabled": "none",
            "2D": "2D",
            "3D": "3D",
        }

        raw = mapping.get(mode)
        if raw is None:
            _LOG.warning("Invalid remapping mode %r (available=%s)", mode, list(mapping.keys()))
            return StatusCodes.BAD_REQUEST

        _LOG.debug("Set remapping mode to: %s", raw)
        await self.executor.select_remapping_mode(raw)
        self._event_loop.create_task(self.executor.select_remapping_mode())

        _LOG.info("Sent remapping mode select command for upmixer %s", raw)
        return StatusCodes.OK

    async def select_source(self, source: str) -> StatusCodes:
        """Switch the Trinnov device to the specified input source."""

        if not source:
            return StatusCodes.BAD_REQUEST
        _LOG.debug("Set input: %s", source)

        await self.executor.select_source(source)
        _LOG.info("Sent source select command for input %s", source)
        return StatusCodes.OK

    async def select_sound_mode(self, upmixer: str) -> StatusCodes:
        """Set the requested upmixer mode and trigger a refresh of the effective mode."""

        if not upmixer:
            return StatusCodes.BAD_REQUEST

        _LOG.debug("Set upmixer to: %s", upmixer)
        await self.executor.select_sound_mode(upmixer)
        self._event_loop.create_task(self.executor.upmixer())

        _LOG.info("Sent sound mode select command for upmixer %s", upmixer)
        return StatusCodes.OK

    def _subscribe_device_state_events(self) -> None:
        """Register handlers for state-change events emitted by the pytrinnov dispatcher."""

        attr_handlers = {
            "audiosync_status": self._handle_audio_sync_status,
            "current_preset": self._handle_preset,
            "current_source": self._handle_source,
            "display_volume": self._handle_volume,
            "decoder_decoder": self._handle_codec,
            "decoder_upmixer": self._handle_decoder_upmixer,
            "mute": self._handle_mute,
            "preset_list": self._handle_preset_list,
            "listening_format": self._handle_listening_format,
            "listening_formats": self._handle_listening_formats,
            "remapping_mode": self._handle_remapping_mode,
            "source_list": self._handle_source_list,
            "srate": self._handle_srate,
            "volume": self._handle_volume,
        }

        async def _handle_state_change(attr_name: str, _event_type, event_data: dict) -> None:
            """Dispatch a single attribute update from the pytrinnov event bus to its handler."""
            value = event_data.get("value")
            _LOG.debug("State changed: %s = %s", attr_name, value)

            handler = attr_handlers.get(attr_name)
            if handler:
                await handler(value)

        def _make_listener(attr_name: str):
            """Create a dispatcher callback that forwards events into the asyncio loop."""

            def _listener(event_type, event_data: dict) -> None:
                coro = _handle_state_change(attr_name, event_type, event_data)

                try:
                    loop = asyncio.get_running_loop()
                    if loop is self._event_loop:
                        asyncio.create_task(coro)
                        return
                except RuntimeError:
                    pass

                self._event_loop.create_task(coro)

            return _listener

        for attr_name in attr_handlers:
            self._device.dispatcher.register_listener(attr_name, _make_listener(attr_name))

        self._device.dispatcher.register_listener(
            EventType.CONNECTION_STATE, self._handle_connection_state
        )

    # ------------------------------------------------------------------
    # Attribute Handlers
    # ------------------------------------------------------------------

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
                EntityPrefix.MUTE: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
                EntityPrefix.PRESETS: (SelectAttr.STATE, SelectStates.UNAVAILABLE),
                EntityPrefix.REMAPPING_MODE: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
                EntityPrefix.REMAPPING_MODE_SELECT: (SelectAttr.STATE, SelectStates.UNAVAILABLE),
                EntityPrefix.SOURCES: (SelectAttr.STATE, SelectStates.UNAVAILABLE),
                EntityPrefix.CODEC: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
                EntityPrefix.LISTENING_FORMAT: (SelectAttr.STATE, SelectStates.UNAVAILABLE),
                EntityPrefix.UPMIXER: (SensorAttr.STATE, SensorStates.UNAVAILABLE),
            }
            for prefix, (attr, value) in updates.items():
                await self._emit_update(prefix.value, attr, value)

            self._volume_seen = False
            self._srate_seen = False

            # Only reconnect if the disconnect was NOT intentional
            if self._was_intentional_disconnect:
                _LOG.info("Intentional disconnect - not reconnecting")
            else:
                _LOG.warning("Unexpected disconnect - device may have been powered down")
                self._schedule_reconnect(20.0)

        elif state == ConnectionStatus.CONNECTED:
            _LOG.info("Connected to Trinnov at %s:%d", self.ip, self.port)

            if self._reconnect_delay_task and not self._reconnect_delay_task.done():
                self._reconnect_delay_task.cancel()
                self._reconnect_delay_task = None

            self._connected = True
            self._attr_state = MediaStates.ON

            self._was_intentional_disconnect = False

            self.events.emit(Events.CONNECTED.name, self.device_id)
            updates = {
                EntityPrefix.MEDIA_PLAYER: (MediaAttr.STATE, MediaStates.ON),
                EntityPrefix.REMOTE: (RemoteAttr.STATE, RemoteStates.ON),
                EntityPrefix.AUDIO_SYNC: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.CODEC: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.MUTE: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.PRESETS: (SelectAttr.STATE, SelectStates.ON),
                EntityPrefix.REMAPPING_MODE: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.SOURCES: (SelectAttr.STATE, SelectStates.ON),
                EntityPrefix.LISTENING_FORMAT: (SelectAttr.STATE, SelectStates.ON),
                EntityPrefix.UPMIXER: (SensorAttr.STATE, SensorStates.ON),
                EntityPrefix.REMAPPING_MODE_SELECT: (SelectAttr.STATE, SelectStates.ON),
            }
            for prefix, (attr, value) in updates.items():
                await self._emit_update(prefix.value, attr, value)

        else:
            _LOG.warning("Unknown connection state: %s", state)
            return

    async def _handle_audio_sync_status(self, status: bool) -> None:
        """Update the audio-sync sensor when the device reports sync status changes."""
        if status != self._attr_audio_sync:
            self._attr_audio_sync = status
            await self._emit_update(
                EntityPrefix.AUDIO_SYNC.value,
                SensorAttr.VALUE,
                "Synced" if status else "Not synced",
            )

    async def _handle_preset(self, index: int) -> None:
        """Update preset select options/current option from the current preset index."""
        self._attr_preset_index = index

        preset_map = self.preset_list or {}
        options = self._mapping_options(preset_map)

        if options:
            await self._emit_select_options_if_changed(
                EntityPrefix.PRESETS, options, "_attr_preset_options"
            )

        await self._emit_current_option_from_mapping(
            EntityPrefix.PRESETS,
            preset_map,
            index,
            warn_label="Preset index",
        )

    async def _handle_preset_list(self, presets: dict | None) -> None:
        """Update preset select options from the preset list and re-assert the current preset."""
        raw = presets or {}
        preset_map: dict[int, str] = {}

        for k, v in raw.items():
            try:
                preset_map[int(k)] = str(v)
            except (TypeError, ValueError):
                continue

        options = self._mapping_options(preset_map)
        await self._emit_select_options_if_changed(
            EntityPrefix.PRESETS, options, "_attr_preset_options"
        )

        if self._attr_preset_index is not None:
            await self._emit_current_option_from_mapping(
                EntityPrefix.PRESETS, preset_map, self._attr_preset_index
            )

    async def _handle_listening_formats(self, formats: dict[str, str] | None) -> None:
        """Update listening format options (key->label)."""
        mapping = formats or {}
        labels = list(mapping.values())

        await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOUND_MODE_LIST, labels)
        await self._emit_update(EntityPrefix.LISTENING_FORMAT.value, SelectAttr.OPTIONS, labels)

        if self._attr_listening_format_key:
            label = mapping.get(self._attr_listening_format_key)
            if label is not None:
                await self._emit_update(EntityPrefix.LISTENING_FORMAT.value, SelectAttr.CURRENT_OPTION, label)

    async def _handle_source_list(self, source_list: dict | None) -> None:
        """Update source options and re-assert current source after list refresh.

        Trinnov provides `audio_settings.source_list` as {index:int -> label:str}.
        We expose UC options as the list of labels, and keep the current option
        synced to the currently selected index (if known).
        """
        raw = source_list or {}
        source_map: dict[int, str] = {}

        for k, v in raw.items():
            try:
                source_map[int(k)] = str(v)
            except (TypeError, ValueError):
                continue

        labels = self._mapping_options(source_map)
        self._attr_source_options = labels

        await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOURCE_LIST, labels)
        await self._emit_update(EntityPrefix.SOURCES.value, SelectAttr.OPTIONS, labels)

        if self._attr_source_index is not None:
            label = source_map.get(self._attr_source_index)
            if label is not None:
                await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOURCE, label)
                await self._emit_update(EntityPrefix.SOURCES.value, SelectAttr.CURRENT_OPTION, label)

    async def _handle_source(self, source: int) -> None:
        """Update current source and keep both media_player and select in sync.

        `current_source` comes from Trinnov as an integer index into `source_list`.
        UC expects the media_player SOURCE to be the *label* (string).
        """
        self._attr_source_index = source
        mapping = self.source_list or {}

        options = self._mapping_options(mapping)

        # Determine if the options list changed before updating the cache
        changed = options != self._attr_source_options

        # Update select options (deduped)
        await self._emit_select_options_if_changed(
            EntityPrefix.SOURCES,
            options,
            "_attr_source_options",
        )

        # Only update media_player SOURCE_LIST when options change
        if changed:
            await self._emit_update(
                EntityPrefix.MEDIA_PLAYER.value,
                MediaAttr.SOURCE_LIST,
                options,
            )

        label = mapping.get(source)

        if label is not None:
            await self._emit_update(
                EntityPrefix.MEDIA_PLAYER.value,
                MediaAttr.SOURCE,
                str(label),
            )
            await self._emit_update(
                EntityPrefix.SOURCES.value,
                SelectAttr.CURRENT_OPTION,
                str(label),
            )
        elif mapping:
            await self._emit_update(
                EntityPrefix.MEDIA_PLAYER.value,
                MediaAttr.SOURCE,
                "",
            )
            await self._emit_update(
                EntityPrefix.SOURCES.value,
                SelectAttr.CURRENT_OPTION,
                "",
            )

    async def _handle_srate(self, srate_hz: int) -> None:
        """Update the sample-rate sensor from a device-reported Hz value."""

        srate_khz = int(srate_hz) // 1000  # 48000 -> 48

        if not self._srate_seen:
            await self._emit_update(EntityPrefix.SAMPLE_RATE.value, SensorAttr.STATE, SensorStates.ON)
            self._srate_seen = True

        if srate_khz == self._attr_srate:
            return

        self._attr_srate = srate_khz

        await self._emit_update(EntityPrefix.SAMPLE_RATE.value, SensorAttr.VALUE, srate_khz)

    async def _handle_decoder_upmixer(self, label: str) -> None:
        """Update the effective upmixer label reported by the decoder."""

        normalized = (label or "").strip()
        if normalized.lower() == "none":
            normalized = "-"

        if normalized == self._attr_listening_format_label:
            return

        self._attr_listening_format_label = normalized

        await self._emit_update(EntityPrefix.UPMIXER.value, SensorAttr.VALUE, normalized)
        await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOUND_MODE, normalized)

    async def _handle_listening_format(self, key: str) -> None:
        """Update the selected listening-format key and derived display label."""

        if key == self._attr_listening_format_key:
            return

        self._attr_listening_format_key = key

        label = (self.listening_formats or {}).get(key, key)

        await self._emit_update(
            EntityPrefix.LISTENING_FORMAT.value, SelectAttr.CURRENT_OPTION, label
        )

        await self._emit_update(
            EntityPrefix.MEDIA_PLAYER.value, MediaAttr.SOUND_MODE, label
        )

    async def _handle_codec(self, codec: str | None) -> None:
        """Normalize and update the decoder codec label for UI display."""

        normalized = (codec or "").strip()

        if normalized == "DD":
            normalized = "Dolby Digital"

        if not normalized or normalized.lower() == "none":
            normalized = "-"

        if normalized == self._attr_codec:
            return

        self._attr_codec = normalized

        await self._emit_update(
            EntityPrefix.CODEC.value,
            SensorAttr.VALUE,
            normalized,
        )

    async def _handle_remapping_mode(self, mode: str | None) -> None:
        """Normalize and update the remapping mode reported by the device."""

        normalized = (mode or "").strip()

        valid_modes = {"none", "2D", "3D", "autoroute", "manual"}
        if normalized not in valid_modes:
            _LOG.warning("Unknown remapping mode: %r", mode)
            return

        if normalized == self._attr_remapping_mode:
            return

        self._attr_remapping_mode = normalized

        display = "Disabled" if normalized == "none" else normalized

        await self._emit_update(
            EntityPrefix.REMAPPING_MODE.value,
            SensorAttr.VALUE,
            display,
        )

        await self._emit_update(
            EntityPrefix.REMAPPING_MODE_SELECT.value,
            SelectAttr.CURRENT_OPTION,
            display,
        )

    async def _handle_volume(self, volume: float) -> None:
        """Update volume sensor and media-player volume percent from a dB value."""

        if not self._volume_seen:
            await self._emit_update(EntityPrefix.VOLUME.value, SensorAttr.STATE, SensorStates.ON)
            self._volume_seen = True

        if volume == self._attr_volume:
            return

        self._attr_volume = volume

        await self._emit_update(EntityPrefix.VOLUME.value, SensorAttr.VALUE, volume)
        await self._emit_update(EntityPrefix.MEDIA_PLAYER.value, MediaAttr.VOLUME, self.volume_percent)

    async def _handle_mute(self, muted: bool) -> None:
        """Update mute sensor and media-player mute state from the device mute flag."""
        if muted != self._attr_muted:
            self._attr_muted = muted

            on_off = "on" if muted else "off"

            await self._emit_update(
                EntityPrefix.MUTE.value,
                SensorAttr.VALUE,
                on_off,
            )

            await self._emit_update(
                EntityPrefix.MEDIA_PLAYER.value,
                MediaAttr.MUTED,
                muted,
            )

    async def _emit_update(self, prefix: str, attr: str, value: Any) -> None:
        """Emit a UC entity attribute update through the driver event emitter."""
        entity_id = f"{prefix}.{self.device_id}"
        self.events.emit(Events.UPDATE.name, entity_id, {attr: value})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mapping_options(self, mapping: dict) -> list[str]:
        """Return a stable list of option labels from a mapping."""
        # Keep current behavior: preserve insertion order of mapping.values().
        return [str(v) for v in mapping.values()]

    async def _emit_select_options_if_changed(
        self,
        entity_prefix: EntityPrefix,
        options: list[str],
        cache_attr: str,
    ) -> None:
        """Emit SelectAttr.OPTIONS only when changed, updating the given cache attribute."""
        if options != getattr(self, cache_attr):
            setattr(self, cache_attr, options)
            await self._emit_update(entity_prefix.value, SelectAttr.OPTIONS, options)

    async def _emit_current_option_from_mapping(
        self,
        entity_prefix: EntityPrefix,
        mapping: dict,
        key,
        *,
        warn_label: str | None = None,
    ) -> None:
        """Emit SelectAttr.CURRENT_OPTION for a key->label mapping if key resolves."""
        label = mapping.get(key)
        if label is None:
            if warn_label and mapping:
                _LOG.warning("%s %s not found in keys=%s", warn_label, key, sorted(mapping.keys()))
            return
        await self._emit_update(entity_prefix.value, SelectAttr.CURRENT_OPTION, str(label))

    async def wait_for_device_ready(self) -> bool:
        """Poll device ports until the Trinnov is reachable and ready for control."""

        loop_cnt: int = 0
        first_attempt = True

        try:
            # Wait for HTTP port
            while True:
                loop_cnt += 1
                if await self._check_tcp_port(80):
                    _LOG.info("After %i tries, Port 80 is open, waiting 2 seconds...", loop_cnt)
                    await asyncio.sleep(2)
                    break

                if loop_cnt == 1:
                    _LOG.debug("Port 80 still closed, retrying every 10 seconds...")

                await asyncio.sleep(10)

            # Wait for control port
            loop_cnt = 0
            while True:
                loop_cnt += 1
                if await self._check_tcp_port(self.port):
                    _LOG.info("After %i tries, Port %i is open, device is fully ready", loop_cnt, self.port)
                    return True

                if first_attempt:
                    _LOG.debug("Port %i still closed, waiting 60 seconds...", self.port)
                    await asyncio.sleep(60)
                    first_attempt = False
                else:
                    _LOG.debug("Retrying port %i in 5 seconds...", self.port)
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            _LOG.info("wait_for_device_ready() cancelled")
            return False

    async def _check_tcp_port(self, port: int, timeout: float = 2.0) -> bool:
        """Return True if the specified TCP port on the device is reachable."""

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

    def _start_reconnect(self) -> None:
        """Start a reconnect attempt if one is not already running."""

        if self._reconnect_task and not self._reconnect_task.done():
            _LOG.debug("Reconnect already running; skipping.")
            return
        self._reconnect_task = self._event_loop.create_task(self.connect())

    def _schedule_reconnect(self, delay_s: float = 20.0) -> None:
        """Schedule a delayed reconnect attempt after an unexpected disconnect."""

        async def _delayed() -> None:
            await asyncio.sleep(delay_s)
            _LOG.info("Triggering reconnect after unexpected disconnect")
            self._start_reconnect()

        if self._reconnect_delay_task and not self._reconnect_delay_task.done():
            _LOG.debug("Reconnect delay already scheduled; skipping.")
            return

        self._reconnect_delay_task = self._event_loop.create_task(_delayed())
