#!/usr/bin/env python3
"""Discover Trinnov Altitude devices via Zeroconf."""

import asyncio
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

_LOG = logging.getLogger(__name__)

SERVICE_TYPES = ["_trinnovtelnet._tcp.local."]


@dataclass(frozen=True)
class TrinnovDeviceInfo:
    """Represents a discovered Trinnov device."""
    ip: str
    port: int
    hostname: str
    txt_records: dict[str, str]


class TrinnovListener(ServiceListener):
    """Collects all discovered Trinnov devices."""

    def __init__(self) -> None:
        """Initialize listener state."""
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str, int]] = set()
        self.found: list[TrinnovDeviceInfo] = []

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Ignore service removal events."""
        return

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Handle service updates as adds."""
        self.add_service(zc, type_, name)

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Process a newly discovered service."""
        info = zc.get_service_info(type_, name)
        if not info or not info.addresses:
            return

        ip = None
        for addr in info.addresses:
            if len(addr) == 4:
                ip = socket.inet_ntoa(addr)
                break

        if ip is None:
            return

        hostname = info.server.rstrip(".") if info.server else "unknown"

        txt: dict[str, str] = {}
        for k, v in info.properties.items():
            try:
                key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
                val = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
                txt[key] = val
            except UnicodeDecodeError:
                continue

        key = (name, ip, info.port)

        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            self.found.append(
                TrinnovDeviceInfo(
                    ip=ip,
                    port=info.port,
                    hostname=hostname,
                    txt_records=txt,
                )
            )

        _LOG.info("Found Trinnov: %s @ %s:%d", hostname, ip, info.port)


def discover_trinnov_devices(
    timeout: float = 5.0,
    settle_time: float = 0.25,
) -> list[TrinnovDeviceInfo]:
    """Discover Trinnov devices and exit early once discovery settles."""
    zeroconf = Zeroconf()
    listener = TrinnovListener()

    _LOG.info("Searching for Trinnov devices via mDNS...")

    browsers = [ServiceBrowser(zeroconf, stype, listener) for stype in SERVICE_TYPES]

    start = time.monotonic()
    last_count = 0
    last_change = start

    try:
        while True:
            now = time.monotonic()
            count = len(listener.found)

            # Track when device count changes
            if count != last_count:
                last_count = count
                last_change = now

            # Exit when discovery has settled
            if count > 0 and (now - last_change) >= settle_time:
                break

            # Exit on hard timeout
            if now - start >= timeout:
                break

            time.sleep(0.05)

    finally:
        for b in browsers:
            try:
                b.cancel()
            except (RuntimeError, AttributeError) as err:
                _LOG.debug("Ignoring browser cancel failure: %s", err)
        zeroconf.close()

    return listener.found

def devices_to_json(devices: list[TrinnovDeviceInfo]) -> str:
    """Return discovered devices as pretty-printed JSON."""
    return json.dumps(
        [
            {
                "ip": d.ip,
                "port": d.port,
                "hostname": d.hostname,
                "txt_records": d.txt_records,
            }
            for d in devices
        ],
        indent=4,
        sort_keys=True,
    )

async def main() -> None:
    """Run discovery and log results."""
    logging.basicConfig(level=logging.INFO)

    devices = await asyncio.to_thread(
        discover_trinnov_devices,
        timeout=5.0,
        settle_time=0.25,
    )

    _LOG.info("Discovered devices:\n%s", devices_to_json(devices))



if __name__ == "__main__":
    asyncio.run(main())
