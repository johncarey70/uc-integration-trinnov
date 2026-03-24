"""
Media-player entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from config import TrinnovEntity
from const import EntityPrefix, MediaPlayerDef
from device import TrinnovDevice, TrinnovInfo
from ucapi import MediaPlayer, StatusCodes
from ucapi.media_player import Attributes, Commands, DeviceClasses, States

_LOG = logging.getLogger(__name__)

class TrinnovMediaPlayer(MediaPlayer, TrinnovEntity):
    """Representation of a Trinnov Media Player entity."""

    def __init__(self, mp_info: TrinnovInfo, device: TrinnovDevice):
        self._device = device
        self.device_id = mp_info.id

        entity_id = f"{EntityPrefix.MEDIA_PLAYER.value}.{mp_info.id}"

        features = MediaPlayerDef.features
        attributes = MediaPlayerDef.attributes

        super().__init__(
            entity_id,
            f"{mp_info.name} Media Player",
            features,
            attributes,
            device_class=DeviceClasses.RECEIVER,
            options=None,
        )


    async def command(
            self,
            cmd_id: str,
            params: dict[str, Any] | None = None,
            *,
            websocket: Any
        ) -> StatusCodes:
        """
        Media-player entity command handler.

        Called by the integration-API if a command is sent to a configured media-player entity.

        :param cmd_id: command
        :param params: optional command parameters
        :return: status code of the command request
        """
        _LOG.info("Got %s command request: %s %s", self.id, cmd_id, params)

        params = params or {}

        try:
            cmd = Commands(cmd_id)
        except ValueError:
            return StatusCodes.BAD_REQUEST

        match cmd:
            case Commands.ON:
                res = await self._device.power_on()
            case Commands.OFF:
                res = await self._device.power_off()
            case Commands.PLAY_PAUSE:
                res = StatusCodes.OK
            case Commands.NEXT:
                res = StatusCodes.OK
            case Commands.PREVIOUS:
                res = StatusCodes.OK
            case Commands.MUTE_TOGGLE:
                res = await self._device.send_command("mute", 2)
            case Commands.MUTE:
                res = await self._device.send_command("mute", 1)
            case Commands.UNMUTE:
                res = await self._device.send_command("mute", 0)

            case Commands.SELECT_SOUND_MODE:
                formats = self._device.listening_formats
                upmixer_label = params.get("mode")
                if not formats or not upmixer_label:
                    res = StatusCodes.BAD_REQUEST
                else:
                    mode_key = next((k for k, v in formats.items() if v == upmixer_label), None)
                    if not mode_key:
                        res = StatusCodes.BAD_REQUEST
                    else:
                        res = await self._device.select_sound_mode(mode_key)

            case Commands.SELECT_SOURCE:
                labels = self._device.source_list
                label_name = params.get("source")
                if not labels or not label_name:
                    res = StatusCodes.BAD_REQUEST
                else:
                    index = next((k for k, v in labels.items() if v == label_name), None)
                    if index is None:
                        res = StatusCodes.BAD_REQUEST
                    else:
                        res = await self._device.send_command("select_source", index)

            case Commands.VOLUME_DOWN:
                res = await self._device.send_command("volume_down")

            case Commands.VOLUME_UP:
                res = await self._device.send_command("volume_up")

            case Commands.VOLUME:
                try:
                    raw_volume = params.get("volume")
                    if raw_volume is None:
                        res = StatusCodes.BAD_REQUEST
                    else:
                        volume = int(raw_volume)
                        res = await self._device.send_command(
                            "volume",
                            self._device.percent_to_db(volume),
                        )
                except (TypeError, ValueError):
                    res = StatusCodes.BAD_REQUEST

            case _:
                res = StatusCodes.BAD_REQUEST

        return res

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """
        Filter the given attributes and return only the changed values.

        :param update: dictionary with attributes.
        :return: filtered entity attributes containing changed attributes only.
        """
        attributes = {}
        update[Attributes.SOUND_MODE_LIST] = list(self._device.listening_formats.values())

        labels = self._device.source_list

        if isinstance(labels, dict):
            update[Attributes.SOURCE_LIST] = list(labels.values())
        else:
            update[Attributes.SOURCE_LIST] = list(labels) if labels else []

        for key in (
            Attributes.MUTED,
            Attributes.SOUND_MODE,
            Attributes.SOUND_MODE_LIST,
            Attributes.SOURCE,
            Attributes.SOURCE_LIST,
            Attributes.STATE,
            Attributes.VOLUME,
        ):
            if key in update and key in self.attributes:
                if update[key] != self.attributes[key]:
                    attributes[key] = update[key]

        if attributes.get(Attributes.STATE) == States.OFF:
            attributes[Attributes.SOURCE] = ""

        if attributes:
            _LOG.debug("TrinnovMediaPlayer update attributes %s -> %s", update, attributes)

        return attributes
