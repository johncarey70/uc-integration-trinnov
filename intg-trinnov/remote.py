"""
Remote entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from config import TrinnovEntity
from const import EntityPrefix, RemoteDef
from const import SimpleCommands as cmds
from device import TrinnovDevice, TrinnovInfo
from ucapi import Remote, StatusCodes, remote
from ucapi.media_player import Attributes as MediaAttributes
from ucapi.media_player import States as MediaStates
from ucapi.remote import Attributes, Commands, States
from ucapi.ui import (Buttons, DeviceButtonMapping, Size, UiPage,
                      create_btn_mapping, create_ui_text)
from utils import parse_toggle_command

_LOG = logging.getLogger(__name__)

REMOTE_STATE_MAPPING = {
    MediaStates.OFF: States.OFF,
    MediaStates.ON: States.ON,
    MediaStates.STANDBY: States.OFF,
    MediaStates.UNAVAILABLE: States.UNAVAILABLE,
    MediaStates.UNKNOWN: States.UNKNOWN,
}

def _is_valid_simple_command(value: str) -> bool:
    """Return True if value matches a SimpleCommands enum value."""
    return value in {member.value for member in cmds}

class TrinnovRemote(Remote, TrinnovEntity):
    """Representation of a Trinnov Remote entity."""

    def __init__(self, info: TrinnovInfo, device: TrinnovDevice):
        """Initialize the class."""
        self._device = device
        self.device_id = info.id

        entity_id = f"{EntityPrefix.REMOTE.value}.{info.id}"
        features = RemoteDef.features
        attributes = RemoteDef.attributes

        super().__init__(
            entity_id,
            f"{info.name} Remote",
            features,
            attributes,
            simple_commands=RemoteDef.simple_commands,
            button_mapping=self.create_button_mappings(),
            ui_pages=self.create_ui(),
        )

    def create_button_mappings(self) -> list[DeviceButtonMapping | dict[str, Any]]:
        """Create button mappings."""
        return [
            create_btn_mapping(Buttons.MUTE, cmds.MUTE_TOGGLE),
            create_btn_mapping(Buttons.VOLUME_DOWN, cmds.VOLUME_DOWN, long=None),
            create_btn_mapping(Buttons.VOLUME_UP, cmds.VOLUME_UP, long=None),
        ]

    def create_ui(self) -> list[UiPage | dict[str, Any]]:
        """Create a user interface with different pages that includes all commands"""

        vol60 = remote.create_sequence_cmd([cmds.VOLUME, "60"])
        vol70 = remote.create_sequence_cmd([cmds.VOLUME, "70"])

        ui_page1 = UiPage("page1", "Power", grid=Size(6, 6))
        ui_page1.add(create_ui_text("Power On", 0, 0, size=Size(3, 1), cmd=Commands.ON))
        ui_page1.add(create_ui_text("Power Off", 3, 0, size=Size(3, 1), cmd=Commands.OFF))
        ui_page1.add(create_ui_text("Set Volume 60%", 0, 1, size=Size(6, 1), cmd=vol60))
        ui_page1.add(create_ui_text("Set Volume 70%", 0, 2, size=Size(6, 1), cmd=vol70))
        ui_page1.add(create_ui_text("--- Toggle Commands ---", 0, 3, size=Size(6, 1)))
        ui_page1.add(create_ui_text("Dim", 0, 4, size=Size(3, 1), cmd=send_cmd(cmds.DIM_TOGGLE)))
        ui_page1.add(create_ui_text("Bypass", 3, 4, size=Size(3, 1), cmd=send_cmd(cmds.BYPASS_TOGGLE)))
        ui_page1.add(create_ui_text("Front Panel Light", 0, 5, size=Size(6, 1), cmd=send_cmd(cmds.FAV_LIGHT)))

        snd1 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "auto"])
        snd2 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "dolby"])
        snd3 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "dts"])
        snd4 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "auro3d"])
        snd5 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "native"])
        snd6 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "upmix_on_native"])
        snd7 = remote.create_sequence_cmd([cmds.SELECT_SOUND_MODE, "legacy"])

        ui_page2 = UiPage("page2", "Sound Modes", grid=Size(6, 6))
        ui_page2.add(create_ui_text("--- Select Sound Mode ---", 0, 0, size=Size(6, 1)))
        ui_page2.add(create_ui_text("Auto", 0, 1, size=Size(6, 1), cmd=snd1))
        ui_page2.add(create_ui_text("Auro-3D", 0, 2, size=Size(3, 1), cmd=snd4))
        ui_page2.add(create_ui_text("Dolby Surround", 0, 3, size=Size(3, 1), cmd=snd2))
        ui_page2.add(create_ui_text("Legacy", 0, 4, size=Size(3, 1), cmd=snd7))
        ui_page2.add(create_ui_text("Native", 3, 2, size=Size(3, 1), cmd=snd5))
        ui_page2.add(create_ui_text("Neural:X", 3, 3, size=Size(3, 1), cmd=snd3))
        ui_page2.add(create_ui_text("Upmix on Native", 3, 4, size=Size(3, 1), cmd=snd6))

        return [ui_page1, ui_page2]

    async def command(
        self,
        cmd_id: str,
        params: dict[str, Any] | None = None,
        *,
        websocket: Any,
    ) -> StatusCodes:
        """Handle command requests for the remote entity."""
        params = params or {}

        simple_cmd: str | None = params.get("command")
        if simple_cmd and simple_cmd.startswith("remote"):
            cmd_id = simple_cmd.split(".")[1]

        _LOG.info(
            "Received Remote command request: %s with parameters: %s",
            cmd_id,
            params or "no parameters",
        )

        try:
            cmd = Commands(cmd_id)
            _LOG.debug("Resolved command: %s", cmd)
        except ValueError:
            return StatusCodes.NOT_IMPLEMENTED

        match cmd:
            case Commands.ON:
                return await self._device.power_on()

            case Commands.OFF:
                return await self._device.power_off()

            case Commands.SEND_CMD:
                return await self._handle_send_cmd(simple_cmd)

            case Commands.SEND_CMD_SEQUENCE:
                return await self._handle_send_cmd_sequence(params)

            case _:
                return StatusCodes.NOT_IMPLEMENTED

    async def _handle_send_cmd(
        self,
        simple_cmd: str | None,
    ) -> StatusCodes:
        """Handle SEND_CMD requests."""
        if not simple_cmd:
            _LOG.warning("Missing command in SEND_CMD")
            return StatusCodes.BAD_REQUEST

        normalized = simple_cmd.replace(" ", "_").lower()

        if not _is_valid_simple_command(normalized):
            _LOG.warning("Unknown command: %s", normalized)
            return StatusCodes.NOT_IMPLEMENTED

        result = (
            parse_toggle_command("mute", normalized)
            or parse_toggle_command("dim", normalized)
            or parse_toggle_command("bypass", normalized)
        )

        actual_cmd: str | None
        cmd_params: Any | None

        if result:
            actual_cmd, cmd_params = result
        else:
            actual_cmd, cmd_params = normalized, None

        _LOG.debug("Resolved simple command: %s params=%s", actual_cmd, cmd_params)
        return await self._device.send_command(actual_cmd, cmd_params)

    async def _handle_send_cmd_sequence(
        self,
        params: dict[str, Any],
    ) -> StatusCodes:
        """Handle SEND_CMD_SEQUENCE requests."""
        commands = params.get("sequence", [])

        if not commands:
            return StatusCodes.BAD_REQUEST

        if commands[0] == "volume":
            try:
                volume_percent = int(commands[1])
            except (IndexError, ValueError, TypeError):
                return StatusCodes.BAD_REQUEST

            return await self._device.send_command(
                "volume",
                self._device.percent_to_db(volume_percent),
            )

        if commands[0] == "select_sound_mode":
            try:
                mode_key = str(commands[1])
            except (IndexError, TypeError):
                return StatusCodes.BAD_REQUEST

            return await self._device.select_sound_mode(mode_key)

        return StatusCodes.NOT_IMPLEMENTED

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given media-player attributes and return remote attributes with converted state.

        :param update: dictionary with MediaAttributes.
        :return: dictionary with changed remote.Attributes only.
        """
        attributes = {}

        if MediaAttributes.STATE in update:
            media_state = update[MediaAttributes.STATE]

            try:
                media_state_enum = MediaStates(media_state)
            except ValueError:
                _LOG.warning("Unknown media_state value: %s", media_state)
                media_state_enum = MediaStates.UNKNOWN

            new_state: States = REMOTE_STATE_MAPPING.get(media_state_enum, States.UNKNOWN)
            current_state = self.attributes.get(Attributes.STATE)

            if current_state != new_state:
                attributes[Attributes.STATE] = new_state

        _LOG.debug("Trinnov Remote update attributes %s -> %s", update, attributes)
        return attributes

def send_cmd(command: cmds):
    """
    Wraps a SimpleCommand enum into a UI-compatible send command payload.

    :param command: A SimpleCommands enum member (e.g. SimpleCommands.UP).
    :return: A dictionary payload compatible with remote.create_send_cmd().
    """
    return remote.create_send_cmd(command.name)
