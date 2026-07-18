"""Standalone diagnostic: browses for the robot's mDNS advertisement,
independent of the milo-brain app. Run with the brain venv active:

    python brain/tools/mdns_probe.py

If this finds nothing while the robot is in pairing mode (or already
advertising, which it always does), the problem is local networking
(Windows Firewall / network profile / router multicast isolation), not
milo-brain's code -- since this script doesn't import milo_brain at all.
"""

from __future__ import annotations

import time

from zeroconf import Zeroconf, ServiceBrowser

SERVICE_TYPE = "_milo-robot._tcp.local."
BROWSE_SECONDS = 8


class _Listener:
    def __init__(self):
        self.found: list[tuple[str, object]] = []

    def add_service(self, zc, service_type, name):
        self.found.append((name, zc.get_service_info(service_type, name)))

    def update_service(self, zc, service_type, name):
        pass

    def remove_service(self, zc, service_type, name):
        pass


def main() -> None:
    print(f"Browsing for {SERVICE_TYPE} for {BROWSE_SECONDS} seconds...")
    zc = Zeroconf()
    listener = _Listener()
    ServiceBrowser(zc, SERVICE_TYPE, listener)
    time.sleep(BROWSE_SECONDS)
    zc.close()

    if not listener.found:
        print(
            "NO SERVICES FOUND -- likely Windows Firewall or network profile "
            "blocking mDNS multicast (see brain/README.md's Windows Firewall "
            "section), or a router/AP blocking multicast between wireless "
            "clients (client isolation)."
        )
        return

    for name, info in listener.found:
        print("FOUND:", name)
        if info is None:
            continue
        print("  addresses:", info.parsed_addresses())
        print("  port:", info.port)
        props = {}
        for k, v in (info.properties or {}).items():
            key = k.decode() if isinstance(k, bytes) else k
            value = v.decode() if isinstance(v, bytes) else v
            props[key] = value
        print("  props:", props)


if __name__ == "__main__":
    main()
