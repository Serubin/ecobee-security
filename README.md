# ecobee Smart Security for Home Assistant

An `alarm_control_panel` for ecobee Smart Security, built on the private GraphQL API the
ecobee Android app uses. ecobee's public API is thermostat-only and does not expose Smart
Security at all, so there is no supported way to do this.

## Read this before installing

**This is an unofficial client for an undocumented API.** It can break without notice, and
nothing here is endorsed by ecobee or Generac.

**Smoke detection is untested.** The alarm's life-safety path has never been exercised, so
it is unknown whether a smoke event opens an incident at all. This integration polls for
incidents continuously, armed or not, on the assumption that it might — but do not rely on
this as your smoke notifier until someone has actually set off a detector and watched what
the API does.

**Both delays are the client's, not the server's.** Arming without an explicit deadline
arms the system *instantly* — the server holds your configured exit delay and ignores it.
Disarming an armed system takes effect immediately with no entry-delay grace. This
integration therefore sends an exit delay derived from your account's configured duration,
but understand that the protection is a client-side courtesy: anyone holding a token arms
and disarms with no countdown and no challenge. That is how the API works; this
integration inherits it rather than introducing it.

**Your refresh token is stored in plaintext.** Home Assistant does not encrypt
`.storage`. That single token carries `smartWrite` scope across your thermostat, cameras,
and alarm, plus `offline_access` for indefinite renewal. **Anyone with a copy of a Home
Assistant backup has durable control of your alarm.** Treat backups accordingly.

**There is no arm/disarm code, and none can be set.** The upstream account does not
require a PIN, so `code_arm_required` is off, no code format is declared, and the `code`
argument to the disarm service is ignored. **Any Home Assistant user or automation can
disarm the house with a one-line service call.** Home Assistant's `default_code` entity
option does not help — it only pre-fills a code for an entity that enforces one.

**Whether the server enforces a PIN is untested.** If your account has require-PIN-to-arm
enabled, arming through this integration has undefined behaviour — it will either work
(meaning the PIN is cosmetic) or fail with an error from the server. The "PIN configured"
diagnostic reports whether a PIN *exists*, which is a different question.

**Account risk.** Using an undocumented API may violate ecobee's terms. In the worst case a
locked account would also lock you out of the official app.

## What it creates

One device per home, with:

- `alarm_control_panel` — `disarmed`, `armed_home`, `armed_away`, `arming`, `pending`
  (entry-delay countdown) and `triggered` (siren sounding). The target of a pending arm is
  on the `desired_armed_state` attribute, because Home Assistant has only one `arming`
  state; recorder history cannot distinguish arming-to-home from arming-to-away. When an
  incident is live, `incident_sources` names the sensors that tripped.

  `triggered` comes from a separate incidents feed, not from the arm state: ecobee's
  `armedState` keeps reporting the ordinary armed value throughout a live siren. Because
  the feed is polled, expect up to a poll interval of delay between the siren starting and
  Home Assistant knowing.
- Diagnostic sensors — exit delay (Away/Home), entry delay, siren duration, and when a
  pending arm is due to complete.
- Diagnostic binary sensors — professional monitoring, and whether a PIN is configured.

**Arming at the thermostat is invisible until it completes.** ecobee runs that exit delay
locally on the panel and does not publish it, so Home Assistant shows `disarmed` for the
whole countdown and then jumps to `armed_away`. The ecobee app behaves the same way. Only
arms started from Home Assistant or the app show an `arming` state.

## Installing

Add this repository to HACS as a custom repository of type "Integration", install, restart
Home Assistant, then add **ecobee Smart Security** from Settings → Devices & Services.

### Signing in

The config flow gives you an ecobee sign-in link. **Open it in a desktop browser.** The
only callback ecobee's OAuth client accepts is an Android App Link, so on a phone the link
opens the ecobee app and you never see the address the flow needs.

After signing in, your browser lands on a page that fails to load. That is expected — copy
its full address out of the address bar and paste it back into the flow. Authorization
codes expire quickly, so do it promptly.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

`api.py`, `model.py`, `pkce.py` and `const.py` import no Home Assistant, so the state
table, the exit-delay arithmetic, and the GraphQL error handling are tested directly. The
test suite does not install Home Assistant, which is what keeps that boundary honest.

`tools/probe.py` runs the sign-in and a read against the live account from outside Home
Assistant. It caches its token at `~/.ecobee-security-probe.json` mode 0600 and writes
findings through an allowlist, never a blocklist.

## Provenance

The protocol was reverse engineered from the ecobee Android app in September 2026 on
owned hardware and an owned account. Notably: the app pins no certificates, a single
token covers thermostat, cameras, and alarm with no step-up for security-critical
mutations, and the delays described above are client-side.

The mutation is sent as captured. The two read documents are not: discovery is a minimal
query of our own, because the app's pulls the whole device tree including your street
address and camera thumbnails, and the state query merges four of the app's operations
into one request. Their field names come from the capture.

The exit delay is driven entirely by the timestamp this client sends, in every mode —
the account's configured durations are advisory numbers a client may read and ignore.
The panel therefore declines to arm at all if it cannot read them, since guessing there
would mean guessing zero and arming instantly. It also refuses to sit on `arming`
indefinitely: the server completes a delayed arm on its own, so a pending state that
outlives its deadline means something stalled, and the panel reports the server's literal
state and logs an error rather than implying an arm is about to finish.
