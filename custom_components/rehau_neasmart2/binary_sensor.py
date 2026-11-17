"""SYSBUS connectivity (fresh data from the base)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RehauConfigEntry
from .entity import RehauEntity, hub_device
from .hub import RehauHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RehauConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RehauConnection(entry.runtime_data)])


class RehauConnection(RehauEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "sysbus"

    def __init__(self, hub: RehauHub) -> None:
        super().__init__(hub, f"{hub.id}_sysbus_connection", hub_device(hub))

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.hub.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        age = self.hub.bank.age()
        return {
            "converter_connected": self.hub.server.connected,
            "port": self.hub.port,
            "seconds_since_last_data": None if age is None else round(age, 1),
        }
