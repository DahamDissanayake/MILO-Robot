"""Registers the robot's ``_milo-robot._tcp`` mDNS service so brains on the
LAN can discover it -- direction-swapped mirror of
``brain/milo_brain/server.py``'s ``Advertiser`` (which used to do this for
the brain; now the robot is the one being discovered).

Always started the moment the robot's WS server starts (see
``RobotServer.serve_forever()``) -- this is what lets an already-paired
brain reconnect automatically in the background without anyone touching
the webapp again. ``pairing`` is just a togglable TXT flag on top of that
always-on service, flipped by ``PairingController`` -- the extra signal
that makes a *new*, unpaired brain discoverable/pairable too.
"""

from __future__ import annotations

import socket as socketlib

SERVICE_TYPE = "_milo-robot._tcp.local."


def _local_ip() -> str:
    """Best-effort real LAN IP to advertise to brains.

    Same reasoning as the brain's identical helper: ``gethostbyname(gethostname())``
    can resolve to a virtual adapter (VPN/WSL/VirtualBox) instead of the
    real LAN one on a multi-homed machine. Connecting a UDP socket sends no
    packets -- it only asks the OS routing table which local interface
    would reach the destination.
    """
    with socketlib.socket(socketlib.AF_INET, socketlib.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return socketlib.gethostbyname(socketlib.gethostname())


class RobotAdvertiser:
    """Registers/updates the ``_milo-robot._tcp`` mDNS service."""

    def __init__(self, cfg):
        self._cfg = cfg
        self._zc = None
        self._info = None
        self.busy = False
        self.pairing = False
        self.advertised_ip = ""

    def _service_info(self):
        from zeroconf import ServiceInfo

        props = {
            "id": self._cfg.robot_id,
            "name": self._cfg.robot_name,
            "busy": "1" if self.busy else "0",
            "pairing": "1" if self.pairing else "0",
        }
        host = _local_ip()
        self.advertised_ip = host
        return ServiceInfo(
            SERVICE_TYPE,
            f"{self._cfg.robot_id}.{SERVICE_TYPE}",
            # zeroconf's own register path back-fills a missing `server` from
            # the instance name, but its update path doesn't -- and update()
            # below builds a fresh ServiceInfo every call, so it must be set
            # explicitly here (see brain/milo_brain/server.py's identical note).
            server=f"{self._cfg.robot_id}.local.",
            addresses=[socketlib.inet_aton(host)],
            port=self._cfg.robot_ws_port,
            properties=props,
        )

    def start(self) -> None:
        from zeroconf import InterfaceChoice, Zeroconf

        # Default restricts registration to interfaces with a default route
        # (the real LAN/WiFi link) instead of every adapter including ones
        # a brain could never reach (see brain/milo_brain/server.py's
        # identical note).
        self._zc = Zeroconf(interfaces=InterfaceChoice.Default)
        self._info = self._service_info()
        self._zc.register_service(self._info)

    def update(self, *, busy: bool | None = None, pairing: bool | None = None) -> None:
        if busy is not None:
            self.busy = busy
        if pairing is not None:
            self.pairing = pairing
        if self._zc is not None:
            new_info = self._service_info()
            self._zc.update_service(new_info)
            self._info = new_info

    def stop(self) -> None:
        if self._zc is not None:
            if self._info is not None:
                self._zc.unregister_service(self._info)
            self._zc.close()
            self._zc = None
