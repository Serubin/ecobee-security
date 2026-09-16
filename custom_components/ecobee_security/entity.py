"""Shared device identity for everything this integration creates."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcobeeSecurityCoordinator


class EcobeeSecurityEntity(CoordinatorEntity[EcobeeSecurityCoordinator]):
    """Groups the panel and its diagnostics under one home."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EcobeeSecurityCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.home_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.home_id)},
            manufacturer="ecobee",
            model="Smart Security",
            name=coordinator.config_entry.title,
        )
