"""Global operating mode and state."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RehauConfigEntry
from .const import (
    PRESET_CLIMATE_MODES_MAPPING,
    PRESET_CLIMATE_MODES_MAPPING_REVERSE,
    PRESET_STATES_MAPPING,
    PRESET_STATES_MAPPING_REVERSE,
    REG_GLOBAL_MODE,
    REG_GLOBAL_STATE,
)
from .entity import RehauEntity, hub_device
from .hub import RehauHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RehauConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        [
            RehauGlobalSelect(
                hub,
                "global_climate_mode",
                REG_GLOBAL_MODE,
                PRESET_CLIMATE_MODES_MAPPING,
                PRESET_CLIMATE_MODES_MAPPING_REVERSE,
            ),
            RehauGlobalSelect(
                hub,
                "global_climate_state",
                REG_GLOBAL_STATE,
                PRESET_STATES_MAPPING,
                PRESET_STATES_MAPPING_REVERSE,
            ),
        ]
    )


class RehauGlobalSelect(RehauEntity, SelectEntity):
    """Options are the English values of v0.3 (used in existing scripts and templates)."""

    def __init__(
        self,
        hub: RehauHub,
        key: str,
        reg: int,
        mapping: dict[str, int],
        reverse: dict[int, str],
    ) -> None:
        super().__init__(hub, f"{hub.id}_{key}", hub_device(hub))
        self._attr_translation_key = key
        self._key = key
        self._reg = reg
        self._mapping = mapping
        self._reverse = reverse
        self._attr_options = list(mapping)

    @property
    def available(self) -> bool:
        # The base hardly ever republishes these registers: a live bus is enough.
        return self.hub.available

    @property
    def current_option(self) -> str | None:
        return self._reverse.get(self.hub.raw(self._reg) or 0)

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_command(self._reg, self._mapping[option], self._key)
