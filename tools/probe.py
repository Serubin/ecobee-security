#!/usr/bin/env python3
"""Prove the PKCE login and the read path outside Home Assistant.

usage: python3 tools/probe.py [--findings PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import pathlib
import secrets
import sys
import time
import aiohttp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from custom_components.ecobee_security.const import (  # noqa: E402
    CLIENT_ID,
    GRAPHQL_URL,
    HOMES_QUERY,
    REDIRECT_URI,
    STATE_QUERY,
    TOKEN_URL,
)
from custom_components.ecobee_security.pkce import (  # noqa: E402
    build_authorize_url,
    extract_code,
    generate_verifier,
)

TOKEN_CACHE = pathlib.Path.home() / ".ecobee-security-probe.json"

# Everything the findings file may contain. An allowlist, so a field we did not think
# about cannot leak into a file that ends up in a repo.
FINDINGS_ALLOWLIST = (
    "bare_client_accepted",
    "home_count",
    "armed_state",
    "desired_armed_state",
    "has_delayed_state",
    "has_pin_set_up",
    "pro_monitoring_enabled",
    "exit_delay_away",
    "exit_delay_stay",
    "entry_delay_away",
    "clock_offset_seconds",
    "token_scope",
    "token_audience",
    "token_lifetime_seconds",
    "graphql_error_codes",
)


def _claims(token: str) -> dict:
    """Decode a JWT's payload without verifying it; for non-sensitive claims only."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


def _write_private(path: pathlib.Path, text: str) -> None:
    """Create at 0600 rather than narrowing afterwards, which races the umask."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)


def _save_token(token: dict) -> None:
    _write_private(TOKEN_CACHE, json.dumps(token))


def _load_token() -> dict | None:
    if not TOKEN_CACHE.exists():
        return None
    return json.loads(TOKEN_CACHE.read_text())


async def login(session: aiohttp.ClientSession) -> dict:
    verifier = generate_verifier()
    state = secrets.token_urlsafe(16)

    print("\nOpen this in a DESKTOP browser and sign in to ecobee:\n")
    print(build_authorize_url(verifier, state))
    print(
        "\nYou will land on a page that fails to load. That is expected.\n"
        "Copy its full address from the address bar and paste it here.\n"
    )
    pasted = input("Redirected address: ").strip()
    code = extract_code(pasted, state)

    async with session.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    ) as response:
        body = await response.json()
        if response.status != 200:
            raise SystemExit(f"Token exchange failed: {body.get('error')}")
    return body


async def graphql(
    session: aiohttp.ClientSession,
    token: str,
    document: str,
    variables: dict,
    operation: str,
) -> tuple[dict, float | None]:
    """Deliberately sends ONLY the two headers believed to be load-bearing."""
    async with session.post(
        GRAPHQL_URL,
        json={"query": document, "variables": variables, "operationName": operation},
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
    ) as response:
        offset = None
        if date := response.headers.get("Date"):
            from email.utils import parsedate_to_datetime

            offset = parsedate_to_datetime(date).timestamp() - time.time()
        return await response.json(), offset


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", type=pathlib.Path)
    parser.add_argument("--login", action="store_true", help="force a fresh login")
    args = parser.parse_args()

    findings: dict = {}

    async with aiohttp.ClientSession() as session:
        token = None if args.login else _load_token()
        if token is None:
            token = await login(session)
            _save_token(token)
            print(f"\nToken cached at {TOKEN_CACHE} (mode 0600)")

        access_token = token["access_token"]
        claims = _claims(access_token)
        findings["token_scope"] = claims.get("scope")
        findings["token_audience"] = claims.get("aud")
        if claims.get("exp") and claims.get("iat"):
            findings["token_lifetime_seconds"] = claims["exp"] - claims["iat"]
        print(f"\nScope: {claims.get('scope')}")
        print(f"Refresh token present: {'refresh_token' in token}")

        homes_payload, _ = await graphql(session, access_token, HOMES_QUERY, {}, "HomesQuery")
        if errors := homes_payload.get("errors"):
            findings["graphql_error_codes"] = [
                (e.get("extensions") or {}).get("code") for e in errors
            ]
            print(f"HomesQuery errors: {findings['graphql_error_codes']}")
        homes = (homes_payload.get("data") or {}).get("homes") or []
        findings["bare_client_accepted"] = bool(homes)
        findings["home_count"] = len(homes)
        if not homes:
            print("No homes returned; cannot continue.")
            return 1
        print(f"Homes: {len(homes)}")

        home_id = homes[0]["id"]
        state_payload, offset = await graphql(
            session, access_token, STATE_QUERY, {"homeId": home_id}, "ArmedStateQuery"
        )
        findings["clock_offset_seconds"] = round(offset, 1) if offset else None

        monitoring = ((state_payload.get("data") or {}).get("homes") or [{}])[0].get(
            "monitoring"
        ) or {}
        settings = monitoring.get("homeMonitoringSettings") or {}
        armed_settings = settings.get("armedStateSettings") or {}
        delayed = monitoring.get("delayedArmedState") or {}

        findings.update(
            armed_state=monitoring.get("armedState"),
            desired_armed_state=delayed.get("desiredArmedState"),
            has_delayed_state=bool(delayed.get("desiredArmedState")),
            has_pin_set_up=settings.get("hasPinSetUp"),
            pro_monitoring_enabled=settings.get("isProfessionalMonitoringEnabled"),
            exit_delay_away=(armed_settings.get("armedAway") or {}).get(
                "exitDelayDuration"
            ),
            exit_delay_stay=(armed_settings.get("armedStay") or {}).get(
                "exitDelayDuration"
            ),
            entry_delay_away=(armed_settings.get("armedAway") or {}).get(
                "entryDelayDuration"
            ),
        )

        print(f"\nArmed state: {findings['armed_state']}")
        print(f"Exit delay away/stay: {findings['exit_delay_away']}/{findings['exit_delay_stay']}")
        print(f"Clock offset vs server: {findings['clock_offset_seconds']}s")
        print("\nBare client with only authorization + content-type: ACCEPTED")

    if args.findings:
        redacted = {k: findings.get(k) for k in FINDINGS_ALLOWLIST if k in findings}
        _write_private(args.findings, json.dumps(redacted, indent=2) + "\n")
        print(f"\nFindings written to {args.findings}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
