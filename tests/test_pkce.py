"""PKCE helpers and the paste step."""

import base64
import hashlib

import pytest

from custom_components.ecobee_security.pkce import (
    PasteError,
    build_authorize_url,
    challenge_for,
    extract_code,
    generate_verifier,
)

REDIRECT = "https://auth.ecobee.com/android/com.ecobee.athenamobile/callback"


def test_challenge_is_s256_of_the_verifier():
    verifier = "a-known-verifier"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge_for(verifier) == expected
    assert "=" not in challenge_for(verifier)


def test_verifiers_are_unique():
    assert generate_verifier() != generate_verifier()


def test_authorize_url_carries_the_audience_and_app_callback():
    url = build_authorize_url("verifier", "state-1")
    assert "audience=https%3A%2F%2Fprod.ecobee.com%2Fapi%2Fv1" in url
    assert "code_challenge_method=S256" in url
    assert "com.ecobee.athenamobile%2Fcallback" in url
    assert "verifier" not in url


def test_extract_code_from_a_full_redirect_url():
    pasted = f"{REDIRECT}?code=abc123&state=state-1"
    assert extract_code(pasted, "state-1") == "abc123"


def test_extract_code_from_a_bare_query_string():
    assert extract_code("code=abc123&state=state-1", "state-1") == "abc123"


def test_stale_paste_is_rejected():
    with pytest.raises(PasteError, match="state_mismatch"):
        extract_code(f"{REDIRECT}?code=abc123&state=old", "state-1")


def test_auth0_error_is_surfaced_verbatim():
    pasted = f"{REDIRECT}?error=access_denied&error_description=User%20cancelled"
    with pytest.raises(PasteError, match="access_denied: User cancelled"):
        extract_code(pasted, "state-1")


def test_paste_without_a_code_is_rejected():
    with pytest.raises(PasteError, match="no_code"):
        extract_code(REDIRECT, "state-1")


def test_empty_paste_is_rejected():
    with pytest.raises(PasteError, match="empty"):
        extract_code("   ", "state-1")


def test_paste_without_a_state_is_rejected():
    """We always send state, so its absence means the paste is not from our flow."""
    with pytest.raises(PasteError, match="state_mismatch"):
        extract_code(f"{REDIRECT}?code=abc123", "state-1")
