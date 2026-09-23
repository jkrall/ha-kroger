"""Constants for the Kroger integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "kroger"

API_BASE: Final = "https://api.kroger.com/v1"
OAUTH2_AUTHORIZE: Final = f"{API_BASE}/connect/oauth2/authorize"
OAUTH2_TOKEN: Final = f"{API_BASE}/connect/oauth2/token"

# cart.basic:write is the only scope that can write to the cart. The other two
# are needed to search the catalogue and to confirm the token belongs to a real
# customer during the config flow.
SCOPES: Final = ["cart.basic:write", "product.compact", "profile.compact"]

# Shown on Kroger's consent screen. "kingsoopers" gives the King Soopers
# branding rather than generic Kroger; the account and cart are the same either
# way, so this is cosmetic.
DEFAULT_BANNER: Final = "kingsoopers"

CONF_LOCATION_ID: Final = "location_id"
CONF_DELIVERY_LOCATION_ID: Final = "delivery_location_id"
CONF_MODALITY: Final = "modality"
CONF_ZIP_CODE: Final = "zip_code"
CONF_CHAIN: Final = "chain"

MODALITY_PICKUP: Final = "PICKUP"
MODALITY_DELIVERY: Final = "DELIVERY"
MODALITIES: Final = [MODALITY_PICKUP, MODALITY_DELIVERY]

DEFAULT_CHAIN: Final = "KINGSOOPERS"
DEFAULT_MODALITY: Final = MODALITY_PICKUP

SERVICE_ADD_TO_CART: Final = "add_to_cart"
SERVICE_SEARCH_PRODUCTS: Final = "search_products"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_UPC: Final = "upc"
ATTR_QUANTITY: Final = "quantity"
ATTR_MODALITY: Final = "modality"
ATTR_TERM: Final = "term"
ATTR_BRAND: Final = "brand"
ATTR_LIMIT: Final = "limit"
ATTR_LOCATION_ID: Final = "location_id"
ATTR_FULFILLMENT: Final = "fulfillment"
ATTR_PRODUCT_ID: Final = "product_id"
ATTR_SIZE: Final = "size"
ATTR_CHECK_AVAILABILITY: Final = "check_availability"
ATTR_ALTERNATIVES: Final = "alternatives"

# Kroger omits stockLevel entirely when it has no data, so absence is not a
# signal. Only this value positively means "do not order".
STOCK_OUT: Final = "TEMPORARILY_OUT_OF_STOCK"

# Kroger rejects a filter.term of more than eight words with a bare 400, and
# counts every space as a word break. Real product names routinely exceed it:
# "Coffee mate French Vanilla Flavored Coffee Creamer Non-Dairy Gluten-Free"
# is ten. The full name is still used to match locally; only what is sent to
# Kroger is trimmed.
MAX_TERM_WORDS: Final = 8

# Which fulfillment flags can be trusted to veto an add, per modality. Kroger
# returns these keys in camelCase (inStore, shipToHome), unlike the lowercase
# spelling in the prose docs, so they are matched case-insensitively.
#
# DELIVERY is deliberately absent, and that is not an oversight. Checked against
# the King Soopers site on 2026-09-21 for UPC 0079849310367 at store 62000084:
# the API reported curbside, inStore AND delivery all false, while the site
# offered "Kroger Delivery: Available" and only "Pickup: Unavailable". The
# pickup flags matched the site exactly; the delivery flag did not. Delivery is
# sourced against the customer's address rather than their selected store, so a
# store's delivery flag under-reports and must not veto an add — it would refuse
# orders that succeed.
MODALITY_FULFILLMENT: Final = {
    MODALITY_PICKUP: ("curbside", "instore"),
}

# Kroger Delivery is not fulfilled from a store at all but from a Kroger
# fulfillment centre — an Ocado "shed" — which is a location in its own right
# (chain KROGER, store number FCnnn; the Denver one is 540FC007). Its
# stockLevel is what the storefront's "Kroger Delivery" availability follows:
# on 2026-09-23 Caribou Blend read TEMPORARILY_OUT_OF_STOCK there and Daybreak
# HIGH, exactly matching the site, while every store reported no stock level
# for either. The shed answers productId lookups but returns nothing for a term
# search, so names are still resolved against the store and only the stock
# check goes to the shed.

# Kroger's public rate limits, for the README and for context in log messages.
CART_CALLS_PER_DAY: Final = 5000
PRODUCT_CALLS_PER_DAY: Final = 10000
