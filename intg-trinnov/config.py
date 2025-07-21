"""
Handles device configuration management and entity-to-device mapping utilities.

Features:
- `_EnhancedJSONEncoder`: Custom JSON encoder for serializing dataclass instances.
- `Devices`: Manager class for:
    - Adding, removing, and updating device entries.
    - Persistent file-backed storage with automatic saves.
    - Configurable storage path for device configs.
    - Device lookup, containment checks, and iteration support.
    - Logging for operational visibility.
- `extract_device_id()`: Utility to extract device ID from an entity object.

This module supports Trinnov device integration in UC API-based environments.
"""


import dataclasses
import json
import logging
import os
from json import JSONDecodeError
from typing import Iterator

import ucapi
from device import TrinnovInfo

_LOG = logging.getLogger(__name__)


def extract_device_id(entity: ucapi.Entity) -> str:
    """
    Extract the device ID from an entity object.

    Args:
        entity: An entity instance (e.g., Remote, MediaPlayer).

    Returns:
        The device ID portion from the entity's ID (e.g., "device123" from "remote.device123").
    """
    return entity.id.split(".", 1)[1]


class _EnhancedJSONEncoder(json.JSONEncoder): # pylint: disable=too-few-public-methods
    """Python dataclass JSON encoder."""

    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


class Devices:
    """Integration driver configuration class. Manages all configured Trinnov devices."""

    def __init__(
            self,
            data_path: str,
            add_handler=None,
            remove_handler=None,
            cfg_filename: str = "config.json"
        ) -> None:
        self._data_path: str = data_path
        self._cfg_file_path: str = os.path.join(data_path, cfg_filename)
        self._config: list[TrinnovInfo] = []
        self._add_handler = add_handler
        self._remove_handler = remove_handler

        self.load()

    def add(self, trinnov: TrinnovInfo) -> None:
        """Add a new configured Trinnov device, ignoring duplicates by ID."""
        if any(d.id == trinnov.id for d in self._config):
            _LOG.warning("Device with id '%s' already exists.", trinnov.id)
            return
        self._config.append(trinnov)
        if self._add_handler:
            self._add_handler(trinnov)
        self.store()
        _LOG.info("Device with id '%s' added and stored.", trinnov.id)

    def remove(self, device_id: str) -> bool:
        """Remove a device from the configuration by its ID."""
        for i, device in enumerate(self._config):
            if device.id == device_id:
                removed_device = self._config.pop(i)
                if self._remove_handler:
                    self._remove_handler(removed_device)
                self.store()
                _LOG.info("Device with id '%s' removed and changes stored.", device_id)
                return True
        _LOG.warning("Device with id '%s' not found for removal.", device_id)
        return False

    def contains(self, device_id: str) -> bool:
        """Check if a device with the given ID exists in the configuration."""
        return any(d.id == device_id for d in self._config)

    def get(self, device_id: str) -> TrinnovInfo | None:
        """Retrieve a device by ID, or None if not found."""
        for device in self._config:
            if device.id == device_id:
                return device
        return None

    def update(self, updated: TrinnovInfo) -> bool:
        """Update an existing device by matching ID. Returns True if updated."""
        for i, device in enumerate(self._config):
            if device.id == updated.id:
                self._config[i] = updated
                self.store()
                _LOG.info("Device with id '%s' was updated and stored.", updated.id)
                return True
        _LOG.warning("Device with id '%s' not found for update.",updated.id)
        return False

    def clear(self) -> None:
        """Remove all device configurations and delete the configuration file."""
        self._config.clear()

        if os.path.exists(self._cfg_file_path):
            os.remove(self._cfg_file_path)

        if self._remove_handler:
            self._remove_handler(None)
        _LOG.info("All devices cleared and config file removed.")

    def _read_config_file(self) -> list[dict]:
        """Reads and returns JSON data from the config file."""
        with open(self._cfg_file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_config_file(self, data: list[TrinnovInfo]) -> None:
        """Writes JSON data to the config file."""
        with open(self._cfg_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, cls=_EnhancedJSONEncoder)

    def load(self) -> bool:
        """
        Load the config into the internal list.

        :return: True if the configuration could be loaded.
        """
        try:
            data = self._read_config_file()

            for item in data:
                if not all(k in item for k in (
                    "id",
                    "name",
                    "ip",
                    "mac"
                )):
                    _LOG.warning("Skipping invalid config item: %s",  item)
                    continue
                try:
                    trinnov = TrinnovInfo(**item)
                except TypeError as e:
                    _LOG.warning("Invalid device format: %s (%s)", item, e)
                    continue
                self._config.append(trinnov)
            return True

        except FileNotFoundError:
            _LOG.info("No config file found at %s. Starting with an empty configuration.",
                      self._cfg_file_path)
        except JSONDecodeError:
            _LOG.error("Config file is present but contains invalid JSON.")
        except OSError:
            _LOG.exception("Cannot open the config file")
        except ValueError:
            _LOG.exception("Empty or invalid config file")

        return False

    def store(self) -> bool:
        """
        Store the configuration file.

        :return: True if the configuration could be saved.
        """
        try:
            self._write_config_file(self._config)
            return True
        except OSError:
            _LOG.exception("Cannot write the config file")

        return False

    def __iter__(self) -> Iterator[TrinnovInfo]:
        """Allow iteration directly on the Devices instance."""
        return iter(self._config)


devices: Devices | None = None
