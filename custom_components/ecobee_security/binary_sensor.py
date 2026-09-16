"""Diagnostic flags from the account's monitoring settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EcobeeSecurityCoordinator
from .entity import EcobeeSecurityEntity
from .model import Snapshot


@dataclass(frozen=True, kw_only=True)
class EcobeeBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Snapshot], bool | None]


BINARY_SENSORS: tuple[EcobeeBinarySensorDescription, ...] = (
    EcobeeBinarySensorDescription(
        key="pro_monitoring",
        translation_key="pro_monitoring",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.pro_monitoring,
    ),
    # hasPinSetUp says a PIN exists, not that one is required to arm. Whether the server
    # enforces a PIN has never been tested; do not read this as a gate.
    EcobeeBinarySensorDescription(
        key="pin_configured",
        translation_key="pin_configured",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snap: snap.has_pin,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EcobeeSecurityCoordinator = entry.runtime_data
    async_add_entities(EcobeeBinarySensor(coordinator, desc) for desc in BINARY_SENSORS)


class EcobeeBinarySensor(EcobeeSecurityEntity, BinarySensorEntity):
    entity_description: EcobeeBinarySensorDescription

    def __init__(
        self,
        coordinator: EcobeeSecurityCoordinator,
        description: EcobeeBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
