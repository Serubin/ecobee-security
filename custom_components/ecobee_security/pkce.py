"""PKCE mechanics and the paste step, free of Home Assistant."""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

from .const import AUDIENCE, AUTHORIZE_URL, CLIENT_ID, REDIRECT_URI, SCOPE


class PasteError(Exception):
    """The pasted redirect URL cannot be turned into an authorization code."""


def generate_verifier() -> str:
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorize_url(verifier: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "audience": AUDIENCE,
            "state": state,
            "code_challenge": challenge_for(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def extract_code(pasted: str, expected_state: str) -> str:
    """Pull the authorization code out of whatever the user pasted.

    Accepts the whole redirect URL or a bare query string. A mismatched state means the
    paste came from an earlier attempt, which would otherwise fail later as an opaque
    token error.
    """
    pasted = pasted.strip()
    if not pasted:
        raise PasteError("empty")

    query = parse_qs(urlparse(pasted).query or pasted)

    if error := query.get("error"):
        description = query.get("error_description", [""])[0]
        raise PasteError(f"{error[0]}: {description}" if description else error[0])

    codes = query.get("code")
    if not codes or not codes[0]:
        raise PasteError("no_code")

    states = query.get("state")
    if not states or states[0] != expected_state:
        raise PasteError("state_mismatch")

    return codes[0]
