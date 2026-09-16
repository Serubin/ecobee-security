"""GraphQL transport for the ecobee Smart Security private API.

Free of Home Assistant imports so it can be exercised against a fake session.
"""

from __future__ import annotations

import logging
import time
import uuid
from email.utils import parsedate_to_datetime
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import (
    APP_ACCEPT,
    APP_CLIENT_LIBRARY,
    APP_USER_AGENT,
    FEATURE_HOME_MONITORING,
    GRAPHQL_URL,
    HOMES_QUERY,
    SET_ARMED_STATE_MUTATION,
    STATE_QUERY,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = ClientTimeout(total=30)

# The graph resets a connection now and then. One immediate retry keeps a dropped socket
# from blanking the alarm entity for a whole poll cycle.
TRANSIENT_RETRIES = 1


class EcobeeSecurityError(Exception):
    """Base error."""


class CannotConnect(EcobeeSecurityError):
    """The endpoint could not be reached."""


class AuthFailed(EcobeeSecurityError):
    """The token was rejected and re-authentication is required."""


class ApiError(EcobeeSecurityError):
    """The server processed the request and refused it."""


# Every field the state machine or the exit-delay arithmetic depends on. An error that
# nulls one of these must fail the request: tolerated, it would reach the state machine
# as a legitimately absent value and read as "not arming" or "no exit delay".
REQUIRED_READ_FIELDS: tuple[str, ...] = (
    "homes",
    "monitoring",
    "armedState",
    "delayedArmedState",
    "delayedUntil",
    "desiredArmedState",
    "armedStateSettings",
    "armedAway",
    "armedStay",
    "exitDelayDuration",
    # A tolerated error here would read as "no alarm firing".
    "incidents",
    "level",
    "status",
)

# Fields whose loss degrades diagnostics without misrepresenting the alarm are absent
# from the set above on purpose; see the emergencyContacts case in the protocol notes.

MUTATION_FIELDS: tuple[str, ...] = ("setArmedStateForHome", "home", "monitoring")

# GraphQL null propagation bubbles a field error up to a non-null parent, so an error deep
# inside one incident can null the whole list — which would read as "no alarm firing".
SUBTREE_CRITICAL: tuple[str, ...] = ("incidents", "delayedArmedState")


def _is_fatal(error_path: list[Any] | None, required: tuple[str, ...]) -> bool:
    """Whether a GraphQL error nulls something we depend on.

    An error nulls the value at its own path; a pathless error is global. Fields outside
    this set are diagnostics, which degrade without misrepresenting the alarm.
    """
    if not error_path:
        return True
    segments = [part for part in error_path if isinstance(part, str)]
    if any(segment in SUBTREE_CRITICAL for segment in segments):
        return True
    return (segments[-1] if segments else None) in required


def _raise_for_errors(payload: dict[str, Any], required: tuple[str, ...]) -> None:
    relevant = [
        err for err in payload.get("errors") or [] if _is_fatal(err.get("path"), required)
    ]
    if relevant:
        messages = "; ".join(str(err.get("message", err)) for err in relevant)
        codes = {
            (err.get("extensions") or {}).get("code") for err in relevant
        }
        if "UNAUTHENTICATED" in codes:
            raise AuthFailed(messages)
        raise ApiError(messages)
    for err in payload.get("errors") or []:
        _LOGGER.warning(
            "Continuing without a diagnostic field the server refused: %s", err.get("path")
        )


class EcobeeSecurityApi:
    """Executes the handful of documents this integration needs."""

    def __init__(self, session: ClientSession, token_getter: Any) -> None:
        self._session = session
        self._token_getter = token_getter
        self._clock_offset: float | None = None

    @property
    def clock_offset(self) -> float | None:
        """Seconds to add to local time to match the server, once measured."""
        return self._clock_offset

    def _record_server_clock(self, response: ClientResponse) -> None:
        date_header = response.headers.get("Date")
        if not date_header:
            return
        try:
            server_time = parsedate_to_datetime(date_header).timestamp()
        except (TypeError, ValueError):
            return
        self._clock_offset = server_time - time.time()

    async def execute(
        self,
        document: str,
        variables: dict[str, Any],
        operation_name: str,
        required: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Run one operation and return its data, raising on errors that damage it."""
        body = {
            "operationName": operation_name,
            "query": document,
            "variables": variables,
            "extensions": {"clientLibrary": APP_CLIENT_LIBRARY},
        }

        try:
            token = await self._token_getter()
        except Exception as err:
            raise AuthFailed(f"Could not obtain an access token: {err}") from err

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": APP_ACCEPT,
            "user-agent": APP_USER_AGENT,
            "x-apollo-operation-name": operation_name,
            "x-correlation-id": str(uuid.uuid4()),
        }

        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                async with self._session.post(
                    GRAPHQL_URL, json=body, headers=headers, timeout=REQUEST_TIMEOUT
                ) as response:
                    self._record_server_clock(response)
                    if response.status == 401:
                        raise AuthFailed("The security graph rejected the token")
                    if response.status >= 400:
                        raise ApiError(f"HTTP {response.status} from the security graph")
                    payload = await response.json()
                break
            except TimeoutError as err:
                last: Exception = CannotConnect("The security graph did not respond in time")
                last.__cause__ = err
            except ClientError as err:
                last = CannotConnect(str(err))
                last.__cause__ = err
            if attempt == TRANSIENT_RETRIES:
                raise last
            _LOGGER.debug("Retrying %s after a transient error: %s", operation_name, last)

        _raise_for_errors(payload, required)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ApiError(f"{operation_name} returned no data")
        return data

    async def async_get_homes(self) -> list[dict[str, Any]]:
        data = await self.execute(HOMES_QUERY, {}, "HomesQuery", ("homes",))
        homes = data.get("homes")
        if not isinstance(homes, list):
            raise ApiError("HomesQuery returned no homes")
        return [
            {
                "id": home["id"],
                "name": home.get("name") or home["id"],
                # Absent features are treated as capable: not knowing is not a reason to
                # hide a home the user can see in the app.
                "monitoring": (
                    FEATURE_HOME_MONITORING in home["features"]
                    if isinstance(home.get("features"), list)
                    else True
                ),
            }
            for home in homes
            if isinstance(home, dict) and home.get("id")
        ]

    async def async_get_state(self, home_id: str) -> dict[str, Any]:
        data = await self.execute(
            STATE_QUERY,
            {"homeId": home_id, "filter": {}},
            "ArmedStateQuery",
            REQUIRED_READ_FIELDS,
        )
        return _extract_monitoring(data, home_id)

    async def async_set_armed_state(
        self, home_id: str, armed_state: str, exit_delay_until: str | None
    ) -> dict[str, Any]:
        """Arm or disarm, returning the monitoring block the server reported back."""
        variables: dict[str, Any] = {"homeId": home_id, "armedState": armed_state}
        if exit_delay_until is not None:
            variables["exitDelayUntil"] = exit_delay_until

        data = await self.execute(
            SET_ARMED_STATE_MUTATION,
            variables,
            "SetArmedStateForHome",
            MUTATION_FIELDS,
        )
        result = data.get("setArmedStateForHome") or {}

        # Empty in every observed call, including deliberate failures, so the element type
        # is unknown; render whatever is there rather than assume a shape.
        if errors := result.get("errors"):
            raise ApiError(
                "; ".join(str(err) for err in errors)
                if isinstance(errors, list)
                else str(errors)
            )

        home = result.get("home")
        if not isinstance(home, dict):
            raise ApiError("setArmedStateForHome reported no home")
        monitoring = home.get("monitoring")
        if not isinstance(monitoring, dict):
            raise ApiError("setArmedStateForHome reported no monitoring state")
        return monitoring


def _extract_monitoring(data: dict[str, Any], home_id: str) -> dict[str, Any]:
    homes = data.get("homes")
    if not isinstance(homes, list):
        raise ApiError("ArmedStateQuery returned no homes")
    for home in homes:
        if not isinstance(home, dict) or home.get("id") != home_id:
            continue
        monitoring = home.get("monitoring")
        if not isinstance(monitoring, dict):
            raise ApiError(f"Home {home_id} reported no monitoring state")
        # Assert presence structurally: a silently dropped key would otherwise read as
        # "not armed" or "no alarm firing" rather than as a failed read.
        if not isinstance(monitoring.get("incidents"), list):
            raise ApiError(f"Home {home_id} returned no incident list")
        if not isinstance(monitoring.get("armedState"), str):
            raise ApiError(f"Home {home_id} returned no armed state")
        return monitoring
    raise ApiError(f"Home {home_id} is not on this account")
