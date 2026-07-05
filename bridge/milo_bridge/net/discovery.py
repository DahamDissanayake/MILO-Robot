"""Find Milo Brain machines: browse mDNS ``_milo-brain._tcp`` and pick the best.

Selection policy (spec §6 + G.1):
    1. Highest-priority *paired*, *not busy* brain wins.
    2. No usable paired brain, but an unpaired one is in pairing mode -> pair it.
    3. Otherwise none -> Milo sleeps (paired-but-busy does not count).
"""

from __future__ import annotations

from dataclasses import dataclass

from milo_common.auth import PairedStore

SERVICE_TYPE = "_milo-brain._tcp.local."


@dataclass(frozen=True)
class BrainRecord:
    brain_id: str
    name: str
    host: str
    port: int
    tier: str = "small"
    busy: bool = False
    pairing: bool = False

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


def record_from_properties(
    host: str, port: int, properties: dict[bytes | str, bytes | str | None]
) -> BrainRecord | None:
    """Build a record from zeroconf TXT properties (bytes or str keys/values)."""
    props: dict[str, str] = {}
    for key, value in properties.items():
        if value is None:
            continue
        k = key.decode() if isinstance(key, bytes) else key
        props[k] = value.decode() if isinstance(value, bytes) else value
    if "id" not in props:
        return None
    return BrainRecord(
        brain_id=props["id"],
        name=props.get("name", props["id"]),
        host=host,
        port=port,
        tier=props.get("tier", "small"),
        busy=props.get("busy", "0") == "1",
        pairing=props.get("pairing", "0") == "1",
    )


def select_brain(
    records: list[BrainRecord], store: PairedStore
) -> tuple[BrainRecord, bool] | None:
    """Returns (record, needs_pairing) or None if Milo should sleep."""
    usable = [r for r in records if store.is_paired(r.brain_id) and not r.busy]
    if usable:
        best = max(usable, key=lambda r: (store.priority_for(r.brain_id), r.name))
        return best, False
    pairable = [r for r in records if not store.is_paired(r.brain_id) and r.pairing]
    if pairable:
        return sorted(pairable, key=lambda r: r.name)[0], True
    return None


class BrainDiscovery:
    """Live view of advertised brains, fed by a zeroconf ServiceBrowser."""

    def __init__(self):
        self._records: dict[str, BrainRecord] = {}
        self._zc = None
        self._browser = None

    def snapshot(self) -> list[BrainRecord]:
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
