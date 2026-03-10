"""
Select entity functions.

:copyright: (c) 2023 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from config import TrinnovEntity
from device import TrinnovDevice, TrinnovInfo
from ucapi import Select, StatusCodes
from ucapi.select import Attributes as SelectAttr
from ucapi.select import Commands, States

_LOG = logging.getLogger(__name__)

SELECT_PRESETS = "presets"
SELECT_SOURCES = "sources"


class TrinnovSelect(TrinnovEntity, Select):
    """Representation of a Trinnov select entity."""

    def __init__(self, info: TrinnovInfo, device: TrinnovDevice, key: str, name: str) -> None:
        """Initialize select."""
        self._info = info
        self._device = device
        self._key = key
        super().__init__(
            identifier=f"{key}.{info.id}",
            name=name,
            attributes={
                SelectAttr.STATE: States.UNAVAILABLE,
                SelectAttr.CURRENT_OPTION: None,
                SelectAttr.OPTIONS: None,
            },
        )

    @property
    def current_option(self) -> str:
        """Return current option."""
        raise NotImplementedError

    @property
    def select_options(self) -> list[str]:
        """Return options list."""
        raise NotImplementedError

    def filter_changed_attributes(self, update: dict[str, Any]) -> dict[str, Any]:
        """Return only attributes that actually changed."""
        changed: dict[str, Any] = {}

        if SelectAttr.OPTIONS in update:
            new_opts = update[SelectAttr.OPTIONS] or []
            old_opts = self.attributes.get(SelectAttr.OPTIONS) or []
            if new_opts != old_opts:
                changed[SelectAttr.OPTIONS] = new_opts

        if SelectAttr.CURRENT_OPTION in update:
            new_cur = update[SelectAttr.CURRENT_OPTION]
            old_cur = self.attributes.get(SelectAttr.CURRENT_OPTION)

            # Prevent startup/reconnect updates from wiping a good label.
            if new_cur in (None, "") and old_cur not in (None, ""):
                pass
            elif new_cur != old_cur:
                changed[SelectAttr.CURRENT_OPTION] = new_cur


        if SelectAttr.STATE in update:
            new_state = update[SelectAttr.STATE]
            old_state = self.attributes.get(SelectAttr.STATE)
            if new_state != old_state:
                changed[SelectAttr.STATE] = new_state

        return changed


    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any
    ) -> StatusCodes:
        """Process select commands from UI."""
        params = params or {}

        if cmd_id == Commands.SELECT_OPTION:
            option = params.get("option")
            if option is None:
                return StatusCodes.BAD_REQUEST
            return await self._select_handler(option)

        options = self.select_options

        if cmd_id == Commands.SELECT_FIRST and options:
            return await self._select_handler(options[0])

        if cmd_id == Commands.SELECT_LAST and options:
            return await self._select_handler(options[-1])

        if cmd_id == Commands.SELECT_NEXT and options:
            cycle = bool(params.get("cycle", False))
            try:
                idx = options.index(self.current_option) + 1
            except ValueError:
                idx = 0
            if idx >= len(options):
                if not cycle:
                    return StatusCodes.OK
                idx = 0
            return await self._select_handler(options[idx])

        if cmd_id == Commands.SELECT_PREVIOUS and options:
            cycle = bool(params.get("cycle", False))
            try:
                idx = options.index(self.current_option) - 1
            except ValueError:
                idx = len(options) - 1
            if idx < 0:
                if not cycle:
                    return StatusCodes.OK
                idx = len(options) - 1
            return await self._select_handler(options[idx])

        _LOG.debug("Unhandled select command: %s params=%s entity=%s", cmd_id, params, self.id)
        if cmd_id in {"turn_on", "turn_off", "enable", "disable"}:
            return StatusCodes.OK

        return StatusCodes.BAD_REQUEST

    async def _select_handler(self, option: str) -> StatusCodes:
        """Apply the selected option."""
        raise NotImplementedError


class TrinnovPresetsSelect(TrinnovSelect):
    """Preset selector."""

    def __init__(self, info: TrinnovInfo, device: TrinnovDevice) -> None:
        """Initialize preset select."""
        super().__init__(info, device, SELECT_PRESETS, f"Trinnov ({info.id}) Presets")
        self._current: str = ""

    @property
    def current_option(self) -> str:
        return str(self.attributes.get(SelectAttr.CURRENT_OPTION) or "")

    @property
    def select_options(self) -> list[str]:
        """Return preset options."""
        # profiles_list is dict[int,str] in your device :contentReference[oaicite:3]{index=3}
        return list(self._device.preset_list.values()) if self._device.preset_list else []

    async def _select_handler(self, option: str) -> StatusCodes:
        """Select preset by name."""
        self._current = option
        # TODO: wire to real executor call later
        return StatusCodes.OK


class TrinnovSourcesSelect(TrinnovSelect):
    """Source selector."""

    def __init__(self, info: TrinnovInfo, device: TrinnovDevice) -> None:
        """Initialize source select."""
        super().__init__(info, device, SELECT_SOURCES, f"Trinnov ({info.id}) Sources")
        self._current: str = ""

    @property
    def current_option(self) -> str:
        return str(self.attributes.get(SelectAttr.CURRENT_OPTION) or "")

    @property
    def select_options(self) -> list[str]:
        """Return source options."""
        # source_list is dict[str,str] in your device :contentReference[oaicite:4]{index=4}
        return list(self._device.source_list.values()) if self._device.source_list else []

    async def _select_handler(self, option: str) -> StatusCodes:
        """Select source by label."""
        self._current = option
        # TODO: wire to device.select_source_by_label(option) later
        return StatusCodes.OK
