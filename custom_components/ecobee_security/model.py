"""Server state to panel state, and the exit-delay arithmetic.

No Home Assistant and no I/O: the decisions here are the ones worth testing directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .const import (
    ARMED_STATE_ARMED,
    INCIDENT_LEVEL_ALERTED,
    INCIDENT_LEVEL_REPORTED,
    ARMED_STATE_AWAY,
    ARMED_STATE_DISARMED,
    ARMED_STATE_STAY,
    DEFAULT_EXIT_DELAY,
    MAX_CLOCK_SKEW,
    TRANSITION_GRACE,
)


class PanelState:
    """The subset of HA's AlarmControlPanelState this API can produce."""

    DISARMED = "disarmed"
    ARMED_HOME = "armed_home"
    ARMED_AWAY = "armed_away"
    ARMING = "arming"
    PENDING = "pending"
    TRIGGERED = "triggered"
    UNKNOWN = None


ARMED_STATE_TO_PANEL: dict[str, str] = {
    ARMED_STATE_DISARMED: PanelState.DISARMED,
    ARMED_STATE_AWAY: PanelState.ARMED_AWAY,
    ARMED_STATE_STAY: PanelState.ARMED_HOME,
    # Meaning unknown and never seen on the wire. Reading a bare "armed" as armed is the
    # safe direction for an alarm.
    ARMED_STATE_ARMED: PanelState.ARMED_AWAY,
}


class SkewTooLarge(Exception):
    """Our clock disagrees with the server's by enough to mis-time an arm."""


class SettingsUnavailable(Exception):
    """The account's configured delays could not be read."""


@dataclass(frozen=True)
class ModeSettings:
    """Per-mode durations as the account has them configured."""

    exit_delay: int
    entry_delay: int
    siren_duration: int


@dataclass(frozen=True)
class Incident:
    """A live security incident. The only place a firing alarm is visible."""

    incident_id: str | None
    level: str | None
    status: str | None
    timestamp: datetime | None
    delay_until: datetime | None
    sources: tuple[str, ...]
    event_types: tuple[str, ...]

    @property
    def is_live(self) -> bool:
        # The feed returns only live incidents today, so status is belt-and-braces
        # against a resolved one arriving and pinning the panel on triggered.
        return self.status in (None, "OPEN")

    @property
    def is_alerting(self) -> bool:
        return self.is_live and self.level == INCIDENT_LEVEL_ALERTED

    @property
    def is_counting_down(self) -> bool:
        return self.is_live and self.level == INCIDENT_LEVEL_REPORTED


@dataclass(frozen=True)
class Snapshot:
    """One read of the monitoring node, normalized."""

    armed_state: str | None
    desired_armed_state: str | None
    delayed_until: datetime | None
    has_pin: bool | None
    pro_monitoring: bool | None
    away: ModeSettings | None
    stay: ModeSettings | None
    incidents: tuple[Incident, ...] = ()

    @property
    def is_pending(self) -> bool:
        return self.desired_armed_state is not None

    @property
    def is_armed(self) -> bool:
        """Whether the house is in any state that is not plainly disarmed."""
        return bool(
            self.is_pending
            or self.incidents
            or (self.armed_state and self.armed_state != ARMED_STATE_DISARMED)
        )

    @property
    def alerting_incident(self) -> Incident | None:
        return next((inc for inc in self.incidents if inc.is_alerting), None)

    @property
    def counting_down_incident(self) -> Incident | None:
        return next((inc for inc in self.incidents if inc.is_counting_down), None)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a server timestamp, always tz-aware so HA's TIMESTAMP class accepts it."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mode_settings(raw: Any) -> ModeSettings | None:
    if not isinstance(raw, dict):
        return None
    return ModeSettings(
        exit_delay=int(raw.get("exitDelayDuration") or 0),
        entry_delay=int(raw.get("entryDelayDuration") or 0),
        siren_duration=int(raw.get("sirenDuration") or 0),
    )


def _build_incident(raw: Any) -> Incident | None:
    if not isinstance(raw, dict):
        return None
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    sources: list[str] = []
    types: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("type"), str):
            types.append(event["type"])
        actor = event.get("actor") or {}
        if actor.get("type") == "SENSOR" and actor.get("name"):
            if actor["name"] not in sources:
                sources.append(actor["name"])
    return Incident(
        incident_id=raw.get("id"),
        level=raw.get("level"),
        status=raw.get("status"),
        timestamp=parse_timestamp(raw.get("timestamp")),
        delay_until=parse_timestamp(raw.get("delayUntil")),
        sources=tuple(sources),
        event_types=tuple(types),
    )


def build_snapshot(monitoring: dict[str, Any]) -> Snapshot:
    delayed = monitoring.get("delayedArmedState") or {}
    settings = monitoring.get("homeMonitoringSettings") or {}
    armed_settings = settings.get("armedStateSettings") or {}
    return Snapshot(
        armed_state=monitoring.get("armedState"),
        desired_armed_state=delayed.get("desiredArmedState"),
        delayed_until=parse_timestamp(delayed.get("delayedUntil")),
        has_pin=settings.get("hasPinSetUp"),
        pro_monitoring=settings.get("isProfessionalMonitoringEnabled"),
        away=_mode_settings(armed_settings.get("armedAway")),
        stay=_mode_settings(armed_settings.get("armedStay")),
        incidents=tuple(
            inc
            for inc in (
                _build_incident(raw)
                for raw in (monitoring.get("incidents") or [])
            )
            if inc is not None
        ),
    )


def panel_state(
    snapshot: Snapshot,
    now: datetime,
    clock_offset: float | None = None,
    pending_since: datetime | None = None,
) -> tuple[str | None, bool]:
    """Map a snapshot to a panel state, and say whether the watchdog fired.

    The server completes a delayed arm on its own, so a pending state that outlives its
    deadline means it stalled, and a panel stuck on "arming" reads to every dashboard as
    "about to be armed".
    """
    # An incident outranks armedState, which does not change when the alarm fires.
    if snapshot.alerting_incident is not None:
        return PanelState.TRIGGERED, False
    if (countdown := snapshot.counting_down_incident) is not None:
        # A countdown that outran its deadline is stuck; do not hold PENDING forever.
        overrun = countdown.delay_until is not None and now + timedelta(
            seconds=clock_offset or 0.0
        ) > countdown.delay_until + timedelta(seconds=TRANSITION_GRACE)
        if not overrun:
            return PanelState.PENDING, False

    if snapshot.is_pending:
        # delayedUntil is the server's clock, so compare in the server's frame.
        server_now = now + timedelta(seconds=clock_offset or 0.0)
        deadline = snapshot.delayed_until
        if deadline is None and pending_since is not None:
            # No parseable deadline, so bound it locally rather than wait forever.
            deadline = pending_since + timedelta(seconds=DEFAULT_EXIT_DELAY)
            server_now = now
        if deadline is None or server_now <= deadline + timedelta(seconds=TRANSITION_GRACE):
            return PanelState.ARMING, False
        return ARMED_STATE_TO_PANEL.get(snapshot.armed_state or ""), True

    if snapshot.armed_state is None:
        return PanelState.UNKNOWN, False
    return ARMED_STATE_TO_PANEL.get(snapshot.armed_state, PanelState.UNKNOWN), False


def exit_delay_for(
    snapshot: Snapshot, target: str, override: int | None = None
) -> int:
    """The exit delay to ask the server for, in seconds.

    Refuses to guess: a missing settings block would otherwise become a zero delay, and
    a zero delay arms the house the instant the button is pressed.
    """
    if override is not None:
        return max(0, override)
    settings = snapshot.stay if target == ARMED_STATE_STAY else snapshot.away
    if settings is None:
        raise SettingsUnavailable(
            f"The configured exit delay for {target} could not be read"
        )
    return max(0, settings.exit_delay)


def exit_delay_until(
    delay_seconds: int, now: datetime, clock_offset: float | None
) -> str | None:
    """The absolute timestamp to hand the server, corrected for our own clock.

    Returns None for a zero delay, which is how the server is told to arm at once. The
    server ignores its own configured duration when the field is absent, so this value
    is the only thing that creates an exit delay at all.
    """
    if clock_offset is not None and abs(clock_offset) > MAX_CLOCK_SKEW:
        raise SkewTooLarge(
            f"Local clock differs from the server by {clock_offset:.0f}s; "
            "refusing to set an exit delay against it"
        )
    if delay_seconds <= 0:
        return None
    corrected = now + timedelta(seconds=(clock_offset or 0.0) + delay_seconds)
    return corrected.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
