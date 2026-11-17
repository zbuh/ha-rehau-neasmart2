"""Constants for the Rehau NEA SMART 2.0 integration (direct SYSBUS, no add-on)."""

from __future__ import annotations

DOMAIN = "rehau_neasmart2"

# --- configuration --------------------------------------------------------
CONF_NAME = "climate_system_name"
CONF_LISTEN_PORT = "listen_port"
CONF_ZONES = "zones"
CONF_MIXED_GROUPS = "mixed_groups"
CONF_PUMPS = "pumps_regs_mapping"
CONF_DEHUMIDIFIERS = "dehumidificators_regs_mapping"  # key kept from v0.3 (sic)
CONF_STALE_AFTER = "stale_after_seconds"
CONF_RESTORE_GLOBALS = "restore_globals"

DEFAULT_NAME = "rehau"
DEFAULT_LISTEN_PORT = 502
# When migrating from the add-on, the add-on still owns port 502.
MIGRATION_LISTEN_PORT = 5020
DEFAULT_STALE_AFTER = 180
DEFAULT_RESTORE_GLOBALS = False
# The base reads every command register about every 2 seconds.
COMMAND_CONFIRM_TIMEOUT = 15
SAVE_DELAY = 60
DISCOVERY_MAX_SECONDS = 90
DISCOVERY_SETTLE_SECONDS = 30  # keep listening this long after the first broadcast

# --- protocol (REHAU "Gateway KNX NEA SMART 2.0" datapoint list) -----------
GATEWAY_UNITS = frozenset({240, 241})
BROADCAST_UNIT = 0

MAX_BASES = 5
MAX_ZONES_PER_BASE = 12
MAX_ZONES = MAX_BASES * MAX_ZONES_PER_BASE

REG_GLOBAL_MODE = 1
REG_GLOBAL_STATE = 2
# The base writes 3, 4 and 5 and never 6, although the manual lists 3/5/6.
REG_ERRORS = 3
REG_WARNINGS = 4
REG_HINTS = 5
REG_OUTSIDE_TEMP = 7
REG_OUTSIDE_TEMP_FILTERED = 8
MIXED_GROUP_BASE_REG = {1: 10, 2: 14, 3: 18}  # +0 valve %, +1 pump, +2 flow, +3 return
MAX_MIXED_GROUPS = 3
DEHUMIDIFIER_BASE_REG = 21  # +1..+9
MAX_DEHUMIDIFIERS = 9
PUMP_BASE_REG = 30  # +1..+5
MAX_PUMPS = 5

ZONE_STATE = 0
ZONE_SETPOINT = 1
ZONE_TEMPERATURE = 2
ZONE_UNKNOWN_3 = 3  # written by the base, undocumented (always 0 so far)
ZONE_HUMIDITY = 10


def zone_base_reg(base_id: int, zone_id: int) -> int:
    """Register N of a zone: N = (base - 1) * 1200 + zone * 100."""
    return (base_id - 1) * MAX_ZONES_PER_BASE * 100 + zone_id * 100


# --- values (kept identical to v0.3 for automation compatibility) ----------
PRESET_STATES_MAPPING = {
    "Normal": 1,
    "Reduced": 2,
    "Standby": 3,
    "Time Program": 4,
    "Party": 5,
    "Absence": 6,
}
PRESET_STATES_MAPPING_REVERSE = {v: k for k, v in PRESET_STATES_MAPPING.items()}
PRESET_CLIMATE_MODES_MAPPING = {
    "Auto": 1,
    "Heating": 2,
    "Cooling": 3,
    "Forced Heating": 4,
    "Forced Cooling": 5,
}
PRESET_CLIMATE_MODES_MAPPING_REVERSE = {v: k for k, v in PRESET_CLIMATE_MODES_MAPPING.items()}
BINARY_STATUSES = {0: "Off", 1: "On"}
PRESENCE_STATES = {True: "Present", False: "Not Present"}

SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
