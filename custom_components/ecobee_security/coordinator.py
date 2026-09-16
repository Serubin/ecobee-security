"""Polls the security graph, faster while a transition is pending."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ApiError, AuthFailed, CannotConnect, EcobeeSecurityApi
from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    TRANSITION_GRACE,
    TRANSITION_POLL_INTERVAL,
)
from .model import Snapshot, build_snapshot

_LOGGER = logging.getLogger(__name__)

MAX_BACKOFF = 600


class EcobeeSecurityCoordinator(DataUpdateCoordinator[Snapshot]):
    """One home's monitoring state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: EcobeeSecurityApi,
        home_id: str,
    ) -> None:
        self.api = api
        self.home_id = home_id
        self._base_interval = timedelta(
            seconds=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
        self._failures = 0
        self._pending_since: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {home_id}",
            update_interval=self._base_interval,
        )

    @property
    def pending_since(self) -> datetime | None:
        """When a pending transition was first seen, for bounding one with no deadline."""
        return self._pending_since

    async def _async_update_data(self) -> Snapshot:
        try:
            monitoring = await self.api.async_get_state(self.home_id)
        except AuthFailed as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (ApiError, CannotConnect) as err:
            self._failures += 1
            self._back_off()
            raise UpdateFailed(str(err)) from err

        self._failures = 0
        snapshot = build_snapshot(monitoring)
        self._pending_since = (
            (self._pending_since or dt_util.utcnow()) if snapshot.is_pending else None
        )
        self._retune_interval(snapshot)
        return snapshot

    def _back_off(self) -> None:
        """Do not keep hammering the graph at the transition rate while it is failing."""
        backoff = self._base_interval * min(2**self._failures, 8)
        self.update_interval = min(backoff, timedelta(seconds=MAX_BACKOFF))

    def _retune_interval(self, snapshot: Snapshot) -> None:
        """Poll fast through an exit delay, but never indefinitely.

        An unbounded fast poll would hammer a third party's production graph from every
        install whenever a pending state failed to clear.
        """
        interval = self._base_interval
        if snapshot.is_pending and snapshot.delayed_until is not None:
            deadline = snapshot.delayed_until + timedelta(seconds=TRANSITION_GRACE)
            if dt_util.utcnow() < deadline:
                interval = timedelta(seconds=TRANSITION_POLL_INTERVAL)
        if interval != self.update_interval:
            self.update_interval = interval

    def apply_mutation_result(self, monitoring: dict[str, Any]) -> None:
        """Adopt the state a mutation reported, keeping settings it does not carry."""
        snapshot = build_snapshot(monitoring)
        if self.data is not None:
            snapshot = replace(
                snapshot,
                has_pin=self.data.has_pin,
                pro_monitoring=self.data.pro_monitoring,
                away=self.data.away,
                stay=self.data.stay,
                incidents=self.data.incidents,
            )
        # Retune first: async_set_updated_data reschedules using the current interval.
        self._retune_interval(snapshot)
        self._pending_since = (
            (self._pending_since or dt_util.utcnow()) if snapshot.is_pending else None
        )
        self.async_set_updated_data(snapshot)
