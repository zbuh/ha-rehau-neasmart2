"""Zone thermostats."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RehauConfigEntry, dpt9
from .const import (
    PRESET_STATES_MAPPING,
    PRESET_STATES_MAPPING_REVERSE,
    ZONE_HUMIDITY,
    ZONE_SETPOINT,
    ZONE_STATE,
    ZONE_TEMPERATURE,
    zone_base_reg,
)
from .entity import RehauEntity, zone_device
from .hub import RehauHub, Zone

# Home Assistant 2026.11 moved climate temperatures to `native_*` (the old attributes stop
# working in 2027.11). Support both so older installations keep working.
NATIVE_API = hasattr(ClimateEntity, "native_target_temperature")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RehauConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(RehauZoneClimate(hub, zone) for zone in hub.zones)


class RehauZoneClimate(RehauEntity, ClimateEntity):
    """Preset (local operating state), setpoint, temperature and humidity of a zone.

    Heating vs cooling is chosen globally (select entity), so the only HVAC mode is AUTO.
    """

    _attr_name = None  # the device name
    if NATIVE_API:
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    _attr_hvac_modes = [HVACMode.AUTO]
    _attr_hvac_mode = HVACMode.AUTO
    _attr_preset_modes = list(PRESET_STATES_MAPPING)
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(self, hub: RehauHub, zone: Zone) -> None:
        super().__init__(hub, f"{hub.id}_{zone.base_id}_{zone.zone_id}_thermostat", zone_device(hub, zone))
        self.zone = zone
        self._n = zone_base_reg(zone.base_id, zone.zone_id)
        self._regs = (self._n + ZONE_TEMPERATURE,)

    @property
    def preset_mode(self) -> str | None:
        return PRESET_STATES_MAPPING_REVERSE.get(self.hub.raw(self._n + ZONE_STATE) or 0)

    def _setpoint(self) -> float | None:
        # None in standby / outside the season (0x8116), as in v0.3.
        return dpt9.temperature(self.hub.raw(self._n + ZONE_SETPOINT))

    def _temperature(self) -> float | None:
        return dpt9.temperature(self.hub.raw(self._n + ZONE_TEMPERATURE))

    if NATIVE_API:

        @property
        def native_target_temperature(self) -> float | None:
            return self._setpoint()

        @property
        def native_current_temperature(self) -> float | None:
            return self._temperature()

    else:

        @property
        def target_temperature(self) -> float | None:
            return self._setpoint()

        @property
        def current_temperature(self) -> float | None:
            return self._temperature()

    @property
    def current_humidity(self) -> float | None:
        value = self.hub.raw(self._n + ZONE_HUMIDITY)
        return value if value and 0 < value <= 100 else None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.hub.async_command(
            self._n + ZONE_STATE, PRESET_STATES_MAPPING[preset_mode], f"{self.zone.name}: preset"
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.hub.async_command(
            self._n + ZONE_SETPOINT, dpt9.pack(float(temperature)), f"{self.zone.name}: setpoint"
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Only AUTO exists; heating/cooling is a global setting."""
