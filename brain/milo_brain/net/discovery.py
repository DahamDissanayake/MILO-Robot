"""Find MILO robots: browse mDNS ``_milo-robot._tcp`` and pick the best.

Direction-swapped mirror of the old bridge/milo_bridge/net/discovery.py's
BrainDiscovery/select_brain -- the robot is now the one advertising (see
bridge/milo_bridge/net/advertiser.py), the brain is the one browsing.

Selection policy:
    1. A manual_target (set by the Connect Robots tab) preempts everything
       else for exactly one match -- lets the user pick a specific robot
       among several without a second competing connector.
    2. Highest-priority *paired*, *not busy* robot wins.
    3. No usable paired robot, but an unpaired one is in pairing mode -> pair it.
    4. Otherwise none -> keep waiting.
"""

from __future__ import annotations

from dataclasses import dataclass

from milo_common.auth import PairedStore

SERVICE_TYPE = "_milo-robot._tcp.local."


@dataclass(frozen=True)
class RobotRecord:
    robot_id: str
    name: str
    host: str
    port: int
    busy: bool = False
    pairing: bool = False

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


def record_from_properties(
    host: str, port: int, properties: dict[bytes | str, bytes | str | None]
) -> RobotRecord | None:
    """Build a record from zeroconf TXT properties (bytes or str keys/values)."""
    props: dict[str, str] = {}
    for key, value in properties.items():
        if value is None:
            continue
        k = key.decode() if isinstance(key, bytes) else key
        props[k] = value.decode() if isinstance(value, bytes) else value
    if "id" not in props:
        return None
    return RobotRecord(
        robot_id=props["id"],
        name=props.get("name", props["id"]),
        host=host,
        port=port,
        busy=props.get("busy", "0") == "1",
        pairing=props.get("pairing", "0") == "1",
    )


def select_robot(
    records: list[RobotRecord], store: PairedStore, manual_target: str | None = None,
) -> tuple[RobotRecord, bool] | None:
    """Returns (record, needs_pairing) or None if there's nothing to connect to."""
    if manual_target is not None:
        match = next((r for r in records if r.robot_id == manual_target and not r.busy), None)
        if match is not None:
            return match, not store.is_paired(match.robot_id)
    usable = [r for r in records if store.is_paired(r.robot_id) and not r.busy]
    if usable:
        best = max(usable, key=lambda r: (store.priority_for(r.robot_id), r.name))
        return best, False
    pairable = [r for r in records if not store.is_paired(r.robot_id) and r.pairing]
    if pairable:
        return sorted(pairable, key=lambda r: r.name)[0], True
    return None


class RobotDiscovery:
    """Live view of advertised robots, fed by a zeroconf ServiceBrowser."""

    def __init__(self):
        self._records: dict[str, RobotRecord] = {}
        self._zc = None
        self._browser = None

    def snapshot(self) -> list[RobotRecord]:
        return list(self._records.values())

    # -- zeroconf listener interface ---------------------------------------
    def add_service(self, zc, service_type: str, service_name: str) -> None:
        info = zc.get_service_info(service_type, service_name)
        if info is None or not info.parsed_addresses():
            return
        record = record_from_properties(info.parsed_addresses()[0], info.port, info.properties)
        if record is not None:
            self._records[service_name] = record

    def update_service(self, zc, service_type: str, service_name: str) -> None:
        self.add_service(zc, service_type, service_name)

    def remove_service(self, zc, service_type: str, service_name: str) -> None:
        self._records.pop(service_name, None)

    # -----------------------------------------------------------------------
    def start(self) -> None:
        from zeroconf import ServiceBrowser, Zeroconf

        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self)

    def stop(self) -> None:
        if self._zc is not None:
            self._zc.close()
            self._zc = None
            self._browser = None
