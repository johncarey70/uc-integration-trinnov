"""
Remote entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

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

class TrinnovRemote(Remote):
    """Representation of a Trinnov Remote entity."""

    def __init__(self, info: TrinnovInfo, device: TrinnovDevice):
        """Initialize the class."""
        self._device = device
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
            ui_pages=self.create_ui()
        )

        _LOG.debug("TrinnovRemote init %s : %s", entity_id, attributes)

    def create_button_mappings(self) -> list[DeviceButtonMapping | dict[str, Any]]:
        """Create button mappings."""
        return [
            create_btn_mapping(Buttons.MUTE, cmds.MUTE_TOGGLE),
            create_btn_mapping(Buttons.VOLUME_DOWN, cmds.VOLUME_DOWN),
            create_btn_mapping(Buttons.VOLUME_UP, cmds.VOLUME_UP),
        ]

    def create_ui(self) -> list[UiPage | dict[str, Any]]:
        """Create a user interface with different pages that includes all commands"""

        vol60 = remote.create_sequence_cmd([cmds.VOLUME, "60"])

        ui_page1 = UiPage("page1", "Power", grid=Size(6, 6))
        ui_page1.add(create_ui_text("Power On", 0, 0, size=Size(3, 1), cmd=Commands.ON))
        ui_page1.add(create_ui_text("Power Off", 3, 0, size=Size(3, 1), cmd=Commands.OFF))
        ui_page1.add(create_ui_text("Set Volume 60%", 0, 1, size=Size(6, 1), cmd=vol60))
        ui_page1.add(create_ui_text("--- Toggle Commands ---", 0, 3, size=Size(6, 1)))
        ui_page1.add(create_ui_text("Dim", 0, 4, size=Size(3, 1), cmd=send_cmd(cmds.DIM_TOGGLE)))
        ui_page1.add(create_ui_text("Bypass", 3, 4, size=Size(3, 1), cmd=send_cmd(cmds.BYPASS_TOGGLE)))
        ui_page1.add(create_ui_text("Front Panel Light", 0, 5, size=Size(6, 1), cmd=send_cmd(cmds.FAV_LIGHT)))

        return [ui_page1]

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None) -> StatusCodes:
        """
        Handle command requests from the integration API for the remote entity.
        """

        params = params or {}

        simple_cmd: str | None = params.get("command")
        if simple_cmd and simple_cmd.startswith("remote"):
            cmd_id = simple_cmd.split(".")[1]

        _LOG.info("Received Remote command request: %s with parameters: %s", cmd_id, params or "no parameters")


        status = StatusCodes.BAD_REQUEST  # Default fallback

        try:
            cmd = Commands(cmd_id)
            _LOG.debug("Resolved command: %s", cmd)
        except ValueError:
            status = StatusCodes.NOT_IMPLEMENTED
        else:
            match cmd:
                case Commands.ON:
                    status = await self._device.power_on()

                case Commands.OFF:
                    status = await self._device.power_off()

                case Commands.SEND_CMD:
                    if not simple_cmd:
                        _LOG.warning("Missing command in SEND_CMD")
                        status = StatusCodes.BAD_REQUEST
                        return status

                    simple_cmd = simple_cmd.replace(" ", "_").lower()

                    if simple_cmd in cmds._value2member_map_:
                        actual_cmd = None
                        cmd_params = None

                        # Handle toggle command groups
                        result = (
                            parse_toggle_command("mute", simple_cmd) or
                            parse_toggle_command("dim", simple_cmd) or
                            parse_toggle_command("bypass", simple_cmd)
                        )

                        if result:
                            actual_cmd, cmd_params = result
                        else:
                            actual_cmd = simple_cmd

                        if actual_cmd:
                            _LOG.debug(actual_cmd)
                            _LOG.debug(params)
                            status = await self._device.send_command(actual_cmd, cmd_params)
                    else:
                        _LOG.warning("Unknown command: %s", simple_cmd)
                        status = StatusCodes.NOT_IMPLEMENTED

                case Commands.SEND_CMD_SEQUENCE:
                    commands = params.get("sequence", [])
                    if commands and commands[0] == "volume":
                        try:
                            volume_percent = int(commands[1])
                            await self._device.executor.volume(self._device.percent_to_db(volume_percent))
                            status = StatusCodes.OK
                        except (IndexError, ValueError):
                            status = StatusCodes.BAD_REQUEST
                    else:
                        status = StatusCodes.NOT_IMPLEMENTED

                case _:
                    status = StatusCodes.NOT_IMPLEMENTED

        return status


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
