"""Sensors. Unique IDs and state values (Present/On/...) are the ones of v0.3."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import RehauConfigEntry, dpt9
from .const import (
    BINARY_STATUSES,
    DEHUMIDIFIER_BASE_REG,
    MIXED_GROUP_BASE_REG,
    PRESENCE_STATES,
    PUMP_BASE_REG,
    REG_ERRORS,
    REG_HINTS,
    REG_OUTSIDE_TEMP,
    REG_OUTSIDE_TEMP_FILTERED,
    REG_WARNINGS,
    ZONE_HUMIDITY,
    ZONE_TEMPERATURE,
    ZONE_UNKNOWN_3,
    zone_base_reg,
)
from .entity import RehauEntity, hub_device, mixed_group_device, zone_device
from .hub import RehauHub


def _percent(raw: int | None) -> int | None:
    return raw if raw is not None and 0 <= raw <= 100 else None


def _humidity(raw: int | None) -> int | None:
    return raw if raw and 0 < raw <= 100 else None


def _presence(raw: int | None) -> str | None:
    return None if raw is None else PRESENCE_STATES[raw != 0]


def _on_off(raw: int | None) -> str | None:
    return None if raw is None else BINARY_STATUSES[1 if raw else 0]


def _raw(raw: int | None) -> int | None:
    return raw


@dataclass(frozen=True, kw_only=True)
class RehauSensorDescription(SensorEntityDescription):
    """Sensor description with a register-value converter."""

    convert: Callable[[int | None], float | int | str | None]


def _temperature(key: str, translation_key: str) -> RehauSensorDescription:
    return RehauSensorDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        convert=dpt9.temperature,
    )


def _enum(key: str, translation_key: str, values: dict, convert, **kwargs) -> RehauSensorDescription:
    return RehauSensorDescription(
        key=key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.ENUM,
        options=list(values.values()),
        convert=convert,
        **kwargs,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RehauConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    hid = hub.id
    dev = hub_device(hub)
    entities: list[RehauSensor] = [
        RehauSensor(
            hub,
            f"{hid}_outside_temperature",
            dev,
            REG_OUTSIDE_TEMP,
            _temperature("outside", "outside_temperature"),
        ),
        RehauSensor(
            hub,
            f"{hid}_filtered_outside_temperature",
            dev,
            REG_OUTSIDE_TEMP_FILTERED,
            _temperature("outside_filtered", "filtered_outside_temperature"),
        ),
        RehauSensor(
            hub,
            f"{hid}_errors_presence",
            dev,
            REG_ERRORS,
            _enum("errors", "errors", PRESENCE_STATES, _presence),
        ),
        RehauSensor(
            hub,
            f"{hid}_warnings_presence",
            dev,
            REG_WARNINGS,
            _enum("warnings", "warnings", PRESENCE_STATES, _presence),
        ),
        RehauSensor(
            hub, f"{hid}_hints_presence", dev, REG_HINTS, _enum("hints", "hints", PRESENCE_STATES, _presence)
        ),
    ]

    for group in hub.mixed_groups:
        base = MIXED_GROUP_BASE_REG[group]
        gdev = mixed_group_device(hub, group)
        entities += [
            RehauSensor(
                hub,
                f"{hid}_{group}_valve_opening",
                gdev,
                base,
                RehauSensorDescription(
                    key="valve",
                    translation_key="valve_opening",
                    native_unit_of_measurement=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                    convert=_percent,
                ),
            ),
            RehauSensor(
                hub,
                f"{hid}_{group}_mixedgroup_pump_state",
                gdev,
                base + 1,
                _enum("mixed_group_pump", "pump_state", BINARY_STATUSES, _on_off),
            ),
            RehauSensor(
                hub,
                f"{hid}_{group}_mixedgroup_flow_temperature",
                gdev,
                base + 2,
                _temperature("flow", "flow_temperature"),
            ),
            RehauSensor(
                hub,
                f"{hid}_{group}_mixedgroup_return_temperature",
                gdev,
                base + 3,
                _temperature("return", "return_temperature"),
            ),
        ]

    for pump in hub.pumps:
        entities.append(
            RehauSensor(
                hub,
                f"{hid}_{pump}_extra_pump_state",
                dev,
                PUMP_BASE_REG + pump,
                _enum(
                    f"pump_{pump}",
                    "extra_pump_state",
                    BINARY_STATUSES,
                    _on_off,
                    translation_placeholders={"n": str(pump)},
                ),
            )
        )

    for dehumidifier in hub.dehumidifiers:
        entities.append(
            RehauSensor(
                hub,
                f"{hid}_{dehumidifier}_dehumidifier_state",
                dev,
                DEHUMIDIFIER_BASE_REG + dehumidifier,
                _enum(
                    f"dehumidifier_{dehumidifier}",
                    "dehumidifier_state",
                    BINARY_STATUSES,
                    _on_off,
                    translation_placeholders={"n": str(dehumidifier)},
                ),
            )
        )

    for zone in hub.zones:
        n = zone_base_reg(zone.base_id, zone.zone_id)
        zid = f"{hid}_{zone.base_id}_{zone.zone_id}"
        zdev = zone_device(hub, zone)
        entities += [
            RehauSensor(
                hub,
                f"{zid}_zone_temperature",
                zdev,
                n + ZONE_TEMPERATURE,
                _temperature("temperature", "zone_temperature"),
            ),
            RehauSensor(
                hub,
                f"{zid}_zone_humidity",
                zdev,
                n + ZONE_HUMIDITY,
                RehauSensorDescription(
                    key="humidity",
                    translation_key="zone_humidity",
                    device_class=SensorDeviceClass.HUMIDITY,
                    native_unit_of_measurement=PERCENTAGE,
                    state_class=SensorStateClass.MEASUREMENT,
                    convert=_humidity,
                ),
            ),
            # Undocumented register written by the base (always 0 so far): for research.
            RehauSensor(
                hub,
                f"{zid}_zone_register_3",
                zdev,
                n + ZONE_UNKNOWN_3,
                RehauSensorDescription(
                    key="register_3",
                    translation_key="zone_register_3",
                    entity_category=EntityCategory.DIAGNOSTIC,
                    entity_registry_enabled_default=False,
                    convert=_raw,
                ),
            ),
        ]

    async_add_entities(entities)


class RehauSensor(RehauEntity, SensorEntity):
    entity_description: RehauSensorDescription

    def __init__(
        self,
        hub: RehauHub,
        unique_id: str,
        device: DeviceInfo,
        reg: int,
        description: RehauSensorDescription,
    ) -> None:
        super().__init__(hub, unique_id, device)
        self.entity_description = description
        self._reg = reg
        self._regs = (reg,)

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.convert(self.hub.raw(self._reg))
