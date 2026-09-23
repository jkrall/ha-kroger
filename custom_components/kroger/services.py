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
    ATTR_ALTERNATIVES,
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
    MAX_TERM_WORDS,
    MODALITY_FULFILLMENT,
    SERVICE_ADD_TO_CART,
    SERVICE_SEARCH_PRODUCTS,
    STOCK_OUT,
)


def _alternative(value: Any) -> dict[str, Any]:
    """Normalise one fallback item to the shape of a primary item.

    A bare string of digits is a UPC and any other bare string is a product
    name; no real product name is all digits, so the shorthand is unambiguous.
    A mapping is needed only to give a name its size or brand.
    """
    if isinstance(value, str):
        value = value.strip()
        return {ATTR_UPC: [value]} if value.isdigit() else {ATTR_TERM: value}
    if ATTR_UPC in value:
        return {**value, ATTR_UPC: [value[ATTR_UPC]]}
    return value


ALTERNATIVE_SCHEMA = vol.All(
    vol.Any(
        cv.string,
        vol.All(
            vol.Schema(
                {
                    vol.Exclusive(ATTR_TERM, "item"): cv.string,
                    vol.Exclusive(ATTR_UPC, "item"): cv.string,
                    vol.Optional(ATTR_BRAND): cv.string,
                    vol.Optional(ATTR_SIZE): cv.string,
                }
            ),
            cv.has_at_least_one_key(ATTR_TERM, ATTR_UPC),
        ),
    ),
    _alternative,
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
        vol.Optional(ATTR_ALTERNATIVES, default=list): vol.All(
            cv.ensure_list, [ALTERNATIVE_SCHEMA]
        ),
    }
)

# The failures that mean "this item cannot be had right now", which is what an
# alternative is for, mapped to how the skip is reported. Anything else —
# notably an ambiguous name — is a mistake in the call, and trying the next
# item would paper over it, so it fails outright.
FALL_THROUGH: dict[str, str] = {
    "name_no_results": "not found",
    "upc_not_found": "not found",
    "name_none_available": "unavailable",
    "not_available": "unavailable",
}

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


def _search_term(term: str) -> str:
    """Trim a product name to something Kroger's search will accept.

    filter.term is capped at eight words and a longer one is rejected with a
    bare 400, so a pasted product name — usually longer — has to be cut down.
    Tokens that are purely punctuation are dropped first, since they consume a
    word each while narrowing nothing. The untrimmed name is still what
    _match_by_name compares against, so precision is not lost, only reach.
    """
    words = [w for w in term.split() if re.search(r"[a-z0-9]", w, re.I)]
    return " ".join(words[:MAX_TERM_WORDS])


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


async def _async_resolve(
    api: Any,
    item: dict[str, Any],
    modality: str,
    check: bool,
    location_id: str | None,
) -> list[dict[str, Any]]:
    """Turn one item — a name, or a list of UPCs — into products to add.

    Raises ServiceValidationError when the item cannot be added; its
    translation_key says why, which is what decides whether an alternative
    gets a turn.
    """
    term = item.get(ATTR_TERM)
    if term:
        found = await api.async_search_products(
            term=_search_term(term),
            brand=item.get(ATTR_BRAND),
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

        match, candidates = _match_by_name(pool, term, item.get(ATTR_SIZE))
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
        return [match]

    resolved = []
    for upc in item[ATTR_UPC]:
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
    return resolved


def _label(item: dict[str, Any]) -> str:
    """How an item is named when reporting that it was skipped."""
    return item.get(ATTR_TERM) or ", ".join(item[ATTR_UPC])


async def _async_pick(
    api: Any,
    items: list[dict[str, Any]],
    modality: str,
    check: bool,
    location_id: str | None,
) -> tuple[int, list[dict[str, Any]], list[dict[str, str]]]:
    """Resolve the first item, in preference order, that can be had.

    Returns its index, the products to add, and what was passed over and why.
    With a single item this is exactly _async_resolve: its error surfaces
    unchanged.
    """
    skipped: list[dict[str, str]] = []
    for index, item in enumerate(items):
        try:
            resolved = await _async_resolve(api, item, modality, check, location_id)
        except ServiceValidationError as err:
            if len(items) == 1 or err.translation_key not in FALL_THROUGH:
                raise
            skipped.append(
                {"item": _label(item), "reason": FALL_THROUGH[err.translation_key]}
            )
            continue
        return index, resolved, skipped

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_alternative_available",
        translation_placeholders={
            "tried": "; ".join(f"{s['item']} ({s['reason']})" for s in skipped),
        },
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Kroger services."""

    async def async_add_to_cart(call: ServiceCall) -> ServiceResponse:
        """Add an item, or the first of its alternatives that can be had."""
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
        alternatives = call.data[ATTR_ALTERNATIVES]

        if alternatives:
            # Without the lookup nothing is ever found unavailable, so the
            # alternatives could never be reached.
            if not check:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="alternatives_need_check",
                )
            # A list of UPCs is several items; there is no saying which of
            # them a single alternative would stand in for.
            if upcs and len(upcs) > 1:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="alternatives_need_one_item",
                )

        if term:
            primary = {
                ATTR_TERM: term,
                ATTR_BRAND: call.data.get(ATTR_BRAND),
                ATTR_SIZE: call.data.get(ATTR_SIZE),
            }
        else:
            primary = {ATTR_UPC: upcs}
        index, resolved, skipped = await _async_pick(
            api, [primary, *alternatives], modality, check, location_id
        )

        await api.async_add_to_cart(
            [
                {"upc": p["upc"], "quantity": quantity, "modality": modality}
                for p in resolved
            ]
        )
        return {
            "added": [
                {k: p.get(k) for k in ("upc", "description", "size", "price")}
                for p in resolved
            ],
            "substituted": index > 0,
            "skipped": skipped,
        }

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
        raw_term = call.data.get(ATTR_TERM)
        products = await api.async_search_products(
            term=_search_term(raw_term) if raw_term else None,
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
        DOMAIN,
        SERVICE_ADD_TO_CART,
        async_add_to_cart,
        schema=ADD_TO_CART_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_PRODUCTS,
        async_search_products,
        schema=SEARCH_PRODUCTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
