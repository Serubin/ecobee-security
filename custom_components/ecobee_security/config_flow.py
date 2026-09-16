"""Config flow: log in at ecobee, paste the URL you land on, pick a home."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import voluptuous as vol
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EcobeeSecurityApi, EcobeeSecurityError
from .auth import EcobeeOAuth2Implementation
from .const import (
    CONF_ARMED_POLL_INTERVAL,
    CONF_EXIT_DELAY_AWAY,
    CONF_EXIT_DELAY_STAY,
    CONF_HOME_ID,
    CONF_HOME_NAME,
    CONF_POLL_INTERVAL,
    DEFAULT_ARMED_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    FEATURE_HOME_MONITORING,
)
from .pkce import PasteError, build_authorize_url, extract_code, generate_verifier

_LOGGER = logging.getLogger(__name__)

CONF_REDIRECT = "redirect_url"


class EcobeeSecurityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Auth0 PKCE, with the redirect carried back by hand."""

    VERSION = 1

    def __init__(self) -> None:
        self._verifier: str | None = None
        self._state: str | None = None
        self._token: dict[str, Any] | None = None
        self._homes: list[dict[str, str]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_code()

        # Generated once per attempt: regenerating here would invalidate the link the
        # user just followed, since the verifier must match the challenge it was built
        # from. An abandoned flow cannot be completed later.
        self._verifier = generate_verifier()
        self._state = secrets.token_urlsafe(16)

        return self.async_show_form(
            step_id="user",
            description_placeholders={
                "authorize_url": build_authorize_url(self._verifier, self._state)
            },
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                code = extract_code(user_input[CONF_REDIRECT], self._state or "")
            except PasteError as err:
                errors["base"] = _paste_error_key(str(err))
            else:
                implementation = EcobeeOAuth2Implementation(self.hass)
                try:
                    self._token = await implementation.async_exchange_code(
                        code, self._verifier or ""
                    )
                except ClientResponseError as err:
                    _LOGGER.debug("Token exchange rejected: %s", err.status)
                    errors["base"] = (
                        "invalid_grant" if err.status < 500 else "cannot_connect"
                    )
                except (ClientError, TimeoutError) as err:
                    _LOGGER.debug("Token exchange could not be sent: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    return await self._async_discover_homes()

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_REDIRECT): str}),
            errors=errors,
            description_placeholders={
                "authorize_url": build_authorize_url(
                    self._verifier or "", self._state or ""
                )
            },
        )

    async def _async_discover_homes(self) -> ConfigFlowResult:
        api = EcobeeSecurityApi(
            async_get_clientsession(self.hass), self._access_token_getter()
        )
        try:
            self._homes = await api.async_get_homes()
        except EcobeeSecurityError as err:
            _LOGGER.debug("Home discovery failed: %s", err)
            return self.async_show_form(
                step_id="code",
                data_schema=vol.Schema({vol.Required(CONF_REDIRECT): str}),
                errors={"base": "cannot_connect"},
                description_placeholders={
                    "authorize_url": build_authorize_url(
                        self._verifier or "", self._state or ""
                    )
                },
            )

        # The feature value is confirmed on one account only, so a home that does not
        # advertise it is deprioritised rather than hidden: guessing wrong here would be
        # an unrecoverable setup failure on someone else's account.
        if advertised := [home for home in self._homes if home["monitoring"]]:
            self._homes = advertised
        else:
            _LOGGER.warning(
                "No home advertises %s; offering all of them anyway",
                FEATURE_HOME_MONITORING,
            )
        if len(self._homes) == 1:
            return await self._async_create(self._homes[0])
        return await self.async_step_pick_home()

    async def async_step_pick_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            home = next(
                (h for h in self._homes if h["id"] == user_input[CONF_HOME_ID]), None
            )
            if home is None:
                return self.async_abort(reason="unknown_home")
            return await self._async_create(home)

        return self.async_show_form(
            step_id="pick_home",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOME_ID): vol.In(
                        {home["id"]: home["name"] for home in self._homes}
                    )
                }
            ),
        )

    async def _async_create(self, home: dict[str, str]) -> ConfigFlowResult:
        await self.async_set_unique_id(home["id"])
        data = {
            "token": self._token,
            CONF_HOME_ID: home["id"],
            CONF_HOME_NAME: home["name"],
        }

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=home["name"], data=data)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_user(user_input)

    def _access_token_getter(self):
        async def _token() -> str:
            return (self._token or {})["access_token"]

        return _token

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlowWithReload:
        return EcobeeSecurityOptionsFlow()


def _paste_error_key(message: str) -> str:
    if message.startswith("state_mismatch"):
        return "state_mismatch"
    if message.startswith("no_code") or message.startswith("empty"):
        return "no_code"
    return "auth_error"


class EcobeeSecurityOptionsFlow(OptionsFlowWithReload):
    """Poll interval, and per-mode exit delays for anyone who wants a different one."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(int, vol.Range(min=15, max=3600)),
                    vol.Optional(
                        CONF_ARMED_POLL_INTERVAL,
                        default=options.get(
                            CONF_ARMED_POLL_INTERVAL, DEFAULT_ARMED_POLL_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=5, max=3600)),
                    vol.Optional(
                        CONF_EXIT_DELAY_AWAY,
                        description={
                            "suggested_value": options.get(CONF_EXIT_DELAY_AWAY)
                        },
                    ): vol.All(int, vol.Range(min=0, max=600)),
                    vol.Optional(
                        CONF_EXIT_DELAY_STAY,
                        description={
                            "suggested_value": options.get(CONF_EXIT_DELAY_STAY)
                        },
                    ): vol.All(int, vol.Range(min=0, max=600)),
                }
            ),
        )
