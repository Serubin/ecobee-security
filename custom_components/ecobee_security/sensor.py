"""Diagnostic readouts of the account's own monitoring settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EcobeeSecurityCoordinator
from .entity import EcobeeSecurityEntity
from .model import Snapshot


@dataclass(frozen=True, kw_only=True)
class EcobeeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Snapshot], int | datetime | None]


SENSORS: tuple[EcobeeSensorDescription, ...] = (
    EcobeeSensorDescription(
        key="exit_delay_away",
        translation_key="exit_delay_away",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.away.exit_delay if snap.away else None,
    ),
    EcobeeSensorDescription(
        key="exit_delay_stay",
        translation_key="exit_delay_stay",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.stay.exit_delay if snap.stay else None,
    ),
    EcobeeSensorDescription(
        key="entry_delay_away",
        translation_key="entry_delay_away",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.away.entry_delay if snap.away else None,
    ),
    EcobeeSensorDescription(
        key="siren_duration_away",
        translation_key="siren_duration_away",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.away.siren_duration if snap.away else None,
    ),
    EcobeeSensorDescription(
        key="delayed_until",
        translation_key="delayed_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.delayed_until,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EcobeeSecurityCoordinator = entry.runtime_data
    async_add_entities(EcobeeSensor(coordinator, desc) for desc in SENSORS)


class EcobeeSensor(EcobeeSecurityEntity, SensorEntity):
    entity_description: EcobeeSensorDescription

    def __init__(
        self, coordinator: EcobeeSecurityCoordinator, description: EcobeeSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | datetime | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
