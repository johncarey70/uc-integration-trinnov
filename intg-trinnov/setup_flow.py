"""
Initial setup and configuration logic for Trinnov integration.

Handles user interaction, automatic discovery of Trinnov devices,
and device onboarding into the system.
"""

# pylint: disable=too-many-return-statements
# pylint: disable=line-too-long

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
import ucapi
from api import api
from device import TrinnovInfo
from discover import TrinnovDeviceInfo, discover_trinnov_devices
from registry import clear_devices

_LOG = logging.getLogger(__name__)

# Cache discovered devices by SRPID for the current setup session.
_DISCOVERED_BY_SRPID: dict[str, TrinnovDeviceInfo] = {}


def _txt(txt: dict[str, str] | None, key: str) -> str | None:
    """Get a TXT record value safely."""
    if not txt:
        return None
    v = txt.get(key)
    return v if v else None


def _fw_version(txt: dict[str, str] | None) -> str | None:
    """Return firmware version from either CI or legacy key."""
    return _txt(txt, "system_release") or _txt(txt, "version")


def _srpid(device: TrinnovDeviceInfo) -> str:
    """Return SRPID (unique identifier) for a discovered device."""
    return _txt(device.txt_records, "srpid") or ""


def _select_device_form(devices_list: list[TrinnovDeviceInfo]) -> ucapi.RequestUserInput:
    """Show dropdown of discovered Trinnov devices (keyed by SRPID)."""
    _DISCOVERED_BY_SRPID.clear()

    configured_srpids = {d.id for d in config.devices if getattr(d, "id", None)}

    dropdown_devices: list[dict[str, Any]] = []
    for d in devices_list:
        srpid = _srpid(d)
        if not srpid:
            # Ignore devices without SRPID; we can't safely key them.
            continue
        if srpid in configured_srpids:
            # Reconfigure->Add: exclude already configured devices by SRPID.
            continue

        _DISCOVERED_BY_SRPID[srpid] = d

        model = _txt(d.txt_records, "machine_class_name") or "Trinnov"
        hostname = d.hostname or "Unknown"
        label = f"{model} ({hostname} - {d.ip})"

        dropdown_devices.append(
            {"id": srpid, "label": {"en": label, "de": label, "fr": label}}
        )

    if not dropdown_devices:
        return ucapi.RequestUserInput(
            {"en": "Select Trinnov Device"},
            [
                {
                    "id": "info",
                    "label": {"en": "Discovered Trinnov Devices"},
                    "field": {
                        "label": {
                            "value": {
                                "en": "No new Trinnov devices found (all discovered devices are already configured)."
                            }
                        }
                    },
                }
            ],
        )

    return ucapi.RequestUserInput(
        {
            "en": "Select Trinnov Device",
            "de": "Trinnov Gerät auswählen",
            "fr": "Sélectionnez l'appareil Trinnov",
        },
        [
            {
                "id": "info",
                "label": {
                    "en": "Discovered Trinnov Devices",
                    "de": "Gefundene Trinnov Geräte",
                    "fr": "Appareils Trinnov trouvés",
                },
                "field": {
                    "label": {
                        "value": {
                            "en": (
                                "Select the Trinnov processor you want to configure.\n\n"
                                "Hostname, model, and IP address are shown for identification."
                            ),
                            "de": "Wählen Sie den Trinnov Prozessor aus, den Sie konfigurieren möchten.",
                            "fr": "Sélectionnez le processeur Trinnov que vous souhaitez configurer.",
                        }
                    }
                },
            },
            {
                "field": {"dropdown": {"value": dropdown_devices[0]["id"], "items": dropdown_devices}},
                "id": "srpid",
                "label": {"en": "Device", "de": "Gerät", "fr": "Appareil"},
            },
        ],
    )


def _single_device_form(device: TrinnovDeviceInfo) -> ucapi.RequestUserInput:
    """Device info / review page."""
    version = _fw_version(device.txt_records)
    model = _txt(device.txt_records, "machine_class_name") or "Unknown"
    mac = _txt(device.txt_records, "id") or "Unknown"
    srpid = _srpid(device) or "Unknown"

    return ucapi.RequestUserInput(
        {"en": "Review Trinnov Device", "de": "Trinnov Gerät prüfen", "fr": "Vérifiez l'appareil Trinnov"},
        [
            {
                "id": "info",
                "label": {"en": "Confirm device details", "de": "Gerätedetails bestätigen", "fr": "Confirmez les détails"},
                "field": {
                    "label": {
                        "value": {
                            "en": (
                                "Please confirm this is the Trinnov you want to configure:\n\n"
                                "Click Next to finish setup."
                            ),
                            "de": (
                                "Bitte bestätigen Sie, dass dies der Trinnov ist, den Sie konfigurieren möchten.\n\n"
                                "Klicken Sie auf Weiter, um die Einrichtung abzuschließen."
                            ),
                            "fr": (
                                "Veuillez confirmer qu'il s'agit du Trinnov que vous souhaitez configurer.\n\n"
                                "Cliquez sur Suivant pour terminer la configuration."
                            ),
                        }
                    }
                },
            },
            {"id": "ip", "label": {"en": "IP Address:"}, "field": {"text": {"value": device.ip}}},
            {"id": "port", "label": {"en": "TCP Port:"}, "field": {"number": {"value": device.port}}},
            {"id": "hostname", "label": {"en": "Hostname:"}, "field": {"text": {"value": device.hostname}}},
            {"id": "mac", "label": {"en": "Mac Address:"}, "field": {"text": {"value": mac}}},
            {"id": "model", "label": {"en": "Model:"}, "field": {"text": {"value": model}}},
            {"id": "version", "label": {"en": "Firmware Version:"}, "field": {"text": {"value": version or "Unknown"}}},
            {"id": "srpid", "label": {"en": "SRPID:"}, "field": {"text": {"value": srpid}}},
        ],
    )


async def driver_setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
    """Main entry point for setup-related UCAPI messages."""
    if isinstance(msg, ucapi.DriverSetupRequest):
        return await handle_driver_setup(msg)
    if isinstance(msg, ucapi.UserDataResponse):
        return await handle_user_data_response(msg)
    if isinstance(msg, ucapi.AbortDriverSetup):
        _LOG.info("Setup was aborted with code: %s", msg.error)
        clear_devices()

    _LOG.error("Error during setup")
    return ucapi.SetupError()


async def handle_driver_setup(msg: ucapi.DriverSetupRequest) -> ucapi.SetupAction:
    """Handle initial setup or reconfiguration request from the user."""

    if msg.reconfigure:
        _LOG.info("Starting reconfiguration")
        return _reconfigure_menu_form()

    await asyncio.sleep(1)

    # Initial setup starts from scratch.
    api.available_entities.clear()
    api.configured_entities.clear()

    devices_list = await asyncio.to_thread(discover_trinnov_devices)
    if not devices_list:
        return ucapi.SetupError()

    if len(devices_list) > 1:
        return _select_device_form(devices_list)

    device = devices_list[0]
    _LOG.info("Using Trinnov ip: %s, port: %i", device.ip, device.port)
    return _single_device_form(device)


async def handle_user_data_response(msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
    """Handle the user's submitted data and complete setup.

    UCAPI may include stale values from prior pages in msg.input_values.
    Route by the most specific keys first:
      1) discovered-device selection (srpid-only page)
      2) reconfigure menu (action/choice)
      3) final review submit (port/mac/etc)
    """
    selected_srpid = msg.input_values.get("srpid")

    # 1) Discovered devices dropdown submit:
    # It has "srpid" and typically does NOT have "port".
    if selected_srpid and selected_srpid in _DISCOVERED_BY_SRPID and "port" not in msg.input_values:
        device = _DISCOVERED_BY_SRPID.get(selected_srpid)
        if not device:
            _LOG.error("Selected SRPID %r not in discovered devices", selected_srpid)
            return ucapi.SetupError()
        _LOG.info("Selected Trinnov from dropdown: %s", selected_srpid)
        return _single_device_form(device)

    # 2) Reconfigure menu submit (only when we're not selecting a discovered device)
    if "action" in msg.input_values and selected_srpid not in _DISCOVERED_BY_SRPID:
        action = msg.input_values.get("action")
        choice = msg.input_values.get("choice", "")

        if action == "add":
            devices_list = await asyncio.to_thread(discover_trinnov_devices)
            if not devices_list:
                return ucapi.SetupError()
            if len(devices_list) > 1:
                return _select_device_form(devices_list)
            return _single_device_form(devices_list[0])

        if action == "remove":
            if not choice:
                _LOG.error("Remove requested but no device selected")
                return ucapi.SetupError()
            if not config.devices.remove(choice):
                return ucapi.SetupError()
            return ucapi.SetupComplete()

        if action == "reset":
            config.devices.clear()
            return ucapi.SetupComplete()

        _LOG.error("Unknown configuration action: %s", action)
        return ucapi.SetupError()

    # 3) Final review/confirm submit:
    # Must include srpid + mac (and port is present on that page).
    if not msg.input_values.get("mac") or not selected_srpid:
        _LOG.error("Unexpected setup input_values: %s", msg.input_values)
        return ucapi.SetupError()

    fields = ("srpid", "ip", "model", "mac", "version")
    srpid, ip, model, mac, version = (msg.input_values.get(k, "Unknown") for k in fields)
    name = f"Trinnov {model}"

    # Initial setup clears entity registries; reconfigure does not.
    is_initial_setup = len(api.configured_entities.get_all()) == 0 and len(api.available_entities.get_all()) == 0
    if is_initial_setup:
        config.devices.clear()

    dv_info = TrinnovInfo(
        id=srpid,
        ip=ip,
        name=name,
        mac=mac,
        model_name=model,
        software_version=version,
    )
    config.devices.add(dv_info)

    _LOG.info("Setup complete")
    return ucapi.SetupComplete()


def _reconfigure_menu_form() -> ucapi.RequestUserInput:
    """Reconfigure flow: choose an existing configured device and an action."""
    dropdown_devices: list[dict[str, Any]] = [
        {"id": d.id, "label": {"en": f"{d.name} ({d.ip})"}} for d in config.devices
    ]

    dropdown_actions: list[dict[str, Any]] = [{"id": "add", "label": {"en": "Add a new device"}}]

    if dropdown_devices:
        dropdown_actions.extend(
            [
                {"id": "remove", "label": {"en": "Delete selected device"}},
                {"id": "reset", "label": {"en": "Remove ALL devices and start over"}},
            ]
        )
    else:
        dropdown_devices.append({"id": "", "label": {"en": "---"}})

    return ucapi.RequestUserInput(
        {"en": "Configuration mode"},
        [
            {
                "id": "info",
                "label": {"en": "Manage configured Trinnov devices"},
                "field": {
                    "label": {
                        "value": {
                            "en": (
                                "Choose an action.\n\n"
                                "Tip: Use 'Add a new device' to configure multiple Trinnov processors."
                            )
                        }
                    }
                },
            },
            {
                "field": {"dropdown": {"value": dropdown_devices[0]["id"], "items": dropdown_devices}},
                "id": "choice",
                "label": {"en": "Configured devices"},
            },
            {
                "field": {"dropdown": {"value": dropdown_actions[0]["id"], "items": dropdown_actions}},
                "id": "action",
                "label": {"en": "Action"},
            },
        ],
    )
