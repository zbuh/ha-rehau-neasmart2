"""Common entity base; devices keep the identifiers used by v0.3."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE
from .hub import RehauHub, Zone

MANUFACTURER = "Rehau"


def hub_device(hub: RehauHub) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, hub.id)},
        name=hub.name,
        manufacturer=MANUFACTURER,
        model="NEA SMART 2.0 Base",
    )


def zone_device(hub: RehauHub, zone: Zone) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{hub.id}_{zone.base_id}_{zone.zone_id}")},
        name=zone.name,
        manufacturer=MANUFACTURER,
        model="NEA SMART 2.0 Room Thermostat",
    )


def mixed_group_device(hub: RehauHub, group: int) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{hub.id}_{group}")},
        name=f"Mixed Group #{group}",
        manufacturer=MANUFACTURER,
        model="Mixed Group w/ 24/230 Pump and 0-10v controlled mixing valve",
    )


class RehauEntity(Entity):
    """Push entity: refreshed when the hub signals a change (0.5 s debounce)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _regs: tuple[int, ...] = ()  # registers that must hold a value for availability

    def __init__(self, hub: RehauHub, unique_id: str, device: DeviceInfo) -> None:
        self.hub = hub
        self._attr_unique_id = unique_id
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        return self.hub.available and all(self.hub.raw(r) is not None for r in self._regs)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_UPDATE.format(self.hub.entry.entry_id), self.async_write_ha_state
            )
        )
