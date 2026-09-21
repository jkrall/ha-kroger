"""OAuth2 implementation for Kroger.

Kroger's token endpoint does not accept client credentials in the form body the
way Home Assistant's stock ``LocalOAuth2Implementation`` sends them. It requires
HTTP Basic authentication:

    Authorization: Basic base64(client_id:client_secret)

Sending the credentials in the body instead fails at the token exchange with an
opaque 401, so ``_token_request`` is reimplemented below. It deliberately raises
the same exception types as the stock helper, because
``AbstractOAuth2FlowHandler`` and ``OAuth2Session`` branch on them to decide
between retrying and starting a reauth flow.
"""

from __future__ import annotations

import base64
from http import HTTPStatus
import json
import logging
from typing import Any, cast

from aiohttp import ClientError, ClientResponseError

from homeassistant.exceptions import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
    OAuth2TokenRequestTransientError,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_BANNER, SCOPES

_LOGGER = logging.getLogger(__name__)


class KrogerOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """Kroger OAuth2, authenticating the token endpoint with HTTP Basic."""

    @property
    def name(self) -> str:
        """Return the name shown in the credentials picker."""
        return "Kroger"

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Return the scopes and the banner to brand the consent screen."""
        return {
            "scope": " ".join(SCOPES),
            "banner": DEFAULT_BANNER,
        }

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh an access token.

        Overridden only to keep ``client_id`` out of the body — the credentials
        travel in the Basic header instead. Kroger's refresh tokens are
        single-use and the response carries a replacement, so merging the new
        token over the old one (as the stock helper does) is what keeps the
        chain alive. Home Assistant persists the result to the config entry.
        """
        new_token = await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            }
        )
        return {**token, **new_token}

    async def _token_request(self, data: dict) -> dict:
        """Make a token request with HTTP Basic authentication."""
        session = async_get_clientsession(self.hass)

        credentials = f"{self.client_id}:{self.client_secret}"
        headers = {
            "Authorization": "Basic "
            + base64.b64encode(credentials.encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }

        _LOGGER.debug("Sending token request to %s", self.token_url)

        try:
            resp = await session.post(self.token_url, data=data, headers=headers)
            if resp.status >= 400:
                detail = "unknown error"
                try:
                    error_body = await resp.text()
                    error_data = json.loads(error_body)
                    error_code = error_data.get("error", "unknown error")
                    error_description = error_data.get("error_description")
                    detail = (
                        f"{error_code}: {error_description}"
                        if error_description
                        else error_code
                    )
                except (ClientError, ValueError, AttributeError):
                    detail = error_body[:200] if error_body else "unknown error"
                _LOGGER.debug(
                    "Token request for %s failed (%s): %s",
                    self.domain,
                    resp.status,
                    detail,
                )
            resp.raise_for_status()
        except ClientResponseError as err:
            if err.status == HTTPStatus.TOO_MANY_REQUESTS or 500 <= err.status <= 599:
                raise OAuth2TokenRequestTransientError(
                    request_info=err.request_info,
                    history=err.history,
                    status=err.status,
                    message=err.message,
                    headers=err.headers,
                    domain=self.domain,
                ) from err
            if 400 <= err.status <= 499:
                raise OAuth2TokenRequestReauthError(
                    request_info=err.request_info,
                    history=err.history,
                    status=err.status,
                    message=err.message,
                    headers=err.headers,
                    domain=self.domain,
                ) from err
            raise OAuth2TokenRequestError(
                request_info=err.request_info,
                history=err.history,
                status=err.status,
                message=err.message,
                headers=err.headers,
                domain=self.domain,
            ) from err

        return cast(dict, await resp.json())
