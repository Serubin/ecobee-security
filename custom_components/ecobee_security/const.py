"""Constants and GraphQL documents for the ecobee Smart Security private API.

Documents are reproduced from a capture of the Android app; see the repository README
for provenance and for what remains unverified.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ecobee_security"

GRAPHQL_URL: Final = "https://federated-api.prod.federated-graph.generacapis.com/graphql"

AUTH_DOMAIN: Final = "https://auth.ecobee.com"
AUTHORIZE_URL: Final = f"{AUTH_DOMAIN}/authorize"
TOKEN_URL: Final = f"{AUTH_DOMAIN}/oauth/token"
CLIENT_ID: Final = "yg66ag34vWdf2Hs4oO2ih2BvI16KrkOR"
AUDIENCE: Final = "https://prod.ecobee.com/api/v1"
SCOPE: Final = "openid piiRead piiWrite smartWrite offline_access"

# The only callback the Auth0 client accepts. It is an Android App Link, so it cannot
# return to Home Assistant and the user pastes the redirected URL back into the flow.
REDIRECT_URI: Final = "https://auth.ecobee.com/android/com.ecobee.athenamobile/callback"

APP_USER_AGENT: Final = "android-26.16.0"
APP_ACCEPT: Final = (
    "multipart/mixed;deferSpec=20220824, application/graphql-response+json, application/json"
)
APP_CLIENT_LIBRARY: Final = {"name": "apollo-kotlin", "version": "4.4.1"}

CONF_HOME_ID: Final = "home_id"
CONF_HOME_NAME: Final = "home_name"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_EXIT_DELAY_AWAY: Final = "exit_delay_away"
CONF_EXIT_DELAY_STAY: Final = "exit_delay_stay"

DEFAULT_POLL_INTERVAL: Final = 60
TRANSITION_POLL_INTERVAL: Final = 5

# A live incident is polled fast too: the entry delay is 30s, so the base interval would
# usually skip the countdown entirely and report a siren up to a minute late. Bounded, so
# an incident that never clears cannot pin us at the fast rate forever.
INCIDENT_FAST_POLL_WINDOW: Final = 900

# The server drives the transition and clears delayedArmedState itself, so overrunning
# this means it stalled. Kept clear of MAX_CLOCK_SKEW so the tolerances cannot cancel.
TRANSITION_GRACE: Final = 60

# exitDelayUntil is an absolute timestamp the client picks, so a skewed clock silently
# arms the house instantly or leaves it unarmed far longer than the user believes.
MAX_CLOCK_SKEW: Final = 30

DEFAULT_EXIT_DELAY: Final = 120

# ARMED is a real HomeArmedState member that was never seen on the wire; its meaning is
# unknown, so a client must tolerate receiving it.
ARMED_STATE_DISARMED: Final = "DISARMED"
ARMED_STATE_AWAY: Final = "ARMED_AWAY"
ARMED_STATE_STAY: Final = "ARMED_STAY"
ARMED_STATE_ARMED: Final = "ARMED"

# Discovery. Deliberately not the app's HomesQuery, which pulls the whole device tree
# including street address and coordinates; we need an id and a name.
HOMES_QUERY: Final = """
query HomesQuery {
  homes { __typename id name features }
}
"""

# A home without this in its features array has no security to arm.
FEATURE_HOME_MONITORING: Final = "HOME_MONITORING"

# armedState does not change when the alarm fires, so a firing alarm is visible only here.
INCIDENT_LEVEL_REPORTED: Final = "REPORTED"
INCIDENT_LEVEL_ALERTED: Final = "ALERTED"

# One document for everything polled. The app splits this across five operations, but every
# field hangs off the same monitoring node, so a cycle costs one request and the armed
# state and any live incident are always read from the same instant.
STATE_QUERY: Final = """
query ArmedStateQuery($homeId: ID, $filter: MonitoringIncidentFilter!) {
  homes(id: $homeId) {
    __typename id name
    monitoring {
      __typename
      armedState
      delayedArmedState { __typename delayedUntil desiredArmedState }
      incidents(filter: $filter) {
        __typename id timestamp status level delayUntil actionOptions
        events {
          __typename type timestamp
          actor { __typename actorId name type isLoggedInUser }
        }
      }
      homeMonitoringSettings {
        __typename
        hasPinSetUp
        isProfessionalMonitoringEnabled
        armedStateSettings {
          __typename
          armedAway { __typename exitDelayDuration entryDelayDuration sirenDuration }
          armedStay { __typename exitDelayDuration entryDelayDuration sirenDuration }
        }
      }
    }
  }
}
"""

SET_ARMED_STATE_MUTATION: Final = """
mutation SetArmedStateForHome($homeId: ID!, $armedState: HomeArmedState!, $exitDelayUntil: String) {
  setArmedStateForHome(input: {
    homeId: $homeId
    armedState: $armedState
    exitDelayUntil: $exitDelayUntil
  }) {
    __typename
    errors
    home {
      __typename id
      monitoring {
        __typename
        armedState
        delayedArmedState { __typename delayedUntil desiredArmedState }
      }
    }
  }
}
"""
