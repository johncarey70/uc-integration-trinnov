"""
Registry for active TrinnovDevice instances.

Used to store and retrieve device connections by device ID.
"""

from typing import Dict, Iterator

from device import TrinnovDevice

_configured_trinnovs: Dict[str, TrinnovDevice] = {}


def get_device(device_id: str) -> TrinnovDevice | None:
    """
    Retrieve the device associated with a given device ID.

    Args:
        device_id: Unique identifier for the Trinnov device.

    Returns:
        The corresponding TrinnovDevice instance, or None if not found.
    """
    return _configured_trinnovs.get(device_id)


def register_device(device_id: str, device: TrinnovDevice) -> None:
    """
    Register a TrinnovDevice for a given device ID.

    Args:
        device_id: Unique identifier for the Trinnov device.
        device: TrinnovDevice instance to associate with the device.
    """
    if device_id not in _configured_trinnovs:
        _configured_trinnovs[device_id] = device


def unregister_device(device_id: str) -> None:
    """
    Remove the device associated with the given device ID.

    Args:
        device_id: Unique identifier of the device to remove.
    """
    _configured_trinnovs.pop(device_id, None)


def all_devices() -> Dict[str, TrinnovDevice]:
    """
    Get a dictionary of all currently registered devices.

    Returns:
        A dictionary mapping device IDs to their TrinnovDevice instances.
    """
    return _configured_trinnovs


def clear_devices() -> None:
    """
    Remove all registered devicess from the registry.
    """
    _configured_trinnovs.clear()


async def connect_all() -> None:
    """
    Connect all registered TrinnovDevice instances asynchronously.
    """
    for device in iter_devices():
        await device.connect()


async def disconnect_all() -> None:
    """
    Disconnect all registered TrinnovDevice instances asynchronously.
    """
    for device in iter_devices():
        device._was_intentional_disconnect = True
        await device.disconnect()


def iter_devices() -> Iterator[TrinnovDevice]:
    """
    Yield each registered TrinnovDevice instance.

    Returns:
        An iterator over all registered device objects.
    """
    return iter(_configured_trinnovs.values())
