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

# Kroger's public rate limits, for the README and for context in log messages.
CART_CALLS_PER_DAY: Final = 5000
PRODUCT_CALLS_PER_DAY: Final = 10000
