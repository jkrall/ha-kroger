# Kroger for Home Assistant

Adds items to the shopping cart of a Kroger-family account (King Soopers, Fred
Meyer, Ralphs, Kroger…) and searches the product catalogue, from automations.

## What it can and cannot do

The public Kroger API is **add-only** on the cart. This integration exposes
everything it offers and nothing it does not:

| | |
|---|---|
| Add an item to the cart | yes — `kroger.add_to_cart` |
| Search the catalogue for a UPC, price, stock level, aisle | yes — `kroger.search_products` |
| Read what is in the cart | **no** — the public API has no such endpoint |
| Remove an item, change a quantity | **no** |
| Check out, schedule a slot, pay | **no** |

Those four gaps are limits of Kroger's public API, not of this integration.
The Partner API can do more, but requires a contract with Kroger.

Two consequences worth designing your automations around:

- **You cannot verify an add.** The call returns `204 No Content`. If an
  automation runs twice, you get the item twice, and nothing here can detect
  that. Guard with `mode: single` and a condition, not with a cart read.
- **`cart/add` takes no store.** Items go to the cart for whichever store the
  Kroger account currently has selected — set that once in the Kroger or King
  Soopers app. The store configured here is used only to price and stock-check
  *searches*.

## Setup

1. Register an application at the
   [Kroger developer console](https://developer.kroger.com/manage/apps):
   - **Redirect URI:** `https://my.home-assistant.io/redirect/oauth`
   - **Scopes:** `cart.basic:write`, `product.compact`, `profile.compact`
2. In Home Assistant, add the **Kroger** integration. Paste the client ID and
   secret when asked, sign in to Kroger, then pick your store.

The redirect URI above is what Home Assistant sends whenever the `my`
integration is loaded (it is, if you have `default_config:`). If you have
removed `my`, Home Assistant instead sends
`<your external URL>/auth/external/callback`, and that is what you must
register.

### Reauthenticating

Kroger refresh tokens are single-use and expire after six months. Home
Assistant stores the replacement token returned on every refresh, so this is
normally invisible — but roughly twice a year the integration will raise a
reauth prompt. Click it and sign in again.

## Actions

### `kroger.search_products`

Returns matching products. Use it to find the 13-digit UPC that
`add_to_cart` wants.

```yaml
action: kroger.search_products
data:
  term: whole milk
  limit: 5
response_variable: found
```

Each result carries `upc`, `description`, `brand`, `size`, `price`,
`promo_price`, `stock_level`, `aisle`, `fulfillment` and `image`.

### `kroger.add_to_cart`

```yaml
action: kroger.add_to_cart
data:
  upc: "0001111060903"
  quantity: 1
  modality: PICKUP
```

`upc` also accepts a list, in which case `quantity` applies to each.

## Example: reorder milk every Tuesday

```yaml
alias: Kroger Weekly Milk
id: kroger_weekly_milk
mode: single
triggers:
  - trigger: time
    at: "07:00:00"
conditions:
  - condition: time
    weekday:
      - tue
actions:
  - action: kroger.search_products
    data:
      term: Simple Truth whole milk half gallon
      limit: 1
    response_variable: found
  - condition: template
    value_template: "{{ found.products | count > 0 }}"
  - action: kroger.add_to_cart
    data:
      upc: "{{ found.products[0].upc }}"
      quantity: 1
```

Searching by term is fuzzy and the order of results can change between
identical requests, so pinning a known UPC is steadier than searching each
time. Search once, note the UPC, then hard-code it.

## Rate limits

Kroger's public allowances are 5,000 cart calls and 10,000 product calls per
day, counted per endpoint rather than per operation. A household automation
will not come close.

## Installation

Add this repository to [HACS](https://hacs.xyz) as a custom repository of type
*Integration*, install **Kroger**, and restart Home Assistant.
