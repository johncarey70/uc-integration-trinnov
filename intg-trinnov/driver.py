#!/usr/bin/env python3
"""Trinnov UC Remote Integration Driver."""

import asyncio
import logging
from enum import Enum
from typing import Any, Type

import config
import ucapi
from api import api, loop
from device import Events, TrinnovDevice, TrinnovInfo
from media_player import TrinnovMediaPlayer
from registry import (all_devices, connect_all, disconnect_all, get_device,
                      register_device, unregister_device)
from remote import REMOTE_STATE_MAPPING, TrinnovRemote
from selects import TrinnovSelect, build_trinnov_selects
from sensors import TrinnovSensor, build_trinnov_sensors
from setup_flow import driver_setup_handler
from ucapi.media_player import Attributes as MediaAttr
from ucapi.media_player import States
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
    await asyncio.sleep(0)
    try:
        await api.set_device_state(ucapi.DeviceStates.DISCONNECTED)
    except RuntimeError as e:
        _LOG.warning("Runtime Error during set_device_state(): %s", e)
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

def _has_configured_devices() -> bool:
    """True if there is at least one configured device."""
    return any(True for _ in config.devices)

@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids: list[str]) -> None:
    """Subscribe to given entities."""
    _LOG.debug("Subscribe entities event: %s", entity_ids)
    if not entity_ids:
        return

    if not _has_configured_devices():
        _LOG.debug(
            "Ignoring subscribe_entities (no configured devices): %s",
            entity_ids,
        )
        return

    first_entity = api.configured_entities.get(entity_ids[0])
    if not first_entity:
        _LOG.debug(
            "Ignoring subscribe for stale entity %s (not configured)",
            entity_ids[0],
        )
        return

    device_id = config.extract_device_id(first_entity)
    device = _get_or_configure_device(device_id)
    if not device:
        _LOG.debug("Device %s not available after configure", device_id)
        return

    _ensure_device_connected(device)

    for entity_id in entity_ids:
        entity = api.configured_entities.get(entity_id)
        if not entity:
            continue

        if isinstance(entity, (TrinnovSensor, TrinnovSelect)):
            snapshot = entity.update_attributes(None) or {}
            if snapshot:
                merged = dict(entity.attributes or {})
                merged.update(snapshot)
                api.configured_entities.update_attributes(entity_id, merged)
        elif isinstance(entity, TrinnovMediaPlayer):
            api.configured_entities.update_attributes(entity_id, device.attributes)
        elif isinstance(entity, TrinnovRemote):
            api.configured_entities.update_attributes(
                entity_id,
                {
                    ucapi.remote.Attributes.STATE: REMOTE_STATE_MAPPING.get(
                        device.attributes.get(MediaAttr.STATE, States.UNAVAILABLE)
                    )
                },
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
        devices_to_remove.discard(device_id)

    # Disconnect and clean up devices no longer in use
    for device_id in devices_to_remove:
        if device_id in all_devices():
            device = get_device(device_id)
            await device.disconnect()
            device.events.remove_all_listeners()

def _get_or_configure_device(device_id: str) -> TrinnovDevice | None:
    """Return an existing configured device or configure it from saved config."""
    device = get_device(device_id)
    if device:
        return device

    fallback_device = config.devices.get(device_id)
    if not fallback_device:
        _LOG.error(
            "Failed to subscribe entities: no Trinnov configuration found for %s",
            device_id,
        )
        return None

    _configure_new_trinnov(fallback_device, connect=True)
    return get_device(device_id)

def _ensure_device_connected(device: TrinnovDevice) -> None:
    """Trigger a background connect when a configured device is not connected."""
    if device.is_connected or device.is_connecting:
        return

    _LOG.debug(
        "Device %s is not connected during subscribe; triggering reconnect",
        device.device_id,
    )
    loop.create_task(device.connect())

def filter_attributes(attributes: dict, attribute_type: Type[Enum]) -> dict[str, Any]:
    """Filter attributes based on an Enum class."""
    return {k: v for k, v in attributes.items() if k in attribute_type}

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
        if not connect:
            loop.create_task(device.disconnect())
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
    Register remote, media player, sensors, and selects for a Trinnov device.

    :param info: Trinnov configuration
    :param device: Active TrinnovDevice for the device
    """
    def _add(entity) -> None:
        if api.available_entities.contains(entity.id):
            api.available_entities.remove(entity.id)
        api.available_entities.add(entity)

    # Remote + Media Player
    for entity_cls in (TrinnovRemote, TrinnovMediaPlayer):
        _add(entity_cls(info, device))

    device_id = getattr(info, "id", None)
    if not device_id:
        raise ValueError("TrinnovInfo.id is required to register entities")

    device_name = info.name

    # Sensors
    for entity in build_trinnov_sensors(device_id, device_name, device):
        _add(entity)

    # Selects
    for entity in build_trinnov_selects(device_id, device_name, device):
        _add(entity)

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

    _LOG.debug("UPDATE keys: %s", list(update.keys()))

    device_id = entity_id.split(".", 1)[1]
    device = get_device(device_id)
    if device is None:
        return

    entity: TrinnovMediaPlayer | TrinnovRemote | TrinnovSensor | TrinnovSelect | None = (
        api.configured_entities.get(entity_id)
    )
    if entity is None:
        _LOG.debug("Entity %s not found", entity_id)
        return

    if isinstance(entity, (TrinnovMediaPlayer, TrinnovRemote)):
        changed_attrs = entity.filter_changed_attributes(update)
    else:
        changed_attrs = entity.update_attributes(update) or {}

    if changed_attrs:
        merged = dict(entity.attributes or {})
        merged.update(changed_attrs)
        _LOG.debug("Changed Attrs: %s, %s", entity_id, changed_attrs)
        api_update_attributes = api.configured_entities.update_attributes(entity_id, merged)
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
    _LOG.warning("on_device_removed")

    if info is None:
        _LOG.debug("Configuration cleared, removing all configured Trinnov instances one by one")

        for device_id in list(all_devices()):
            device = get_device(device_id)
            if device:
                loop.create_task(_async_remove(device))

            _remove_trinnov_entities(device_id)
            unregister_device(device_id)
            _LOG.info("Device for device_id %s cleaned up", device_id)

        api.configured_entities.clear()
        api.available_entities.clear()

        _LOG.info("All devices cleared from config.")
        return

    device_id = info.id
    device = get_device(device_id)
    if device:
        loop.create_task(_async_remove(device))

    _remove_trinnov_entities(device_id)
    unregister_device(device_id)
    _LOG.info("Device for device_id %s cleaned up", device_id)

def _remove_trinnov_entities(device_id: str) -> None:
    """Remove all Trinnov entities for device_id from both configured + available."""
    suffix = f".{device_id}"
    _LOG.debug("_remove_trinnov_entities %s", device_id)

    for e in list(api.configured_entities.get_all()):
        eid = e.get("entity_id")
        if isinstance(eid, str) and eid.endswith(suffix):
            try:
                api.configured_entities.remove(eid)
            except (KeyError, ValueError, RuntimeError):
                pass

    for e in list(api.available_entities.get_all()):
        eid = e.get("entity_id")
        if isinstance(eid, str) and eid.endswith(suffix):
            try:
                api.available_entities.remove(eid)
            except (KeyError, ValueError, RuntimeError):
                pass

async def _async_remove(device: TrinnovDevice) -> None:
    """Disconnect from receiver and remove all listeners."""
    _LOG.debug("Disconnecting and removing all listeners")
    device.events.remove_all_listeners()
    await device.disconnect()

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
