"""Auth0 authorization-code + PKCE against the ecobee tenant.

The only callback the client accepts is an Android App Link, so the browser cannot return
to Home Assistant and the user pastes the redirected URL back into the config flow.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2Implementation

from .const import (
    AUDIENCE,
    AUTHORIZE_URL,
    CLIENT_ID,
    DOMAIN,
    REDIRECT_URI,
    SCOPE,
    TOKEN_URL,
)


class EcobeeOAuth2Implementation(LocalOAuth2Implementation):
    """The tenant's public native client, with no secret and a fixed callback."""

    def __init__(self, hass: Any) -> None:
        super().__init__(
            hass,
            DOMAIN,
            CLIENT_ID,
            "",
            AUTHORIZE_URL,
            TOKEN_URL,
        )

    @property
    def name(self) -> str:
        return "ecobee"

    @property
    def redirect_uri(self) -> str:
        return REDIRECT_URI

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        return {"scope": SCOPE, "audience": AUDIENCE}

    async def async_exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        """Trade an authorization code for a token bundle OAuth2Session can consume."""
        token = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            }
        )
        return with_expiry(token)


def with_expiry(token: dict[str, Any]) -> dict[str, Any]:
    """Stamp the absolute expiry HA stores but only its own flow handler adds.

    Without it OAuth2Session raises KeyError on the first refresh check.
    """
    expires_in = int(token.get("expires_in", 0))
    return {**token, "expires_in": expires_in, "expires_at": time.time() + expires_in}
