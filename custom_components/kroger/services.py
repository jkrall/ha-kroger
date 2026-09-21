"""Services for the Kroger integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_BRAND,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FULFILLMENT,
    ATTR_LIMIT,
    ATTR_LOCATION_ID,
    ATTR_MODALITY,
    ATTR_QUANTITY,
    ATTR_TERM,
    ATTR_UPC,
    CONF_LOCATION_ID,
    CONF_MODALITY,
    DEFAULT_MODALITY,
    DOMAIN,
    MODALITIES,
    SERVICE_ADD_TO_CART,
    SERVICE_SEARCH_PRODUCTS,
)

ADD_TO_CART_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_UPC): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_QUANTITY, default=1): vol.All(int, vol.Range(min=1, max=99)),
        vol.Optional(ATTR_MODALITY): vol.In(MODALITIES),
    }
)

SEARCH_PRODUCTS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_TERM): cv.string,
        vol.Optional(ATTR_BRAND): cv.string,
        vol.Optional(ATTR_LOCATION_ID): cv.string,
        vol.Optional(ATTR_FULFILLMENT): cv.string,
        vol.Optional(ATTR_LIMIT, default=10): vol.All(int, vol.Range(min=1, max=50)),
    }
)


def _resolve_entry(hass: HomeAssistant, call: ServiceCall):
    """Find the config entry this call should run against."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]

    if entry_id:
        for entry in loaded:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_found",
            translation_placeholders={"entry_id": entry_id},
        )

    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_entries"
        )
    if len(loaded) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="multiple_entries"
        )
    return loaded[0]


def _compact_product(product: dict[str, Any]) -> dict[str, Any]:
    """Reduce a catalogue entry to the fields an automation actually uses."""
    item = (product.get("items") or [{}])[0]
    price = item.get("price") or {}
    images = product.get("images") or []
    thumbnail = None
    for image in images:
        if image.get("perspective") == "front":
            sizes = {s.get("size"): s.get("url") for s in image.get("sizes", [])}
            thumbnail = sizes.get("medium") or sizes.get("thumbnail")
            break

    return {
        # productId is the 13-digit value the cart endpoint wants as "upc".
        "upc": product.get("productId"),
        "description": product.get("description"),
        "brand": product.get("brand"),
        "size": item.get("size"),
        "price": price.get("regular"),
        "promo_price": price.get("promo") or None,
        "stock_level": (item.get("inventory") or {}).get("stockLevel"),
        "aisle": next(
            (
                loc.get("description")
                for loc in (product.get("aisleLocations") or [])
                if loc.get("description")
            ),
            None,
        ),
        "fulfillment": item.get("fulfillment"),
        "image": thumbnail,
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Kroger services."""

    async def async_add_to_cart(call: ServiceCall) -> None:
        """Add one or more items to the cart."""
        entry = _resolve_entry(hass, call)
        api = entry.runtime_data

        modality = call.data.get(ATTR_MODALITY) or entry.options.get(
            CONF_MODALITY, DEFAULT_MODALITY
        )
        quantity = call.data[ATTR_QUANTITY]

        items = [
            {"upc": upc, "quantity": quantity, "modality": modality}
            for upc in call.data[ATTR_UPC]
        ]
        await api.async_add_to_cart(items)

    async def async_search_products(call: ServiceCall) -> ServiceResponse:
        """Search the catalogue and return compact results."""
        entry = _resolve_entry(hass, call)
        api = entry.runtime_data

        if not call.data.get(ATTR_TERM) and not call.data.get(ATTR_BRAND):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="search_needs_term"
            )

        location_id = call.data.get(ATTR_LOCATION_ID) or entry.options.get(
            CONF_LOCATION_ID
        )

        products = await api.async_search_products(
            term=call.data.get(ATTR_TERM),
            brand=call.data.get(ATTR_BRAND),
            location_id=location_id,
            fulfillment=call.data.get(ATTR_FULFILLMENT),
            limit=call.data[ATTR_LIMIT],
        )
        return {"products": [_compact_product(p) for p in products]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_TO_CART,
        async_add_to_cart,
        schema=ADD_TO_CART_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_PRODUCTS,
        async_search_products,
        schema=SEARCH_PRODUCTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
