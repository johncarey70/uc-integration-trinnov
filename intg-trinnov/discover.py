#!/usr/bin/env python3

"""
Trinnov Altitude Network Discovery Tool

This script uses Zeroconf (mDNS) to discover a Trinnov Altitude device
on the local network that advertises the _trinnovtelnet._tcp.local. service.

Discovery stops as soon as one device is found.

Dependencies:
    pip install zeroconf
"""

import asyncio
import logging
import socket
import threading
from dataclasses import dataclass

from pytrinnov.models.base import ConfigStatus, EthernetStatus
from pytrinnov.models.constants import WS_CONFIG, WS_ETHERNET
from pytrinnov.trinnov.websocket import DeviceError, WebSocketClient
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

_LOG = logging.getLogger(__name__)

SERVICE_TYPES = ["_trinnovtelnet._tcp.local."]

@dataclass
class TrinnovDeviceInfo:
    """
    Represents a discovered Trinnov device with IP, port, hostname, and TXT records.
    """
    ip: str
    port: int
    hostname: str
    txt_records: dict[str, str]


class TrinnovListener(ServiceListener):
    """
    Zeroconf listener that captures and records the first Trinnov device found.
    """

    def __init__(self, zeroconf: Zeroconf, stop_event: threading.Event) -> None:
        self.found: list[TrinnovDeviceInfo] = []
        self.zeroconf = zeroconf
        self.stop_event = stop_event

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Not implemented (required abstract method)."""

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Not implemented (required abstract method)."""

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Called when a matching mDNS service is discovered."""
        if self.stop_event.is_set():
            return

        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return

        ip = socket.inet_ntoa(info.addresses[0])
        hostname = info.server.rstrip(".") if info.server else "unknown"

        txt: dict[str, str] = {}
        for k, v in info.properties.items():
            try:
                key = k.decode() if isinstance(k, bytes) else str(k)
                val = v.decode() if isinstance(v, bytes) else str(v)
                txt[key] = val
            except UnicodeDecodeError as ex:
                _LOG.debug("Failed to decode TXT record: %s", ex)
                continue

        _LOG.info("Found Trinnov: %s @ %s:%d", hostname, ip, info.port)
        if txt:
            _LOG.debug("  TXT Records: %s", txt)

        self.found.append(
            TrinnovDeviceInfo(ip=ip, port=info.port, hostname=hostname, txt_records=txt)
        )
        self.stop_event.set()
        self.zeroconf.close()


def discover_trinnov_devices(timeout: int = 5) -> list[TrinnovDeviceInfo]:
    """
    Perform mDNS discovery of Trinnov devices using Zeroconf.
    Stops as soon as the first device is found.

    Args:
        timeout: How long to wait for discovery if no device is found.

    Returns:
        A list containing one TrinnovDeviceInfo if found, else an empty list.
    """
    zeroconf = Zeroconf()
    stop_event = threading.Event()
    listener = TrinnovListener(zeroconf, stop_event)

    _LOG.info("Searching for Trinnov device via mDNS...")
    _ = [ServiceBrowser(zeroconf, stype, listener) for stype in SERVICE_TYPES]

    stop_event.wait(timeout=timeout)

    if not listener.found:
        _LOG.warning("No Trinnov device found via mDNS.")
    return listener.found


def get_hostname(ip_address: str) -> str:
    """
    Resolve the hostname for a given IP address using reverse DNS lookup.

    Args:
        ip_address (str): The IPv4 or IPv6 address to resolve.

    Returns:
        str: The resolved hostname, or None if resolution fails.
    """
    try:
        hostname, _, _ = socket.gethostbyaddr(ip_address)
        return hostname
    except socket.herror:
        return None


async def fetch_manual_device_info(ip: str) -> dict:
    """
    Fetch device info using WebSocket when manual IP/port are provided.

    Args:
        ip (str): Device IP address.
        port (int): Device WebSocket port.

    Returns:
        dict: Dictionary containing device information fields.
    """
    messages = [
        (WS_CONFIG, 1, None),
        (WS_ETHERNET, 1, None)
    ]

    try:
        async with WebSocketClient(ip) as client:
            responses = await client.send_and_receive(messages)

            config: ConfigStatus = responses.get(WS_CONFIG)
            _LOG.debug(ConfigStatus)
            ethernet: EthernetStatus = responses.get(WS_ETHERNET)
            _LOG.debug(ethernet)

            hostname = get_hostname(ip)

            return {
                "ip": ip,
                "mac": ethernet.macaddr if ethernet else None,
                "model": config.class_name if config else None,
                "version": config.release if config else None,
                "srpid": str(config.product_id) if config else None,
                "name": hostname if hostname else "Unknown",
            }

    except DeviceError as e:
        _LOG.error("WebSocket device info fetch failed: %s", e)
        return {}


async def main():
    """
    Asynchronously run discovery and print the Trinnov device found.
    """
    logging.basicConfig(level=logging.INFO)

    devices_list = await asyncio.to_thread(discover_trinnov_devices)
    if devices_list:
        device = devices_list[0]
        _LOG.info(device)

if __name__ == "__main__":
    asyncio.run(main())
