"""
Select entity functions (Trinnov).

Single generic TrinnovSelect class driven by a spec table.

Selects:
- Sources
- Presets
- Listening Format (UPMIXER)
"""

# pylint: disable=too-many-return-statements

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from config import TrinnovEntity
from const import EntityPrefix
from device import TrinnovDevice
from ucapi import Select, StatusCodes
from ucapi.media_player import States as MediaStates
from ucapi.select import Attributes as SelectAttr
from ucapi.select import Commands
from ucapi.select import States as SelectStates
from utils import _qualify_name

_LOG = logging.getLogger(__name__)

SelectHandler = Callable[[TrinnovDevice, str | None], Awaitable[StatusCodes]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

K = TypeVar("K")

def _reverse_lookup(mapping: dict[K, str], label: str) -> K | None:
    """Return the key in mapping whose value matches label."""
    for k, v in mapping.items():
        if v == label:
            return k
    return None


# ---------------------------------------------------------------------------
# Spec definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _SelectSpec:
    """Configuration descriptor for a Trinnov select entity."""
    prefix: EntityPrefix
    name: str | dict[str, str]
    current_fn: Callable[[TrinnovDevice], str]
    options_fn: Callable[[TrinnovDevice], list[str]]
    select_fn: SelectHandler


# ---------------------------------------------------------------------------
# Select action implementations
# ---------------------------------------------------------------------------

async def _select_source(device: TrinnovDevice, label: str | None) -> StatusCodes:
    """Select a source by its display label."""
    if not label:
        return StatusCodes.BAD_REQUEST

    mapping = device.source_list or {}
    key = _reverse_lookup(mapping, label)
    if key is None:
        _LOG.warning("Invalid source label %r (available=%s)", label, list(mapping.values()))
        return StatusCodes.BAD_REQUEST

    return await device.select_source(str(key))


async def _select_preset(device: TrinnovDevice, label: str | None) -> StatusCodes:
    if not label:
        return StatusCodes.BAD_REQUEST

    mapping = device.preset_list or {}
    idx = _reverse_lookup(mapping, label)
    if idx is None:
        _LOG.warning("Invalid preset label %r (available=%s)", label, list(mapping.values()))
        return StatusCodes.BAD_REQUEST

    return await device.select_preset(int(idx))

async def _select_remapping_mode(device: TrinnovDevice, mode: str | None) -> StatusCodes:
    """Set the remapping mode on the Trinnov device."""

    if not mode:
        return StatusCodes.BAD_REQUEST

    _LOG.debug("Set remapping mode to: %s", mode)
    await device.select_remapping_mode(mode)
    _LOG.info("Sent remapping mode command for %s", mode)
    return StatusCodes.OK

async def _select_upmixer(device: TrinnovDevice, label: str | None) -> StatusCodes:
    """Select a listening format by its display label."""
    if not label:
        return StatusCodes.BAD_REQUEST

    mapping = device.listening_formats or {}
    key = _reverse_lookup(mapping, label)
    if key is None:
        _LOG.warning("Invalid listening format %r (available=%s)", label, list(mapping.values()))
        return StatusCodes.BAD_REQUEST

    return await device.select_sound_mode(str(key))


# ---------------------------------------------------------------------------
# Spec table
# ---------------------------------------------------------------------------

SELECT_SPECS: dict[EntityPrefix, _SelectSpec] = {
    EntityPrefix.SOURCES: _SelectSpec(
        prefix=EntityPrefix.SOURCES,
        name={"en": "Source"},
        current_fn=lambda d: (d.source_list or {}).get(d._attr_source_index, ""),
        options_fn=lambda d: list((d.source_list or {}).values()),
        select_fn=_select_source,
    ),
    EntityPrefix.PRESETS: _SelectSpec(
        prefix=EntityPrefix.PRESETS,
        name={"en": "Preset"},
        current_fn=lambda d: (d.preset_list or {}).get(d.preset_index, ""),
        options_fn=lambda d: list((d.preset_list or {}).values()),
        select_fn=_select_preset,
    ),
    EntityPrefix.LISTENING_FORMAT: _SelectSpec(
        prefix=EntityPrefix.LISTENING_FORMAT,
        name={"en": "Listening Format"},
        current_fn=lambda d: d.listening_format_label or "",
        options_fn=lambda d: list((d.listening_formats or {}).values()),
        select_fn=_select_upmixer,
    ),
    EntityPrefix.REMAPPING_MODE_SELECT: _SelectSpec(
        prefix=EntityPrefix.REMAPPING_MODE_SELECT,
        name={"en": "Remapping Mode"},
        current_fn=lambda d: (
            "Disabled" if getattr(d, "remapping_mode", "") == "none"
            else getattr(d, "remapping_mode", "") or ""
        ),
        options_fn=lambda d: ["Disabled", "2D", "3D"],
        select_fn=_select_remapping_mode,
    ),
}


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class TrinnovSelect(Select, TrinnovEntity):
    """Generic Trinnov select entity."""

    def __init__(
            self,
            device_id: str,
            device_name: str,
            device: TrinnovDevice,
            prefix: EntityPrefix
        ) -> None:

        if prefix not in SELECT_SPECS:
            raise ValueError(f"Unsupported select prefix: {prefix}")

        self.device_id = device_id

        self._device = device
        self._spec = SELECT_SPECS[prefix]
        self._state: SelectStates = SelectStates.ON

        entity_id = f"{prefix.value}.{device_id}"

        qualified_name = _qualify_name(device_name, self._spec.name)

        super().__init__(
            identifier=entity_id,
            name=qualified_name,
            attributes={},
        )


    @property
    def current_option(self) -> str:
        """Return the currently selected option label."""
        return self._spec.current_fn(self._device)

    @property
    def select_options(self) -> list[str]:
        """Return the list of selectable option labels."""
        return self._spec.options_fn(self._device)

    def update_attributes(
        self,
        update: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Apply incremental updates or build a full select state snapshot."""

        # Incremental update from driver
        if update is not None:
            if SelectAttr.STATE in update:
                self._state = update[SelectAttr.STATE]  # keep internal state in sync
            return update

        # Full snapshot (used during priming / get_entity_states)
        options = self.select_options
        state = (
            SelectStates.UNAVAILABLE
            if self._device.state in (MediaStates.OFF, MediaStates.UNAVAILABLE)
            else SelectStates.ON
        )

        return {
            SelectAttr.CURRENT_OPTION: self.current_option,
            SelectAttr.OPTIONS: options,
            SelectAttr.STATE: state,
        }

    async def command(
        self,
        cmd_id: str,
        params: dict[str, object] | None = None,
        *,
        websocket=None,
    ) -> StatusCodes:
        """Process selector commands (vendor-style behavior)."""

        if cmd_id == Commands.SELECT_OPTION and params:
            option = params.get("option")
            return await self._spec.select_fn(self._device, option)

        options = self.select_options
        if not options:
            return StatusCodes.OK

        if cmd_id == Commands.SELECT_FIRST:
            return await self._spec.select_fn(self._device, options[0])

        if cmd_id == Commands.SELECT_LAST:
            return await self._spec.select_fn(self._device, options[-1])

        if cmd_id == Commands.SELECT_NEXT:
            cycle = bool((params or {}).get("cycle", False))

            try:
                index = options.index(self.current_option) + 1
                if not cycle and index >= len(options):
                    return StatusCodes.OK
                if index >= len(options):
                    index = 0
                return await self._spec.select_fn(self._device, options[index])
            except ValueError:
                return StatusCodes.BAD_REQUEST

        if cmd_id == Commands.SELECT_PREVIOUS:
            cycle = bool((params or {}).get("cycle", False))
            try:
                index = options.index(self.current_option) - 1
                if not cycle and index < 0:
                    return StatusCodes.OK
                if index < 0:
                    index = len(options) - 1
                return await self._spec.select_fn(self._device, options[index])
            except ValueError:
                return StatusCodes.BAD_REQUEST

        return StatusCodes.BAD_REQUEST


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_trinnov_selects(
        device_id: str,
        device_name: str,
        device: TrinnovDevice
    ) -> list[TrinnovSelect]:
    """Create all Trinnov select entities for a device."""
    return [
        TrinnovSelect(device_id, device_name, device, EntityPrefix.SOURCES),
        TrinnovSelect(device_id, device_name, device, EntityPrefix.PRESETS),
        TrinnovSelect(device_id, device_name, device, EntityPrefix.LISTENING_FORMAT),
        TrinnovSelect(device_id, device_name, device, EntityPrefix.REMAPPING_MODE_SELECT),
    ]
