"""Thin async client for the Kroger public API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .const import API_BASE

_LOGGER = logging.getLogger(__name__)

# Kroger's product search intermittently answers 404 for a query that succeeds
# moments later — the button that motivated this failed once and then passed
# five times running, unchanged. A genuinely empty search is a 200 with no
# data, never a 404, so on a GET a 404 is always spurious and worth another go.
GET_ATTEMPTS = 3


def _is_transient(status: int) -> bool:
    """Whether a GET that got this status is worth repeating."""
    return status == 404 or status >= 500


class KrogerApiError(HomeAssistantError):
    """Raised when the Kroger API returns an error."""


class KrogerAuthError(KrogerApiError):
    """Raised when Kroger rejects the token and reauthentication is needed."""


class KrogerRateLimitError(KrogerApiError):
    """Raised when the daily call allowance is exhausted."""


class KrogerApi:
    """Wrap the Kroger public API with a Home Assistant OAuth2 session."""

    def __init__(
        self,
        session: ClientSession,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialise the client."""
        self._session = session
        self._oauth_session = oauth_session

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated request, refreshing the token if needed."""
        await self._oauth_session.async_ensure_token_valid()
        token = self._oauth_session.token["access_token"]

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        # Only reads are retried. A cart add whose response was lost may well
        # have landed, and the API cannot remove an item, so a retry there
        # risks a silent double add.
        attempts = GET_ATTEMPTS if method == "GET" else 1
        try:
            for attempt in range(1, attempts + 1):
                resp = await self._session.request(
                    method,
                    f"{API_BASE}{path}",
                    params=params,
                    json=json_body,
                    headers=headers,
                )
                if attempt < attempts and _is_transient(resp.status):
                    _LOGGER.warning(
                        "Kroger answered %s %s with %s; retrying (%s of %s)",
                        method, path, resp.status, attempt, attempts - 1,
                    )
                    resp.release()
                    await asyncio.sleep(attempt)
                    continue
                break
            resp.raise_for_status()
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise KrogerAuthError(
                    translation_domain="kroger",
                    translation_key="auth_failed",
                ) from err
            if err.status == 429:
                raise KrogerRateLimitError(
                    translation_domain="kroger",
                    translation_key="rate_limited",
                ) from err
            raise KrogerApiError(
                translation_domain="kroger",
                translation_key="api_error",
                translation_placeholders={
                    "status": str(err.status),
                    "message": err.message or "",
                },
            ) from err
        except ClientError as err:
            raise KrogerApiError(
                translation_domain="kroger",
                translation_key="cannot_connect",
            ) from err

        # PUT /cart/add answers 204 with no body.
        if resp.status == 204 or not resp.content_length:
            return None
        return await resp.json()

    async def async_add_to_cart(self, items: list[dict[str, Any]]) -> None:
        """Add items to the authenticated customer's cart.

        The endpoint is add-only: the public API cannot read the cart, remove an
        item, or check out. It also takes no location — items land in the cart
        for whichever store the Kroger account currently has selected.
        """
        await self._async_request("PUT", "/cart/add", json_body={"items": items})

    async def async_search_products(
        self,
        *,
        term: str | None = None,
        brand: str | None = None,
        product_id: str | None = None,
        location_id: str | None = None,
        fulfillment: str | None = None,
        limit: int = 10,
        start: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search the product catalogue.

        A location_id is what makes the response carry price, stock level and
        aisle; without one you get descriptions and UPCs only.
        """
        params: dict[str, Any] = {"filter.limit": limit}
        if term:
            params["filter.term"] = term
        if brand:
            params["filter.brand"] = brand
        if product_id:
            params["filter.productId"] = product_id
        if location_id:
            params["filter.locationId"] = location_id
        if fulfillment:
            params["filter.fulfillment"] = fulfillment
        if start:
            params["filter.start"] = start

        result = await self._async_request("GET", "/products", params=params)
        return result.get("data", []) if result else []

    async def async_search_locations(
        self,
        *,
        zip_code: str | None = None,
        chain: str | None = None,
        radius_in_miles: int = 20,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find stores near a ZIP code."""
        params: dict[str, Any] = {
            "filter.radiusInMiles": radius_in_miles,
            "filter.limit": limit,
        }
        if zip_code:
            params["filter.zipCode.near"] = zip_code
        if chain:
            params["filter.chain"] = chain

        result = await self._async_request("GET", "/locations", params=params)
        return result.get("data", []) if result else []

    async def async_get_profile(self) -> dict[str, Any]:
        """Return the authenticated customer's profile id.

        Used as a cheap check that the token really carries customer context.
        """
        result = await self._async_request("GET", "/identity/profile")
        return result.get("data", {}) if result else {}
