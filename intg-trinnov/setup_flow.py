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
from discover import discover_trinnov_devices, fetch_manual_device_info
from registry import clear_devices

_LOG = logging.getLogger(__name__)

async def driver_setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
    """
    Main entry point for handling all setup-related UCAPI messages.

    Args:
        msg (ucapi.SetupDriver): Message from UCAPI.

    Returns:
        ucapi.SetupAction: Action to take in response to the setup request.
    """

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
    """
    Handle initial setup or reconfiguration request from the user.

    Args:
        msg (ucapi.DriverSetupRequest): Setup message containing context and flags.

    Returns:
        ucapi.SetupAction: Action (form, complete, or error) based on discovery result.
    """

    if msg.reconfigure:
        _LOG.info("Starting reconfiguration")

    api.available_entities.clear()
    api.configured_entities.clear()

    if msg.setup_data.get("manual") == "true":
        _LOG.info("Entering manual setup settings")
        return _basic_input_form()

    devices_list = await asyncio.to_thread(discover_trinnov_devices)
    if not devices_list:
        return ucapi.SetupError()

    device = devices_list[0]
    _LOG.info("Using Trinnov ip: %s, port: %i", device.ip, device.port)

    return ucapi.RequestUserInput(
        {"en": "Discovered Trinnov"},
        [
            {
                "id": "ip",
                "label": {"en": "Discovered Trinnov at IP Address:"},
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
                "field": {"text": {"value": device.txt_records.get("id")}},
            },
            {
                "id": "model",
                "label": {"en": "Model:"},
                "field": {"text": {"value": device.txt_records.get("machine_class_name")}},
            },
            {
                "id": "version",
                "label": {"en": "Firmware Version:"},
                "field": {"text": {"value": device.txt_records.get("version")}},
            },
            {
                "id": "srpid",
                "label": {"en": "SRPID:"},
                "field": {"text": {"value": device.txt_records.get("srpid")}},
            },
        ]
    )

def _basic_input_form(ip: str = "192.168.15.30") -> ucapi.RequestUserInput:
    """
    Returns a form for manual configuration of IP and port.

    Args:
        ip (str): IP address to prepopulate. Default is empty.
        port (int): Port number to prepopulate. Default is TRINNOV_PORT.

    Returns:
        ucapi.RequestUserInput: Form requesting user input for IP and port.
    """
    return ucapi.RequestUserInput(
        {"en": "Manual Configuration"},
        [
            {
                "id": "ip",
                "label": {"en": "Enter Trinnov IP Address:"},
                "field": {"text": {"value": ip}}
            },
        ]
    )

async def _detailed_input_form(msg: ucapi.UserDataResponse) -> ucapi.RequestUserInput:
    """
    Return a detailed device information form after fetching via WebSocket.

    Args:
        device (TrinnovInfo): Device information object.

    Returns:
        ucapi.RequestUserInput: Form populated with device details.
    """

    return ucapi.RequestUserInput(
        {"en": "Manual Configuration"},
        [
            {
                "id": "ip",
                "label": {"en": "Using Trinnov IP Address:"},
                "field": {"text": {"value": msg.input_values.get("ip")}}
            },
            {
                "id": "hostname",
                "label": {"en": "Hostname:"},
                "field": {"text": {"value": msg.input_values.get("name")}}
            },
            {
                "id": "mac",
                "label": {"en": "Mac Address:"},
                "field": {"text": {"value": msg.input_values.get("mac")}}
            },
            {
                "id": "model",
                "label": {"en": "Model:"},
                "field": {"text": {"value": msg.input_values.get("model")}}
            },
            {
                "id": "version",
                "label": {"en": "Firmware Version:"},
                "field": {"text": {"value": msg.input_values.get("version")}}
            },
            {
                "id": "srpid",
                "label": {"en": "SRPID:"},
                "field": {"text": {"value": msg.input_values.get("srpid")}}
            },
        ]
    )

async def handle_user_data_response(msg: ucapi.UserDataResponse) -> ucapi.SetupAction:
    """
    Handle the user's submitted data from the input form and validate device.

    Args:
        msg (ucapi.UserDataResponse): Contains IP and port info submitted by the user.

    Returns:
        ucapi.SetupAction: Action signaling success or failure of setup.
    """

    if not msg.input_values.get("mac"):
        ip = msg.input_values.get("ip")
        txt_records = await fetch_manual_device_info(ip)

        if not txt_records:
            return ucapi.SetupError()

        msg.input_values.clear()
        msg.input_values.update({
            key: txt_records.get(key, "Unknown")
            for key in ("ip", "mac", "srpid", "model", "version", "name")
        })

        return await _detailed_input_form(msg)

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
        software_version=version
    )

    config.devices.add(dv_info)

    _LOG.info("Setup complete")
    return ucapi.SetupComplete()
