"""
Initial setup and configuration logic for Trinnov integration.

Handles user interaction, automatic discovery of Trinnov devices,
and device onboarding into the system.
"""

import asyncio
import logging

import config
import ucapi
from api import api
from device import TrinnovInfo
from discover import discover_trinnov_devices, TrinnovDeviceInfo
from registry import clear_devices

_LOG = logging.getLogger(__name__)

# Cache discovered devices by IP for the current setup session
_DISCOVERED_BY_IP: dict[str, object] = {}


def _txt(txt: dict[str, str] | None, key: str) -> str | None:
    """Get a TXT record value safely."""
    if not txt:
        return None
    v = txt.get(key)
    return v if v else None

def _fw_version(txt: dict[str, str] | None) -> str | None:
    """Return firmware version from either CI or legacy key."""
    return _txt(txt, "system_release") or _txt(txt, "version")

def _select_ip_form(devices_list: list[object]) -> ucapi.RequestUserInput:
    """Show dropdown of discovered Trinnov devices."""
    _LOG.critical("_select_ip_form %s", devices_list)
    _DISCOVERED_BY_IP.clear()

    dropdown_devices: list[dict] = []

    d: TrinnovDeviceInfo = None
    for d in devices_list:
        _DISCOVERED_BY_IP[d.ip] = d

        model = _txt(d.txt_records, "machine_class_name") or "Trinnov"
        hostname = d.hostname or "Unknown"

        label = f"{model} ({hostname} - {d.ip})"

        dropdown_devices.append(
            {
                "id": d.ip,
                "label": {
                    "en": label,
                    "de": label,
                    "fr": label,
                },
            }
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
                            "de": (
                                "Wählen Sie den Trinnov Prozessor aus, den Sie konfigurieren möchten."
                            ),
                            "fr": (
                                "Sélectionnez le processeur Trinnov que vous souhaitez configurer."
                            ),
                        }
                    }
                },
            },

            {
                "field": {
                    "dropdown": {
                        "value": dropdown_devices[0]["id"],
                        "items": dropdown_devices,
                    }
                },
                "id": "ip",
                "label": {
                    "en": "Device",
                    "de": "Gerät",
                    "fr": "Appareil",
                },
            },
        ],
    )

def _single_device_form(device: TrinnovDeviceInfo) -> ucapi.RequestUserInput:
    """Device info / review page."""
    version = _fw_version(device.txt_records)


    return ucapi.RequestUserInput(
        {
            "en": "Review Trinnov Device",
            "de": "Trinnov Gerät prüfen",
            "fr": "Vérifiez l'appareil Trinnov",
        },
        [
            {
                "id": "info",
                "label": {
                    "en": "Confirm device details",
                    "de": "Gerätedetails bestätigen",
                    "fr": "Confirmez les détails",
                },
                "field": {
                    "label": {
                        "value": {
                            "en": (
                                f"Please confirm this is the Trinnov you want to configure:\n\n"
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

            {
                "id": "ip",
                "label": {"en": "IP Address:"},
                "field": {"text": {"value": device.ip}},
            },
            {
                "id": "port",
                "label": {"en": "TCP Port:"},
                "field": {"number": {"value": device.port}},
            },
            {
                "id": "hostname",
                "label": {"en": "Hostname:"},
                "field": {"text": {"value": device.hostname}},
            },
            {
                "id": "mac",
                "label": {"en": "Mac Address:"},
                "field": {"text": {"value": _txt(device.txt_records, "id") or "Unknown"}},
            },
            {
                "id": "model",
                "label": {"en": "Model:"},
                "field": {"text": {"value": _txt(device.txt_records, "machine_class_name") or "Unknown"}},
            },
            {
                "id": "version",
                "label": {"en": "Firmware Version:"},
                "field": {"text": {"value": version or "Unknown"}},
            },
            {
                "id": "srpid",
                "label": {"en": "SRPID:"},
                "field": {"text": {"value": _txt(device.txt_records, "srpid") or "Unknown"}},
            },
        ],
    )

async def _confirm_device_form(msg: ucapi.UserDataResponse) -> ucapi.RequestUserInput:
    """Second page: confirm the chosen device details (populated from discovery)."""
    _LOG.critical("_confirm_device_form %s", msg)
    return ucapi.RequestUserInput(
        {"en": "Confirm Trinnov"},
        [
            {
                "id": "ip",
                "label": {"en": "Using Trinnov IP Address:"},
                "field": {"text": {"value": msg.input_values.get("ip")}},
            },
            {
                "id": "hostname",
                "label": {"en": "Hostname:"},
                "field": {"text": {"value": msg.input_values.get("name")}},
            },
            {
                "id": "mac",
                "label": {"en": "Mac Address:"},
                "field": {"text": {"value": msg.input_values.get("mac")}},
            },
            {
                "id": "model",
                "label": {"en": "Model:"},
                "field": {"text": {"value": msg.input_values.get("model")}},
            },
            {
                "id": "version",
                "label": {"en": "Firmware Version:"},
                "field": {"text": {"value": msg.input_values.get("version")}},
            },
            {
                "id": "srpid",
                "label": {"en": "SRPID:"},
                "field": {"text": {"value": msg.input_values.get("srpid")}},
            },
        ],
    )

async def driver_setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
    """Main entry point for setup-related UCAPI messages."""
    _LOG.critical("driver setup handler %s", msg)
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
    _LOG.critical("handle driver setup %s", msg)
    if msg.reconfigure:
        _LOG.info("Starting reconfiguration")
        return _reconfigure_menu_form()

    api.available_entities.clear()
    api.configured_entities.clear()

    devices_list = await asyncio.to_thread(discover_trinnov_devices)
    if not devices_list:
        return ucapi.SetupError()

    if len(devices_list) > 1:
        return _select_ip_form(devices_list)

    device = devices_list[0]
    _LOG.info("Using Trinnov ip: %s, port: %i", device.ip, device.port)
    return _single_device_form(device)

async def handle_user_data_response(msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
    """Handle the user's submitted data and complete setup."""
    # If mac is missing, this is the multi-device selection page (dropdown).
    # Next page should be the device info page (prefilled), NOT SetupComplete.

    # Reconfigure menu submit
    if "action" in msg.input_values:
        action = msg.input_values.get("action")
        choice = msg.input_values.get("choice", "")

        if action == "add":
            devices_list = await asyncio.to_thread(discover_trinnov_devices)
            if not devices_list:
                return ucapi.SetupError()
            if len(devices_list) > 1:
                return _select_ip_form(devices_list)
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

    if not msg.input_values.get("mac"):
        ip = msg.input_values.get("ip")

        device = _DISCOVERED_BY_IP.get(ip)
        if not device:
            _LOG.error("Selected IP %r not in discovered devices", ip)
            return ucapi.SetupError()

        _LOG.info("Selected Trinnov from dropdown: %s", ip)
        return _single_device_form(device)

    fields = ("srpid", "ip", "model", "mac", "version")
    srpid, ip, model, mac, version = (msg.input_values.get(k, "Unknown") for k in fields)
    name = f"Trinnov {model}"

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
    # Build configured device dropdown
    dropdown_devices: list[dict] = []
    for d in config.devices:
        dropdown_devices.append(
            {
                "id": d.id,
                "label": {"en": f"{d.name} ({d.ip})"},
            }
        )

    dropdown_actions: list[dict] = [
        {"id": "add", "label": {"en": "Add a new device"}},
    ]

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
