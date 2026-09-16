"""Transport behaviour, especially the error layers that arrive with HTTP 200."""

import pytest

from custom_components.ecobee_security.api import (
    ApiError,
    AuthFailed,
    CannotConnect,
    EcobeeSecurityApi,
)

HOME_ID = "home-1"

MONITORING = {
    "armedState": "DISARMED",
    "delayedArmedState": None,
    "incidents": [],
    "homeMonitoringSettings": {},
}


class FakeResponse:
    def __init__(self, payload, status=200, date=None):
        self._payload = payload
        self.status = status
        self.headers = {"Date": date} if date else {}

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def post(self, url, json, headers, timeout=None):
        self.requests.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return self._responses.pop(0)


def make_api(*responses):
    session = FakeSession(*responses)

    async def token():
        return "token-value"

    return EcobeeSecurityApi(session, token), session


@pytest.mark.asyncio
async def test_unrelated_forbidden_does_not_fail_a_read():
    """The captured shape: HTTP 200, an error on a sibling field, and usable data."""
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied: Check your permissions",
                        "path": ["homes", 0, "monitoring", "emergencyContacts"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]},
            }
        )
    )
    assert await api.async_get_state(HOME_ID) == MONITORING


@pytest.mark.asyncio
async def test_error_on_the_field_we_read_does_fail():
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied",
                        "path": ["homes", 0, "monitoring"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"homes": [{"id": HOME_ID, "monitoring": None}]},
            }
        )
    )
    with pytest.raises(ApiError):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_forbidden_is_not_an_auth_failure():
    """A per-home permission error must not loop the user through reauth."""
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied",
                        "path": ["setArmedStateForHome"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"setArmedStateForHome": None},
            }
        )
    )
    with pytest.raises(ApiError):
        await api.async_set_armed_state(HOME_ID, "ARMED_AWAY", None)


@pytest.mark.asyncio
async def test_unauthenticated_is_an_auth_failure():
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {"message": "no", "path": None, "extensions": {"code": "UNAUTHENTICATED"}}
                ],
                "data": None,
            }
        )
    )
    with pytest.raises(AuthFailed):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_http_401_is_an_auth_failure():
    api, _ = make_api(FakeResponse({}, status=401))
    with pytest.raises(AuthFailed):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_payload_errors_fail_the_mutation():
    """A refusal arrives as HTTP 200 with an empty top-level errors array."""
    api, _ = make_api(
        FakeResponse(
            {
                "data": {
                    "setArmedStateForHome": {
                        "errors": ["PIN_REQUIRED"],
                        "home": {"id": HOME_ID, "monitoring": MONITORING},
                    }
                }
            }
        )
    )
    with pytest.raises(ApiError, match="PIN_REQUIRED"):
        await api.async_set_armed_state(HOME_ID, "ARMED_AWAY", None)


@pytest.mark.asyncio
async def test_payload_errors_may_be_objects():
    api, _ = make_api(
        FakeResponse(
            {
                "data": {
                    "setArmedStateForHome": {
                        "errors": [{"code": "DEVICE_OFFLINE"}],
                        "home": None,
                    }
                }
            }
        )
    )
    with pytest.raises(ApiError, match="DEVICE_OFFLINE"):
        await api.async_set_armed_state(HOME_ID, "ARMED_AWAY", None)


@pytest.mark.asyncio
async def test_successful_mutation_returns_monitoring():
    api, session = make_api(
        FakeResponse(
            {
                "data": {
                    "setArmedStateForHome": {
                        "errors": [],
                        "home": {"id": HOME_ID, "monitoring": MONITORING},
                    }
                }
            }
        )
    )
    assert await api.async_set_armed_state(HOME_ID, "ARMED_AWAY", "2026-09-15T20:02:00Z") == MONITORING
    assert session.requests[0]["json"]["variables"]["exitDelayUntil"] == "2026-09-15T20:02:00Z"


@pytest.mark.asyncio
async def test_omitted_exit_delay_is_not_sent_as_null():
    api, session = make_api(
        FakeResponse(
            {
                "data": {
                    "setArmedStateForHome": {
                        "errors": [],
                        "home": {"id": HOME_ID, "monitoring": MONITORING},
                    }
                }
            }
        )
    )
    await api.async_set_armed_state(HOME_ID, "DISARMED", None)
    assert "exitDelayUntil" not in session.requests[0]["json"]["variables"]


@pytest.mark.asyncio
async def test_state_for_another_home_is_rejected():
    api, _ = make_api(
        FakeResponse({"data": {"homes": [{"id": "someone-else", "monitoring": MONITORING}]}})
    )
    with pytest.raises(ApiError):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_clock_offset_measured_from_date_header():
    api, _ = make_api(
        FakeResponse(
            {"data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]}},
            date="Tue, 15 Sep 2026 20:00:00 GMT",
        )
    )
    await api.async_get_state(HOME_ID)
    assert api.clock_offset is not None


@pytest.mark.asyncio
async def test_discovery_asks_for_no_personal_data():
    api, session = make_api(FakeResponse({"data": {"homes": [{"id": HOME_ID, "name": "Home"}]}}))
    assert await api.async_get_homes() == [
        {"id": HOME_ID, "name": "Home", "monitoring": True}
    ]
    document = session.requests[0]["json"]["query"]
    for field in ("address", "lat", "lng", "devices", "cameras"):
        assert field not in document


@pytest.mark.asyncio
async def test_error_nulling_the_pending_state_is_fatal():
    """Tolerated, this would read as 'not arming' while the house is mid-exit-delay."""
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied",
                        "path": ["homes", 0, "monitoring", "delayedArmedState"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]},
            }
        )
    )
    with pytest.raises(ApiError):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_error_nulling_the_exit_delay_settings_is_fatal():
    """Tolerated, this would fall back to a zero delay and arm the house instantly."""
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied",
                        "path": ["homes", 0, "monitoring", "homeMonitoringSettings",
                                 "armedStateSettings"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]},
            }
        )
    )
    with pytest.raises(ApiError):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_error_on_a_diagnostic_field_is_tolerated():
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied",
                        "path": ["homes", 0, "monitoring", "homeMonitoringSettings",
                                 "hasPinSetUp"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]},
            }
        )
    )
    assert await api.async_get_state(HOME_ID) == MONITORING


@pytest.mark.asyncio
async def test_a_dead_token_getter_is_an_auth_failure():
    session = FakeSession(FakeResponse({}))

    async def token():
        raise RuntimeError("refresh token rejected")

    api = EcobeeSecurityApi(session, token)
    with pytest.raises(AuthFailed):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_a_timeout_is_a_connection_failure():
    class TimingOutSession(FakeSession):
        def post(self, *args, **kwargs):
            raise TimeoutError

    async def token():
        return "t"

    api = EcobeeSecurityApi(TimingOutSession(), token)
    with pytest.raises(CannotConnect):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_a_home_without_monitoring_is_flagged():
    api, _ = make_api(
        FakeResponse(
            {
                "data": {
                    "homes": [
                        {"id": "a", "name": "Cabin", "features": ["LOCATION_SERVICES"]},
                        {"id": "b", "name": "House", "features": ["HOME_MONITORING"]},
                    ]
                }
            }
        )
    )
    homes = await api.async_get_homes()
    assert [h["monitoring"] for h in homes] == [False, True]


@pytest.mark.asyncio
async def test_absent_features_are_treated_as_capable():
    """Not knowing is not a reason to hide a home the user can see in the app."""
    api, _ = make_api(FakeResponse({"data": {"homes": [{"id": "a", "name": "House"}]}}))
    assert (await api.async_get_homes())[0]["monitoring"] is True


@pytest.mark.asyncio
async def test_an_error_inside_an_incident_is_fatal():
    """Null propagation can take the whole list with it, which reads as 'no alarm'."""
    api, _ = make_api(
        FakeResponse(
            {
                "errors": [
                    {
                        "message": "PermissionDenied",
                        "path": ["homes", 0, "monitoring", "incidents", 0, "timestamp"],
                        "extensions": {"code": "FORBIDDEN"},
                    }
                ],
                "data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]},
            }
        )
    )
    with pytest.raises(ApiError):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_a_missing_incident_list_is_rejected():
    """Silently absent is indistinguishable from 'no alarm firing', so refuse it."""
    without = {k: v for k, v in MONITORING.items() if k != "incidents"}
    api, _ = make_api(
        FakeResponse({"data": {"homes": [{"id": HOME_ID, "monitoring": without}]}})
    )
    with pytest.raises(ApiError, match="incident"):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_a_missing_armed_state_is_rejected():
    without = {k: v for k, v in MONITORING.items() if k != "armedState"}
    api, _ = make_api(
        FakeResponse({"data": {"homes": [{"id": HOME_ID, "monitoring": without}]}})
    )
    with pytest.raises(ApiError, match="armed state"):
        await api.async_get_state(HOME_ID)


@pytest.mark.asyncio
async def test_a_transient_reset_is_retried_once():
    """A dropped socket must not blank the alarm entity for a whole poll cycle."""
    import aiohttp

    good = FakeResponse({"data": {"homes": [{"id": HOME_ID, "monitoring": MONITORING}]}})

    class ResettingOnce(FakeSession):
        def __init__(self):
            super().__init__(good)
            self.attempts = 0

        def post(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise aiohttp.ClientError("Connection reset by peer")
            return super().post(*args, **kwargs)

    async def token():
        return "t"

    session = ResettingOnce()
    api = EcobeeSecurityApi(session, token)
    assert await api.async_get_state(HOME_ID) == MONITORING
    assert session.attempts == 2


@pytest.mark.asyncio
async def test_a_persistent_reset_still_fails():
    import aiohttp

    class AlwaysResets(FakeSession):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def post(self, *args, **kwargs):
            self.attempts += 1
            raise aiohttp.ClientError("Connection reset by peer")

    async def token():
        return "t"

    session = AlwaysResets()
    api = EcobeeSecurityApi(session, token)
    with pytest.raises(CannotConnect):
        await api.async_get_state(HOME_ID)
    assert session.attempts == 2
