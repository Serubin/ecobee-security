"""The token bundle must carry what OAuth2Session reads back."""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent / "custom_components" / "ecobee_security"

# auth.py imports Home Assistant for its implementation class; load just the helper.
_src = (ROOT / "auth.py").read_text()
_helper = _src.split("def with_expiry", 1)[1]
_ns: dict = {}
exec(compile("import time\ndef with_expiry" + _helper, "auth.py", "exec"), _ns)
with_expiry = _ns["with_expiry"]


def test_absolute_expiry_is_stamped():
    """HA's own flow handler adds this; a hand-rolled flow must do it itself."""
    token = with_expiry({"access_token": "a", "refresh_token": "r", "expires_in": 7200})
    assert token["expires_at"] > 0
    assert token["expires_at"] - token["expires_in"] > 0


def test_expires_in_is_coerced_to_int():
    token = with_expiry({"access_token": "a", "expires_in": "7200"})
    assert token["expires_in"] == 7200


def test_a_token_without_expiry_is_treated_as_expired():
    token = with_expiry({"access_token": "a"})
    assert token["expires_in"] == 0


def test_the_refresh_token_survives():
    token = with_expiry({"access_token": "a", "refresh_token": "keep", "expires_in": 1})
    assert token["refresh_token"] == "keep"
