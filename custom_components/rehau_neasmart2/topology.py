"""Configuration parsing and bus discovery (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import dpt9
from .bank import RegisterBank
from .const import (
    DEHUMIDIFIER_BASE_REG,
    MAX_BASES,
    MAX_DEHUMIDIFIERS,
    MAX_MIXED_GROUPS,
    MAX_PUMPS,
    MAX_ZONES,
    MAX_ZONES_PER_BASE,
    MIXED_GROUP_BASE_REG,
    PUMP_BASE_REG,
    ZONE_TEMPERATURE,
    zone_base_reg,
)


def parse_zone_entry(entry: str, position: int) -> tuple[int, int, str]:
    """Parse one `base.zone:name` entry, or a bare name (legacy positional form)."""
    entry = entry.strip()
    if not entry:
        raise ValueError("zone entries must include a name")

    if ":" not in entry:
        if not 0 <= position < MAX_ZONES:
            raise ValueError(f"zone position must be between 1 and {MAX_ZONES}")
        return (position // MAX_ZONES_PER_BASE) + 1, (position % MAX_ZONES_PER_BASE) + 1, entry

    address, name = entry.split(":", 1)
    if not name.strip():
        raise ValueError("explicit zone entries must include a name")
    parts = address.strip().split(".")
    if len(parts) != 2:
        raise ValueError("explicit zone address must use base.zone:name")
    try:
        base_id, zone_id = (int(part) for part in parts)
    except ValueError as err:
        raise ValueError("zone address must contain numeric base and zone") from err
    if not 1 <= base_id <= MAX_BASES:
        raise ValueError(f"zone base must be between 1 and {MAX_BASES}")
    if not 1 <= zone_id <= MAX_ZONES_PER_BASE:
        raise ValueError(f"zone number must be between 1 and {MAX_ZONES_PER_BASE}")
    return base_id, zone_id, name.strip()


def parse_zone_entries(zones: str) -> list[tuple[int, int, str]]:
    """Parse and validate a comma-separated zone list."""
    if not isinstance(zones, str) or not zones.strip():
        raise ValueError("at least one zone is required")
    entries = zones.split(",")
    if len(entries) > MAX_ZONES:
        raise ValueError(f"at most {MAX_ZONES} zones are supported")
    parsed = [parse_zone_entry(entry, i) for i, entry in enumerate(entries)]
    seen: set[tuple[int, int]] = set()
    for base_id, zone_id, _ in parsed:
        if (base_id, zone_id) in seen:
            raise ValueError(f"duplicate zone address {base_id}.{zone_id}")
        seen.add((base_id, zone_id))
    return parsed


def parse_index_list(value: str | None, maximum: int) -> list[int]:
    """'1,3' -> [1, 3], validating 1..maximum and duplicates."""
    if not value or not str(value).strip():
        return []
    out: list[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part.isdecimal() or not 1 <= int(part) <= maximum:
            raise ValueError(f"invalid index '{part}' (1-{maximum})")
        if int(part) in out:
            raise ValueError(f"duplicate index {part}")
        out.append(int(part))
    return out


@dataclass
class Detected:
    """What the base has been seen writing on the bus."""

    zones: list[tuple[int, int]] = field(default_factory=list)
    mixed_groups: list[int] = field(default_factory=list)
    pumps: list[int] = field(default_factory=list)
    dehumidifiers: list[int] = field(default_factory=list)

    def zones_config(self) -> str:
        return ",".join(f"{b}.{z}:Zone {b}.{z}" for b, z in self.zones)


def detect(bank: RegisterBank) -> Detected:
    """Infer the installation from the registers the base wrote.

    A zone is present when its temperature register carries a valid value; a mixed
    group when its flow temperature does; pumps and dehumidifiers when written at all
    (the base only broadcasts the ones that exist).
    """
    found = Detected()
    for base in range(1, MAX_BASES + 1):
        for zone in range(1, MAX_ZONES_PER_BASE + 1):
            reg = zone_base_reg(base, zone) + ZONE_TEMPERATURE
            if reg in bank.written and dpt9.temperature(bank.get(reg)) not in (None, 0.0):
                found.zones.append((base, zone))
    for group in range(1, MAX_MIXED_GROUPS + 1):
        reg = MIXED_GROUP_BASE_REG[group] + 2
        if reg in bank.written and dpt9.temperature(bank.get(reg)) not in (None, 0.0):
            found.mixed_groups.append(group)
    found.pumps = [p for p in range(1, MAX_PUMPS + 1) if PUMP_BASE_REG + p in bank.written]
    found.dehumidifiers = [
        d for d in range(1, MAX_DEHUMIDIFIERS + 1) if DEHUMIDIFIER_BASE_REG + d in bank.written
    ]
    return found
