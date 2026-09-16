"""Config entry diagnostics, with the token and the home's identity withheld."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_POLL_INTERVAL

# An allowlist, so a key added to the entry later cannot leak by being forgotten here.
ENTRY_KEYS_SAFE_TO_REPORT = ("home_id_present", "token_present")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    snapshot = coordinator.data
    return {
        "entry": {
            "home_id_present": bool(entry.data.get("home_id")),
            "token_present": bool(entry.data.get("token")),
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
