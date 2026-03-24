"""
Trinnov sensor entities.

Single generic TrinnovSensor class driven by a spec table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from config import TrinnovEntity
from const import EntityPrefix
from device import TrinnovDevice
from ucapi.media_player import States as MediaStates
from ucapi.sensor import Attributes as SensorAttr
from ucapi.sensor import DeviceClasses, Options, Sensor, States
from utils import _qualify_name

# ---------------------------------------------------------------------------
# Sensor availability mapping
# ---------------------------------------------------------------------------

SENSOR_STATE_MAPPING: dict[MediaStates, States] = {
    MediaStates.OFF: States.UNAVAILABLE,
    MediaStates.ON: States.ON,
    MediaStates.STANDBY: States.ON,
    MediaStates.PLAYING: States.ON,
    MediaStates.PAUSED: States.ON,
    MediaStates.UNAVAILABLE: States.UNAVAILABLE,
    MediaStates.UNKNOWN: States.UNKNOWN,
}


# ---------------------------------------------------------------------------
# Spec definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _SensorSpec:
    """Configuration descriptor for a Trinnov sensor entity."""
    prefix: EntityPrefix
    name: str | dict[str, str]
    value_fn: Callable[[Any], str | float]
    device_class: DeviceClasses = DeviceClasses.CUSTOM
    options: dict[Options, Any] | None = None


SENSOR_SPECS: dict[EntityPrefix, _SensorSpec] = {
    EntityPrefix.AUDIO_SYNC: _SensorSpec(
        prefix=EntityPrefix.AUDIO_SYNC,
        name={"en": "Audio Sync"},
        value_fn=lambda d: "Synced" if bool(getattr(d, "audio_sync", False)) else "Not synced",
    ),
    EntityPrefix.CODEC: _SensorSpec(
        prefix=EntityPrefix.CODEC,
        name={"en": "Codec"},
        value_fn=lambda d: getattr(d, "codec", "") or "",
    ),
    EntityPrefix.MUTE: _SensorSpec(
        prefix=EntityPrefix.MUTE,
        name={"en": "Mute Status"},
        value_fn=lambda d: "on" if bool(getattr(d, "muted", False)) else "off",
        device_class = DeviceClasses.BINARY,
    ),
    EntityPrefix.REMAPPING_MODE: _SensorSpec(
        prefix=EntityPrefix.REMAPPING_MODE,
        name={"en": "Remapping"},
        value_fn=lambda d: (
            "Disabled" if getattr(d, "remapping_mode", "") == "none"
            else getattr(d, "remapping_mode", "") or ""
        ),
    ),
    EntityPrefix.SAMPLE_RATE: _SensorSpec(
        prefix=EntityPrefix.SAMPLE_RATE,
        name={"en": "Sample Rate"},
        value_fn=lambda d: getattr(d, "srate", 0) or 0,
        options={
            Options.CUSTOM_UNIT: "kHz",
            Options.MIN_VALUE: 0,
            Options.MAX_VALUE: 192,
        },
    ),
    EntityPrefix.UPMIXER: _SensorSpec(
        prefix=EntityPrefix.UPMIXER,
        name={"en": "Upmixer"},
        value_fn=lambda d: getattr(d, "listening_format_label", "") or "",
    ),
    EntityPrefix.VOLUME: _SensorSpec(
        prefix=EntityPrefix.VOLUME,
        name={"en": "Volume"},
        value_fn=lambda d: float(getattr(d, "volume", 0) or 0),
        options={
            Options.CUSTOM_UNIT: "dB"
        },
    ),
}


# ---------------------------------------------------------------------------
# TrinnovSensor Entity
# ---------------------------------------------------------------------------

class TrinnovSensor(Sensor, TrinnovEntity):
    """
    Generic Trinnov sensor driven by SENSOR_SPECS.

    Entity id convention: "{prefix}.{device_id}"
    """

    def __init__(self,
                 device_id: str,
                 device_name: str,
                 device: TrinnovDevice,
                 prefix: EntityPrefix
            ) -> None:

        if prefix not in SENSOR_SPECS:
            raise ValueError(f"Unsupported sensor prefix: {prefix}")

        self.device_id = device_id

        self._device = device
        self._spec = SENSOR_SPECS[prefix]
        self._state: States = States.UNAVAILABLE

        entity_id = f"{self._spec.prefix.value}.{device_id}"

        qualified_name = _qualify_name(device_name, self._spec.name)

        super().__init__(
            entity_id,
            qualified_name,
            [],
            {},
            device_class=self._spec.device_class,
            options=self._spec.options,
        )


    @property
    def state(self) -> States:
        """Return the current sensor state."""
        return self._state

    @property
    def sensor_value(self) -> str | float:
        """Return the current sensor state."""
        return self._spec.value_fn(self._device)

    def update_attributes(
        self,
        update: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Update attributes from incoming payload.

        Incoming updates from TrinnovDevice use ucapi.sensor Attributes enums:
        - SensorAttr.STATE
        - SensorAttr.VALUE
        """
        if update:
            attrs: dict[str, Any] = {}

            # Direct state updates (already a ucapi.sensor States value)
            if SensorAttr.STATE in update:
                new_state = update[SensorAttr.STATE]
                if new_state != self._state:
                    self._state = new_state
                    attrs[SensorAttr.STATE] = self._state

            # Direct value updates
            if SensorAttr.VALUE in update:
                attrs[SensorAttr.VALUE] = update[SensorAttr.VALUE]

            return attrs or None

        # Full refresh (snapshot from cached device properties)
        dev_state = getattr(self._device, "state", MediaStates.UNKNOWN)
        return {
            SensorAttr.VALUE: self.sensor_value,
            SensorAttr.STATE: SENSOR_STATE_MAPPING.get(dev_state, States.UNKNOWN),
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_trinnov_sensors(
        device_id: str,
        device_name: str,
        device: TrinnovDevice
    ) -> list[TrinnovSensor]:
    """Create all Trinnov sensor entities for a device."""
    prefixes = [
        EntityPrefix.AUDIO_SYNC,
        EntityPrefix.CODEC,
        EntityPrefix.UPMIXER,
        EntityPrefix.MUTE,
        EntityPrefix.REMAPPING_MODE,
        EntityPrefix.SAMPLE_RATE,
        EntityPrefix.VOLUME,
    ]
    return [TrinnovSensor(device_id, device_name, device, p) for p in prefixes]
