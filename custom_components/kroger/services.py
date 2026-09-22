"""Services for the Kroger integration."""

from __future__ import annotations

import re
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
    ATTR_CHECK_AVAILABILITY,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FULFILLMENT,
    ATTR_LIMIT,
    ATTR_LOCATION_ID,
    ATTR_MODALITY,
    ATTR_PRODUCT_ID,
    ATTR_QUANTITY,
    ATTR_SIZE,
    ATTR_TERM,
    ATTR_UPC,
    CONF_LOCATION_ID,
    CONF_MODALITY,
    DEFAULT_MODALITY,
    DOMAIN,
    MODALITIES,
    MODALITY_FULFILLMENT,
    SERVICE_ADD_TO_CART,
    SERVICE_SEARCH_PRODUCTS,
    STOCK_OUT,
)

ADD_TO_CART_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_UPC): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_TERM): cv.string,
        vol.Optional(ATTR_BRAND): cv.string,
        vol.Optional(ATTR_SIZE): cv.string,
        vol.Optional(ATTR_QUANTITY, default=1): vol.All(int, vol.Range(min=1, max=99)),
        vol.Optional(ATTR_MODALITY): vol.In(MODALITIES),
        vol.Optional(ATTR_CHECK_AVAILABILITY, default=True): cv.boolean,
    }
)

SEARCH_PRODUCTS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_TERM): cv.string,
        vol.Optional(ATTR_BRAND): cv.string,
        vol.Optional(ATTR_PRODUCT_ID): cv.string,
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
    thumbnail = None
    for image in product.get("images") or []:
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


def _fulfillable(product: dict[str, Any], modality: str) -> bool:
    """Whether the store is known to be unable to supply this for the modality.

    Deliberately asserts only what the API is reliable about. An explicit
    TEMPORARILY_OUT_OF_STOCK is a real no. The pickup flags were verified to
    agree with the storefront. The per-store delivery flag was verified NOT to
    — see MODALITY_FULFILLMENT — so for delivery nothing beyond stock is
    checked, and this returns True.

    Absence is never treated as a no: Kroger omits stockLevel entirely when it
    has no data, and does so even for products it will happily deliver.
    """
    if product.get("stock_level") == STOCK_OUT:
        return False
    keys = MODALITY_FULFILLMENT.get(modality)
    if not keys:
        return True
    flags = {
        str(k).lower(): bool(v)
        for k, v in (product.get("fulfillment") or {}).items()
    }
    return any(flags.get(k) for k in keys)


def _normalise(text: str | None) -> str:
    """Lowercase and collapse anything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _describe(product: dict[str, Any]) -> str:
    """One-line rendering of a product, for disambiguation messages."""
    bits = [product.get("description") or "?"]
    if product.get("size"):
        bits.append(str(product["size"]))
    if product.get("price") is not None:
        bits.append(f"${product['price']}")
    return f"{product.get('upc')} — {', '.join(bits)}"


def _match_by_name(
    products: list[dict[str, Any]], term: str, size: str | None = None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Resolve a product name to exactly one product.

    Kroger's term search is fuzzy and its documentation warns that the order of
    results can change between identical requests, so the top hit is not a safe
    answer. Narrow through progressively looser tiers and accept a match only
    when the narrowest non-empty tier holds exactly one product; otherwise hand
    back the candidates and let the caller refuse.

    A name is often not enough on its own. Kroger ships genuinely different
    products under descriptions that differ only in capitalisation — UPC
    0005000032275 and 0005000035022 are both "Coffee Mate French Vanilla
    Flavored Coffee Creamer Non-Dairy Gluten-Free", at 32 fl oz for $4.49 and
    64 fl oz for $7.79. Size is what separates them, so it filters first.
    """
    wanted = _normalise(term)
    tokens = wanted.split()

    if size:
        want_size = _normalise(size)
        sized = [
            p for p in products
            if want_size == _normalise(p.get("size"))
            or want_size in _normalise(p.get("size"))
        ]
        if sized:
            products = sized

    def haystack(p: dict[str, Any]) -> str:
        return _normalise(" ".join(
            str(p.get(k) or "") for k in ("description", "brand", "size")
        ))

    tiers = [
        [p for p in products if _normalise(p.get("description")) == wanted],
        [p for p in products if wanted and wanted in haystack(p)],
        [p for p in products if tokens and all(t in haystack(p) for t in tokens)],
    ]
    for tier in tiers:
        if len(tier) == 1:
            return tier[0], tier
        if tier:
            return None, tier
    return None, products


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Kroger services."""

    async def async_add_to_cart(call: ServiceCall) -> None:
        """Add one or more items to the cart."""
        entry = _resolve_entry(hass, call)
        api = entry.runtime_data

        upcs = call.data.get(ATTR_UPC)
        term = call.data.get(ATTR_TERM)
        if bool(upcs) == bool(term):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="need_upc_or_term"
            )

        modality = call.data.get(ATTR_MODALITY) or entry.options.get(
            CONF_MODALITY, DEFAULT_MODALITY
        )
        quantity = call.data[ATTR_QUANTITY]
        check = call.data[ATTR_CHECK_AVAILABILITY]
        location_id = entry.options.get(CONF_LOCATION_ID)

        if term:
            found = await api.async_search_products(
                term=term,
                brand=call.data.get(ATTR_BRAND),
                location_id=location_id,
                limit=50,
            )
            products = [_compact_product(p) for p in found]
            if not products:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="name_no_results",
                    translation_placeholders={"term": term},
                )

            # Availability filters before disambiguation, so an unorderable
            # near-duplicate cannot make a real match look ambiguous.
            pool = [p for p in products if _fulfillable(p, modality)] if check else products
            if not pool:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="name_none_available",
                    translation_placeholders={
                        "term": term,
                        "modality": modality.lower(),
                        "candidates": "; ".join(_describe(p) for p in products[:5]),
                    },
                )

            match, candidates = _match_by_name(
                pool, term, call.data.get(ATTR_SIZE)
            )
            if match is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="name_ambiguous",
                    translation_placeholders={
                        "term": term,
                        "count": str(len(candidates)),
                        "candidates": "; ".join(_describe(p) for p in candidates[:5]),
                    },
                )
            resolved = [match]
        else:
            resolved = []
            for upc in upcs:
                if not check:
                    resolved.append({"upc": upc, "description": upc})
                    continue
                found = await api.async_search_products(
                    product_id=upc, location_id=location_id, limit=1
                )
                if not found:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="upc_not_found",
                        translation_placeholders={"upc": upc},
                    )
                product = _compact_product(found[0])
                if not _fulfillable(product, modality):
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="not_available",
                        translation_placeholders={
                            "description": product.get("description") or upc,
                            "upc": upc,
                            "modality": modality.lower(),
                        },
                    )
                resolved.append(product)

        await api.async_add_to_cart(
            [
                {"upc": p["upc"], "quantity": quantity, "modality": modality}
                for p in resolved
            ]
        )

    async def async_search_products(call: ServiceCall) -> ServiceResponse:
        """Search the catalogue and return compact results."""
        entry = _resolve_entry(hass, call)
        api = entry.runtime_data

        if not any(
            call.data.get(k) for k in (ATTR_TERM, ATTR_BRAND, ATTR_PRODUCT_ID)
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="search_needs_term"
            )

        location_id = call.data.get(ATTR_LOCATION_ID) or entry.options.get(
            CONF_LOCATION_ID
        )
        products = await api.async_search_products(
            term=call.data.get(ATTR_TERM),
            brand=call.data.get(ATTR_BRAND),
            product_id=call.data.get(ATTR_PRODUCT_ID),
            location_id=location_id,
            fulfillment=call.data.get(ATTR_FULFILLMENT),
            limit=call.data[ATTR_LIMIT],
        )
        modality = entry.options.get(CONF_MODALITY, DEFAULT_MODALITY)
        compact = [_compact_product(p) for p in products]
        for p in compact:
            p["available"] = _fulfillable(p, modality)
        return {"products": compact}

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_TO_CART, async_add_to_cart, schema=ADD_TO_CART_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_PRODUCTS,
        async_search_products,
        schema=SEARCH_PRODUCTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
