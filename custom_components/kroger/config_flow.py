"""Config flow for the Kroger integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE,
    CONF_CHAIN,
    CONF_LOCATION_ID,
    CONF_MODALITY,
    CONF_ZIP_CODE,
    DEFAULT_CHAIN,
    DEFAULT_MODALITY,
    DOMAIN,
    MODALITIES,
)

_LOGGER = logging.getLogger(__name__)


async def _async_api_get(
    hass, access_token: str, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """GET a Kroger endpoint with a bare access token.

    Used during the config flow, where the token has just been minted and there
    is no config entry yet to hang an OAuth2Session off.
    """
    session = async_get_clientsession(hass)
    try:
        resp = await session.get(
            f"{API_BASE}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        return await resp.json()
    except (ClientError, ValueError) as err:
        _LOGGER.debug("Kroger config-flow request to %s failed: %s", path, err)
        return None


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the Kroger OAuth2 config flow."""

    # AbstractOAuth2FlowHandler reads this class attribute in __init__ and
    # refuses to instantiate without it. The domain= class keyword above only
    # registers the handler; the two are separate requirements.
    DOMAIN = DOMAIN

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        super().__init__()
        self._token_data: dict[str, Any] = {}
        self._locations: list[dict[str, Any]] = []

    @property
    def logger(self) -> logging.Logger:
        """Return the logger."""
        return _LOGGER

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a reauthentication.

        Kroger refresh tokens expire after six months and are invalidated when
        used, so a lost or stale refresh token lands here.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication before bouncing back to Kroger."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle the token, then ask which store to shop."""
        if self.source == "reauth":
            reauth_entry = self._get_reauth_entry()
            return self.async_update_reload_and_abort(reauth_entry, data=data)

        self._token_data = data

        access_token = data["token"]["access_token"]
        profile = await _async_api_get(self.hass, access_token, "/identity/profile")
        if profile and (profile_id := profile.get("data", {}).get("id")):
            await self.async_set_unique_id(profile_id)
            self._abort_if_unique_id_configured()

        return await self.async_step_store()

    async def async_step_store(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a ZIP code so we can list nearby stores."""
        if user_input is None:
            return self.async_show_form(
                step_id="store",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_ZIP_CODE): str,
                        vol.Optional(CONF_CHAIN, default=DEFAULT_CHAIN): str,
                    }
                ),
            )

        result = await _async_api_get(
            self.hass,
            self._token_data["token"]["access_token"],
            "/locations",
            {
                "filter.zipCode.near": user_input[CONF_ZIP_CODE],
                "filter.chain": user_input[CONF_CHAIN],
                "filter.radiusInMiles": 25,
                "filter.limit": 25,
            },
        )
        self._locations = result.get("data", []) if result else []

        if not self._locations:
            # Either the lookup failed or the chain filter matched nothing.
            # Let the user type the store id rather than dead-ending the flow.
            return await self.async_step_manual_store()

        return await self.async_step_pick_store()

    async def async_step_pick_store(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a store from the ones found near the ZIP code."""
        if user_input is None:
            options = [
                selector.SelectOptionDict(
                    value=loc["locationId"],
                    label=_format_location(loc),
                )
                for loc in self._locations
            ]
            return self.async_show_form(
                step_id="pick_store",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_LOCATION_ID): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=options,
                                mode=selector.SelectSelectorMode.DROPDOWN,
                            )
                        ),
                        vol.Required(
                            CONF_MODALITY, default=DEFAULT_MODALITY
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=MODALITIES,
                                translation_key="modality",
                            )
                        ),
                    }
                ),
            )

        return self._async_create_entry(user_input)

    async def async_step_manual_store(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fall back to typing the 8-character store id by hand."""
        if user_input is None:
            return self.async_show_form(
                step_id="manual_store",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_LOCATION_ID): str,
                        vol.Required(
                            CONF_MODALITY, default=DEFAULT_MODALITY
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=MODALITIES,
                                translation_key="modality",
                            )
                        ),
                    }
                ),
                errors={"base": "no_locations"},
            )

        return self._async_create_entry(user_input)

    @callback
    def _async_create_entry(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Create the config entry with the chosen store."""
        location_id = user_input[CONF_LOCATION_ID]
        title = "Kroger"
        for loc in self._locations:
            if loc["locationId"] == location_id:
                title = loc.get("name") or title
                break

        return self.async_create_entry(
            title=title,
            data=self._token_data,
            options={
                CONF_LOCATION_ID: location_id,
                CONF_MODALITY: user_input[CONF_MODALITY],
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> KrogerOptionsFlow:
        """Return the options flow."""
        return KrogerOptionsFlow()


class KrogerOptionsFlow(OptionsFlowWithReload):
    """Change the default store and modality after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCATION_ID,
                        default=options.get(CONF_LOCATION_ID, ""),
                    ): str,
                    vol.Required(
                        CONF_MODALITY,
                        default=options.get(CONF_MODALITY, DEFAULT_MODALITY),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=MODALITIES,
                            translation_key="modality",
                        )
                    ),
                }
            ),
        )


def _format_location(loc: dict[str, Any]) -> str:
    """Render a store as 'Name — street, city (id)'."""
    address = loc.get("address", {})
    parts = [
        part
        for part in (address.get("addressLine1"), address.get("city"))
        if part
    ]
    where = ", ".join(parts)
    name = loc.get("name", "Store")
    return f"{name} — {where} ({loc['locationId']})" if where else name
