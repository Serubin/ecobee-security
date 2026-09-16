"""Config entry diagnostics, with the token and the home's identity withheld."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_POLL_INTERVAL

# Presence flags only: reporting whether a key is set can never leak what it holds, so a
# key added to the entry later cannot leak by being forgotten here.
ENTRY_KEYS_TO_REPORT = ("home_id", "token")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    return {
        "entry": {
            f"{key}_present": bool(entry.data.get(key))
            for key in ENTRY_KEYS_TO_REPORT
        },
        "options": {CONF_POLL_INTERVAL: entry.options.get(CONF_POLL_INTERVAL)},
        "state": {
            "armed_state": snapshot.armed_state if snapshot else None,
            "desired_armed_state": snapshot.desired_armed_state if snapshot else None,
            "is_pending": snapshot.is_pending if snapshot else None,
            "has_pin": snapshot.has_pin if snapshot else None,
            "pro_monitoring": snapshot.pro_monitoring if snapshot else None,
        },
        "clock_offset_seconds": coordinator.api.clock_offset,
        "pending_since": coordinator.pending_since,
    }
