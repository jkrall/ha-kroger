"""The Kroger integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .api import KrogerApi, KrogerApiError, KrogerAuthError
from .const import DOMAIN
from .services import async_setup_services

type KrogerConfigEntry = ConfigEntry[KrogerApi]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the Kroger services once, for all config entries."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KrogerConfigEntry) -> bool:
    """Set up Kroger from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    api = KrogerApi(async_get_clientsession(hass), oauth_session)

    # Prove the stored refresh token still works before declaring setup done,
    # so a six-month expiry surfaces as a reauth prompt rather than as a failed
    # automation at 06:00.
    try:
        await api.async_get_profile()
    except KrogerAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except KrogerApiError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err

    entry.runtime_data = api
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KrogerConfigEntry) -> bool:
    """Unload a config entry."""
    return True
