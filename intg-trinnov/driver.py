#!/usr/bin/env python3
"""Trinnov UC Remote Integration Driver."""

import logging
from typing import Any

import config
import ucapi
from api import api, loop
from const import EntityPrefix
from device import Events, TrinnovDevice, TrinnovInfo
from media_player import TrinnovMediaPlayer
from registry import (all_devices, clear_devices, connect_all, disconnect_all,
                      get_device, register_device, unregister_device)
from remote import REMOTE_STATE_MAPPING, TrinnovRemote
from sensor import TrinnovSensor
from setup_flow import driver_setup_handler
from ucapi.media_player import Attributes as MediaAttr
from ucapi.media_player import States
from ucapi.sensor import Attributes as SensorAttr
from utils import setup_logger

_LOG = logging.getLogger("driver")


@api.listens_to(ucapi.Events.CONNECT)
async def on_connect() -> None:
    """Connect all configured receivers when the Remote Two sends the connect command."""
    _LOG.info("Received connect event message from remote")
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)
    loop.create_task(connect_all())


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_r2_disconnect() -> None:
    """Disconnect notification from the Remote Two."""

    _LOG.info("Received disconnect event message from remote")
    await api.set_device_state(ucapi.DeviceStates.DISCONNECTED)
    loop.create_task(disconnect_all())


@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def on_r2_enter_standby() -> None:
    """
    Enter standby notification from Remote Two.

    Disconnect every Trinnov instance.
    """

    _LOG.debug("Enter standby event: disconnecting device(s)")
    loop.create_task(disconnect_all())


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def on_r2_exit_standby() -> None:
    """
    Exit standby notification from Remote Two.

    Connect all Trinnov instances.
    """

    _LOG.debug("Exit standby event: connecting device(s)")
    loop.create_task(connect_all())

@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids: list[str]) -> None:
    """
    Subscribe to given entities.

    :param entity_ids: entity identifiers.
    """
    _LOG.debug("Subscribe entities event: %s", entity_ids)

    if not entity_ids:
        return

    # Assume all entities share the same device
    first_entity = api.configured_entities.get(entity_ids[0])
    if not first_entity:
        _LOG.error("First entity %s not found in configured_entities", entity_ids[0])
        return

    device_id = config.extract_device_id(first_entity)
    device = get_device(device_id)

    if not device:
        fallback_device = config.devices.get(device_id)
        if fallback_device:
            _configure_new_trinnov(fallback_device, connect=True)
        else:
            _LOG.error(
                "Failed to subscribe entities: no Trinnov configuration found for %s",
                device_id
            )
        return

    for entity_id in entity_ids:
        _LOG.debug("entity id = %s", entity_id)
        entity = api.configured_entities.get(entity_id)
        if not entity:
            continue

        # Handle TrinnovSensor entities
        if isinstance(entity, TrinnovSensor):
            _LOG.info("Setting initial state of Trinnov Sensor %s", entity_id)

            if entity_id.startswith(EntityPrefix.SAMPLE_RATE.value):
                api.configured_entities.update_attributes(
                    entity_id,
                    {
                        SensorAttr.STATE: States.OFF,
                        SensorAttr.VALUE: device.srate,
                        SensorAttr.UNIT: "kHz"
                    }
                )
            elif entity_id.startswith(EntityPrefix.AUDIO_SYNC.value):
                api.configured_entities.update_attributes(
                    entity_id,
                    {
                        SensorAttr.STATE: States.ON,
                        SensorAttr.VALUE: device.audio_sync,
                        SensorAttr.UNIT: ""
                    }
                )
            elif entity_id.startswith(EntityPrefix.MUTED.value):
                api.configured_entities.update_attributes(
                    entity_id,
                    {
                        SensorAttr.STATE: States.ON,
                        SensorAttr.VALUE: device.muted,
                        SensorAttr.UNIT: ""
                    }
                )
            elif entity_id.startswith(EntityPrefix.VOLUME.value):
                api.configured_entities.update_attributes(
                    entity_id,
                    {
                        SensorAttr.STATE: States.ON,
                        SensorAttr.VALUE: device.volume,
                        SensorAttr.UNIT: "dB"
                    }
                )

            current_value = entity.attributes.get(SensorAttr.VALUE, "unknown")
            _LOG.info("Updated Trinnov Sensor entity %s with value %s", entity_id, current_value)
            continue

        # Handle media_player or remote entities
        _update_entity_attributes(entity_id, entity, device.attributes)


def _update_entity_attributes(entity_id: str, entity, attributes: dict):
    """
    Update attributes for the given entity based on its type.
    """
    if isinstance(entity, TrinnovMediaPlayer):
        api.configured_entities.update_attributes(entity_id, attributes)
    elif isinstance(entity, TrinnovRemote):
        api.configured_entities.update_attributes(
            entity_id,
            {
                ucapi.remote.Attributes.STATE:
                REMOTE_STATE_MAPPING.get(attributes.get(MediaAttr.STATE, States.UNAVAILABLE))
            }
        )


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids: list[str]) -> None:
    """On unsubscribe, disconnect devices only if no other entities are using them."""
    _LOG.debug("Unsubscribe entities event: %s", entity_ids)

    # Collect devices associated with the entities being unsubscribed
    devices_to_remove = {
        config.extract_device_id(api.configured_entities.get(entity_id))
        for entity_id in entity_ids
        if api.configured_entities.get(entity_id)
    }

    # Check other remaining entities to see if they still use these devices
    remaining_entities = [
        e for e in api.configured_entities.get_all()
        if e.get("entity_id") not in entity_ids
    ]

    for entity in remaining_entities:
        device_id = config.extract_device_id(entity)
        devices_to_remove.discard(device_id)  # discard safely removes if present

    # Disconnect and clean up devices no longer in use
    for device_id in devices_to_remove:
        if device_id in all_devices():
            device = get_device(device_id)
            await device.disconnect()
            device.events.remove_all_listeners()



def _configure_new_trinnov(info: TrinnovInfo, connect: bool = False) -> None:
    """
    Create and configure a new Trinnov device.

    If a device already exists for the given device ID, reuse it.
    Otherwise, create and register a new one.

    :param info: The Trinnov device configuration.
    :param connect: Whether to initiate connection immediately.
    """

    _LOG.debug("Configure new Trinnov connect = %s", connect)

    device = get_device(info.id)
    if device:
        device.disconnect()
    else:
        device = TrinnovDevice(info.ip, info.mac, device_id=info.id)

        device.events.on(Events.CONNECTED.name, on_trinnov_connected)
        device.events.on(Events.DISCONNECTED.name, on_trinnov_disconnected)
        device.events.on(Events.UPDATE.name, on_trinnov_update)

        register_device(info.id, device)

    if connect:
        loop.create_task(device.connect())

    _register_available_entities(info, device)


def _register_available_entities(info: TrinnovInfo, device: TrinnovDevice) -> None:
    """
    Register remote and media player entities for a Trinnov device and associate its device.

    :param info: Trinnov configuration
    :param device: Active TrinnovDevice for the device
    """
    for entity_cls in (TrinnovRemote, TrinnovMediaPlayer):
        entity = entity_cls(info, device)

        if api.available_entities.contains(entity.id):
            api.available_entities.remove(entity.id)

        api.available_entities.add(entity)

    for sensor in [EntityPrefix.SAMPLE_RATE, EntityPrefix.AUDIO_SYNC, EntityPrefix.VOLUME, EntityPrefix.MUTED]:
        entity = TrinnovSensor(info, sensor.value)

        if api.available_entities.contains(entity.id):
            api.available_entities.remove(entity.id)

        api.available_entities.add(entity)


async def on_trinnov_connected(device_id: str):
    """Handle Trinnov connection."""
    _LOG.debug("Trinnov connected: %s", device_id)

    if not get_device(device_id):
        _LOG.warning("Trinnov %s is not configured", device_id)
        return

    await api.set_device_state(ucapi.DeviceStates.CONNECTED)


async def on_trinnov_disconnected(device_id: str):
    """Handle Trinnov disconnection."""
    _LOG.debug("Trinnov disconnected: %s", device_id)

    if not get_device(device_id):
        _LOG.warning("Trinnov %s is not configured", device_id)
        return

    await api.set_device_state(ucapi.DeviceStates.DISCONNECTED)


async def on_trinnov_update(entity_id: str, update: dict[str, Any] | None) -> None:
    """
    Update attributes of a configured entity if its attributes changed.

    :param entity_id: Fully qualified entity identifier (e.g., media_player.device_id).
    :param update: Dictionary containing the updated attributes or None.
    """
    if update is None:
        return

    device_id = entity_id.split(".", 1)[1]
    device = get_device(device_id)
    if device is None:
        return

    _LOG.debug("[%s] Trinnov update: %s", device_id, update)

    entity: TrinnovMediaPlayer | TrinnovRemote | TrinnovSensor | None = (
        api.configured_entities.get(entity_id)
    )
    if entity is None:
        _LOG.debug("Entity %s not found", entity_id)
        return

    changed_attrs = entity.filter_changed_attributes(update)
    if changed_attrs:
        _LOG.debug("Changed Attrs: %s, %s", entity_id, changed_attrs)
        api_update_attributes = api.configured_entities.update_attributes(entity_id, changed_attrs)
        _LOG.debug("api_update_attributes = %s", api_update_attributes)
    else:
        _LOG.debug("attributes not changed")


def on_device_added(info: TrinnovInfo) -> None:
    """Handle a newly added device in the configuration."""
    _LOG.debug("New Trinnov device added: %s", info)

    loop.create_task(api.set_device_state(ucapi.DeviceStates.CONNECTED))
    _configure_new_trinnov(info, connect=False)


def on_device_removed(info: TrinnovInfo | None) -> None:
    """Handle removal of a Trinnov device from config."""
    if info is None:
        _LOG.info("All devices cleared from config.")
        clear_devices()
        api.configured_entities.clear()
        api.available_entities.clear()
        return

    device = get_device(info.id)
    if device:
        unregister_device(info.id)
        loop.create_task(_async_remove(info))
        api.configured_entities.remove(f"media_player.{info.id}")
        api.configured_entities.remove(f"remote.{info.id}")
        _LOG.info("Device for device_id %s cleaned up", info.id)
    else:
        _LOG.debug("No Device found for removed device %s", info.id)


async def _async_remove(device: TrinnovDevice) -> None:
    """Disconnect from receiver and remove all listeners."""
    _LOG.debug("Disconnecting and removing all listeners")
    await device.disconnect()
    device.events.remove_all_listeners()


async def main():
    """Start the Remote Two integration driver."""

    logging.basicConfig(
        format=(
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | "
            "%(name)-14s | %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    setup_logger()

    _LOG.debug("Starting driver...")
    await api.init("driver.json", driver_setup_handler)

    config.devices = config.Devices(api.config_dir_path, on_device_added, on_device_removed)
    for device in config.devices:
        _configure_new_trinnov(device, connect=False)


if __name__ == "__main__":
    try:
        loop.run_until_complete(main())
        loop.run_forever()
    except KeyboardInterrupt:
        pass
