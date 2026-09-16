"""The state table and the exit-delay arithmetic."""

from datetime import datetime, timezone

import pytest

from custom_components.ecobee_security.model import (
    PanelState,
    SettingsUnavailable,
    SkewTooLarge,
    build_snapshot,
    exit_delay_for,
    exit_delay_until,
    panel_state,
)

NOW = datetime(2026, 9, 15, 20, 0, 0, tzinfo=timezone.utc)


def monitoring(armed_state="DISARMED", desired=None, delayed_until=None, **settings):
    delayed = (
        {"delayedUntil": delayed_until, "desiredArmedState": desired}
        if desired
        else None
    )
    return {
        "armedState": armed_state,
        "delayedArmedState": delayed,
        "homeMonitoringSettings": {
            "hasPinSetUp": settings.get("has_pin", False),
            "isProfessionalMonitoringEnabled": settings.get("pro", False),
            "armedStateSettings": {
                "armedAway": {
                    "exitDelayDuration": settings.get("away_exit", 120),
                    "entryDelayDuration": 30,
                    "sirenDuration": 300,
                },
                "armedStay": {
                    "exitDelayDuration": settings.get("stay_exit", 0),
                    "entryDelayDuration": 0,
                    "sirenDuration": 300,
                },
            },
        },
    }


@pytest.mark.parametrize(
    ("armed_state", "expected"),
    [
        ("DISARMED", PanelState.DISARMED),
        ("ARMED_AWAY", PanelState.ARMED_AWAY),
        ("ARMED_STAY", PanelState.ARMED_HOME),
        ("ARMED", PanelState.ARMED_AWAY),
        ("ARMED_AWAY_PENDING", PanelState.UNKNOWN),
        ("SOMETHING_NEW", PanelState.UNKNOWN),
        (None, PanelState.UNKNOWN),
    ],
)
def test_settled_states(armed_state, expected):
    state, watchdog = panel_state(build_snapshot(monitoring(armed_state)), NOW)
    assert state == expected
    assert watchdog is False


def test_pending_arm_is_arming():
    snap = build_snapshot(
        monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="2026-09-15T20:02:00Z")
    )
    assert panel_state(snap, NOW) == (PanelState.ARMING, False)


def test_pending_arm_within_grace_is_still_arming():
    snap = build_snapshot(
        monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="2026-09-15T20:02:00Z")
    )
    just_after = datetime(2026, 9, 15, 20, 2, 20, tzinfo=timezone.utc)
    assert panel_state(snap, just_after) == (PanelState.ARMING, False)


def test_watchdog_stops_reporting_arming_forever():
    """A pending arm that outlives its deadline must not read as 'about to be armed'."""
    snap = build_snapshot(
        monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="2026-09-15T20:02:00Z")
    )
    much_later = datetime(2026, 9, 15, 21, 0, 0, tzinfo=timezone.utc)
    state, watchdog = panel_state(snap, much_later)
    assert state == PanelState.DISARMED
    assert watchdog is True


def test_pending_without_deadline_stays_arming():
    snap = build_snapshot(monitoring("DISARMED", desired="ARMED_AWAY"))
    assert panel_state(snap, NOW) == (PanelState.ARMING, False)


def test_exit_delay_read_per_mode():
    snap = build_snapshot(monitoring(away_exit=120, stay_exit=0))
    assert exit_delay_for(snap, "ARMED_AWAY") == 120
    assert exit_delay_for(snap, "ARMED_STAY") == 0


def test_exit_delay_honours_a_configured_stay_delay():
    """Stay is not inherently instant; it is instant because this account configures 0."""
    snap = build_snapshot(monitoring(stay_exit=45))
    assert exit_delay_for(snap, "ARMED_STAY") == 45


def test_exit_delay_override():
    snap = build_snapshot(monitoring())
    assert exit_delay_for(snap, "ARMED_AWAY", override=30) == 30


def test_zero_delay_sends_no_timestamp():
    assert exit_delay_until(0, NOW, clock_offset=0.0) is None


def test_timestamp_is_corrected_for_server_clock():
    """Our clock is 10s behind; the deadline must still be 120s of real time away."""
    assert exit_delay_until(120, NOW, clock_offset=10.0) == "2026-09-15T20:02:10Z"


def test_timestamp_without_a_measured_offset():
    assert exit_delay_until(120, NOW, clock_offset=None) == "2026-09-15T20:02:00Z"


def test_excessive_skew_refuses_to_arm():
    with pytest.raises(SkewTooLarge):
        exit_delay_until(120, NOW, clock_offset=600.0)


def test_skew_check_applies_even_to_an_instant_arm():
    with pytest.raises(SkewTooLarge):
        exit_delay_until(0, NOW, clock_offset=-900.0)


def test_snapshot_reads_settings():
    snap = build_snapshot(monitoring(has_pin=True, pro=True))
    assert snap.has_pin is True
    assert snap.pro_monitoring is True
    assert snap.away.entry_delay == 30
    assert snap.stay.siren_duration == 300


def test_missing_delayed_until_parses_to_none():
    snap = build_snapshot(monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="nonsense"))
    assert snap.delayed_until is None
    assert snap.is_pending is True


def test_missing_settings_refuses_to_arm_rather_than_guess_zero():
    """Guessing here would mean arming the house the instant the button is pressed."""
    snap = build_snapshot({"armedState": "DISARMED"})
    for target in ("ARMED_AWAY", "ARMED_STAY"):
        with pytest.raises(SettingsUnavailable):
            exit_delay_for(snap, target)


def test_missing_settings_still_honours_an_override():
    snap = build_snapshot({"armedState": "DISARMED"})
    assert exit_delay_for(snap, "ARMED_AWAY", override=90) == 90


def test_watchdog_compares_in_the_server_clock_frame():
    """Our clock 25s fast must not trip a watchdog whose grace is measured server-side."""
    snap = build_snapshot(
        monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="2026-09-15T20:02:00Z")
    )
    local_now = datetime(2026, 9, 15, 20, 2, 25, tzinfo=timezone.utc)
    assert panel_state(snap, local_now, clock_offset=-25.0) == (PanelState.ARMING, False)


def test_pending_without_a_deadline_is_bounded_locally():
    """An unparseable delayedUntil must not pin the panel on 'arming' forever."""
    snap = build_snapshot(monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="nonsense"))
    first_seen = datetime(2026, 9, 15, 20, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 15, 20, 30, 0, tzinfo=timezone.utc)
    assert panel_state(snap, first_seen, pending_since=first_seen)[0] == PanelState.ARMING
    state, watchdog = panel_state(snap, later, pending_since=first_seen)
    assert state == PanelState.DISARMED
    assert watchdog is True


def test_timestamps_are_always_timezone_aware():
    snap = build_snapshot(
        monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="2026-09-15T20:02:00")
    )
    assert snap.delayed_until.tzinfo is not None


def incident(level, sources=("Front Door",), delay_until=None, events=None):
    return {
        "id": "inc-1",
        "timestamp": "2026-09-15T20:00:00Z",
        "status": "OPEN",
        "level": level,
        "delayUntil": delay_until,
        "actionOptions": ["DISMISS"],
        "events": events
        or [
            {
                "type": "CONTACT_OPENED",
                "timestamp": "2026-09-15T20:00:00Z",
                "actor": {"name": name, "type": "SENSOR", "isLoggedInUser": False},
            }
            for name in sources
        ],
    }


def test_a_firing_alarm_outranks_the_armed_state():
    """armedState keeps reporting 'armed' through a live siren; the incident is the signal."""
    mon = monitoring("ARMED_AWAY")
    mon["incidents"] = [incident("ALERTED")]
    assert panel_state(build_snapshot(mon), NOW) == (PanelState.TRIGGERED, False)


def test_entry_delay_countdown_is_pending():
    mon = monitoring("ARMED_STAY")
    mon["incidents"] = [incident("REPORTED", delay_until="2026-09-15T20:00:30Z")]
    assert panel_state(build_snapshot(mon), NOW) == (PanelState.PENDING, False)


def test_alerting_outranks_a_countdown():
    mon = monitoring("ARMED_AWAY")
    mon["incidents"] = [incident("REPORTED"), incident("ALERTED")]
    assert panel_state(build_snapshot(mon), NOW) == (PanelState.TRIGGERED, False)


def test_a_firing_alarm_outranks_a_pending_arm():
    mon = monitoring("DISARMED", desired="ARMED_AWAY", delayed_until="2026-09-15T20:02:00Z")
    mon["incidents"] = [incident("ALERTED")]
    assert panel_state(build_snapshot(mon), NOW) == (PanelState.TRIGGERED, False)


def test_no_incidents_leaves_the_armed_state_alone():
    mon = monitoring("ARMED_AWAY")
    mon["incidents"] = []
    assert panel_state(build_snapshot(mon), NOW) == (PanelState.ARMED_AWAY, False)


def test_incident_records_which_sensors_tripped():
    mon = monitoring("ARMED_AWAY")
    mon["incidents"] = [incident("ALERTED", sources=("Front Door", "Hallway"))]
    inc = build_snapshot(mon).alerting_incident
    assert inc.sources == ("Front Door", "Hallway")
    assert inc.delay_until is None


def test_non_sensor_actors_are_not_listed_as_sources():
    """Only the device that tripped belongs here, not the system or the user."""
    mon = monitoring("ARMED_AWAY")
    mon["incidents"] = [
        incident(
            "ALERTED",
            events=[
                {"type": "SIREN_AUTO_STARTED", "timestamp": "2026-09-15T20:00:30Z",
                 "actor": {"name": "Home Monitoring", "type": "HOME_MONITORING"}},
                {"type": "CONTACT_OPENED", "timestamp": "2026-09-15T20:00:00Z",
                 "actor": {"name": "Back Door", "type": "SENSOR"}},
            ],
        )
    ]
    inc = build_snapshot(mon).alerting_incident
    assert inc.sources == ("Back Door",)
    assert "SIREN_AUTO_STARTED" in inc.event_types


def test_a_malformed_incident_does_not_break_the_snapshot():
    mon = monitoring("ARMED_AWAY")
    mon["incidents"] = ["nonsense", {"id": "x", "level": "ALERTED", "events": None}]
    snap = build_snapshot(mon)
    assert snap.alerting_incident is not None
    assert snap.alerting_incident.sources == ()
