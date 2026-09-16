"""ecobee Smart Security, over the app's private GraphQL API."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EcobeeSecurityApi
from .auth import EcobeeOAuth2Implementation
from .const import CONF_HOME_ID
from .coordinator import EcobeeSecurityCoordinator

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

type EcobeeSecurityConfigEntry = ConfigEntry[EcobeeSecurityCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: EcobeeSecurityConfigEntry
) -> bool:
    if "expires_at" not in (entry.data.get("token") or {}):
        # Stored by a build that omitted it; expired forces a refresh on first use.
        token = {**entry.data["token"], "expires_in": 0, "expires_at": 0}
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": token}
        )

    session = config_entry_oauth2_flow.OAuth2Session(
        hass, entry, EcobeeOAuth2Implementation(hass)
    )

    async def _token() -> str:
        await session.async_ensure_token_valid()
        return session.token["access_token"]

    api = EcobeeSecurityApi(async_get_clientsession(hass), _token)
    coordinator = EcobeeSecurityCoordinator(
        hass, entry, api, entry.data[CONF_HOME_ID]
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EcobeeSecurityConfigEntry
) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, entry: EcobeeSecurityConfigEntry
) -> bool:
    return entry.version == 1
