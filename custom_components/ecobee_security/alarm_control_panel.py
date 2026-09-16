"""The panel. Reports what the server says, and refuses to guess when it cannot."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import EcobeeSecurityError
from .const import (
    ARMED_STATE_AWAY,
    ARMED_STATE_DISARMED,
    ARMED_STATE_STAY,
    CONF_EXIT_DELAY_AWAY,
    CONF_EXIT_DELAY_STAY,
)
from .coordinator import EcobeeSecurityCoordinator
from .entity import EcobeeSecurityEntity
from .model import (
    PanelState,
    SettingsUnavailable,
    SkewTooLarge,
    exit_delay_for,
    exit_delay_until,
    panel_state,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

PANEL_STATE_TO_HA = {
    PanelState.DISARMED: AlarmControlPanelState.DISARMED,
    PanelState.ARMED_HOME: AlarmControlPanelState.ARMED_HOME,
    PanelState.ARMED_AWAY: AlarmControlPanelState.ARMED_AWAY,
    PanelState.ARMING: AlarmControlPanelState.ARMING,
    PanelState.PENDING: AlarmControlPanelState.PENDING,
    PanelState.TRIGGERED: AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([EcobeeSecurityPanel(entry.runtime_data, entry)])


class EcobeeSecurityPanel(EcobeeSecurityEntity, AlarmControlPanelEntity):
    """One home's arm state."""

    _attr_name = None
    _attr_code_arm_required = False
    _attr_code_format = None
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY | AlarmControlPanelEntityFeature.ARM_HOME
    )

    def __init__(
        self, coordinator: EcobeeSecurityCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, "alarm")
        self._entry = entry
        self._warned_states: set[str] = set()
        self._watchdog_logged = False

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        snapshot = self.coordinator.data
        if snapshot is None:
            return None

        state, watchdog_fired = panel_state(
            snapshot,
            dt_util.utcnow(),
            self.coordinator.api.clock_offset,
            self.coordinator.pending_since,
        )

        if watchdog_fired and not self._watchdog_logged:
            self._watchdog_logged = True
            _LOGGER.error(
                "Arming to %s did not complete by %s; reporting the server's own state "
                "of %s instead of leaving the panel on 'arming'",
                snapshot.desired_armed_state,
                snapshot.delayed_until,
                snapshot.armed_state,
            )
        elif not watchdog_fired:
            self._watchdog_logged = False

        if state is None:
            self._warn_once(snapshot.armed_state)
            return None
        return PANEL_STATE_TO_HA[state]

    def _warn_once(self, armed_state: str | None) -> None:
        key = armed_state or "<missing>"
        if key in self._warned_states:
            return
        self._warned_states.add(key)
        _LOGGER.warning(
            "Unrecognized armed state %r; reporting unknown rather than guessing", key
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """HA has one 'arming' state, so the target rides along as an attribute."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        incident = snapshot.alerting_incident or snapshot.counting_down_incident
        return {
            "desired_armed_state": snapshot.desired_armed_state,
            "delayed_until": snapshot.delayed_until,
            "raw_armed_state": snapshot.armed_state,
            "incident_level": incident.level if incident else None,
            "incident_sources": list(incident.sources) if incident else [],
            "incident_delay_until": incident.delay_until if incident else None,
        }

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._set_state(ARMED_STATE_DISARMED)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._set_state(ARMED_STATE_AWAY)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._set_state(ARMED_STATE_STAY)

    async def _set_state(self, target: str) -> None:
        snapshot = self.coordinator.data
        until: str | None = None

        if target != ARMED_STATE_DISARMED:
            if snapshot is None:
                raise HomeAssistantError(
                    "Refusing to arm before the current state has been read"
                )
            override = self._entry.options.get(
                CONF_EXIT_DELAY_STAY
                if target == ARMED_STATE_STAY
                else CONF_EXIT_DELAY_AWAY
            )
            try:
                delay = exit_delay_for(snapshot, target, override)
                until = exit_delay_until(
                    delay, dt_util.utcnow(), self.coordinator.api.clock_offset
                )
            except (SettingsUnavailable, SkewTooLarge) as err:
                raise HomeAssistantError(str(err)) from err

        try:
            monitoring = await self.coordinator.api.async_set_armed_state(
                self.coordinator.home_id, target, until
            )
        except EcobeeSecurityError as err:
            # The server may have acted anyway, so re-read rather than trust our own view.
            await self.coordinator.async_refresh()
            raise HomeAssistantError(
                f"Could not confirm {target} with ecobee: {err}"
            ) from err

        self.coordinator.apply_mutation_result(
            monitoring, disarmed=target == ARMED_STATE_DISARMED
        )
        # Undebounced: the mutation payload carries no incident list, so only a real read
        # can confirm whether an alarm is still sounding.
        await self.coordinator.async_refresh()
