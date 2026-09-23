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

Confirmed working end to end against a King Soopers account on 2026-09-21: a
`204` from `cart/add` really did put the item in the banner's delivery cart. The
account-wide API does reach a banner cart — but only the one the account is
currently pointed at.

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

### `Invalid client_id` on the Kroger sign-in page

Kroger runs two environments with **separate, non-transferable credentials**:
production (`api.kroger.com`) and certification (`api-ce.kroger.com`). An
application is locked to whichever one it was registered in, and this
integration only ever talks to production. A certification client_id therefore
fails at the authorize step with a Kroger-branded *invalid request / Invalid
client_id* page, before Home Assistant is involved at all.

There is no way to move an application between environments — register a new
one in production and replace the credentials.

To tell which one you have, request the authorize endpoint on both hosts with
your client_id (no secret needed, nothing is logged in):

```bash
for h in api.kroger.com api-ce.kroger.com; do
  echo -n "$h -> "
  curl -s -o /dev/null -w "%{http_code}\n" \
    "https://$h/v1/connect/oauth2/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=https://my.home-assistant.io/redirect/oauth&scope=product.compact"
done
```

`400` from production and `200` from certification means the application is in
the wrong environment. You want the reverse.

Certification is not a usable fallback: it authenticates against
`login-stage.kroger.com`, which your real Kroger account does not exist in, and
Kroger's own documentation states the certification environment is not
accessible outside their network and third-party clients are not permitted
access. Authorization Code integrations have to be tested in production.

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

Identify the item by name, which is usually what you want in a button or
automation:

```yaml
action: kroger.add_to_cart
data:
  term: Coffee mate French Vanilla Flavored Coffee Creamer Non-Dairy Gluten-Free
  size: 32 fl oz
  quantity: 1
```

or by UPC, which is exact:

```yaml
action: kroger.add_to_cart
data:
  upc: "0001111060903"
  quantity: 1
  modality: PICKUP
```

Give one or the other, not both. `upc` also accepts a list, in which case
`quantity` applies to each.

**A product name often does not identify a product, so `size` matters.**
Kroger ships genuinely different items under descriptions that differ only in
capitalisation. These two are both "Coffee Mate French Vanilla Flavored Coffee
Creamer Non-Dairy Gluten-Free":

| UPC | size | price |
|---|---|---|
| 0005000032275 | 32 fl oz | $4.49 |
| 0005000035022 | 64 fl oz | $7.79 |

Because Kroger's search is fuzzy *and* its result order is documented as
unstable between identical requests, taking the top hit would pick between
those at random. This integration does not: an exact name wins outright, `size`
and `brand` narrow the field, and **anything still ambiguous fails and lists
the candidates rather than adding the wrong thing**. Since the API cannot
remove what it added, refusing is the only safe answer.

Long names are handled: Kroger caps `filter.term` at **eight words** and
rejects anything longer with a bare 400, while real product names routinely
run past that — the creamer above is ten words. Only what is sent to Kroger is
trimmed; the full name you gave is still what the match is made against.

#### `check_availability`

On by default. Looks the product up before adding and refuses if the store is
known to be unable to supply it. It deliberately asserts only what the API is
reliable about:

- **An explicit `TEMPORARILY_OUT_OF_STOCK` blocks the add.** A *missing* stock
  level does not — Kroger omits the field entirely when it has no data, even
  for products it will happily deliver.
- **For pickup, the `curbside` and `inStore` flags block the add.** These were
  checked against the storefront and agreed with it.
- **For delivery, nothing beyond stock is checked.** The per-store `delivery`
  flag is not trustworthy: for UPC 0079849310367 at store 62000084 the API
  reported `curbside`, `inStore` and `delivery` all false, while the site
  offered "Kroger Delivery: Available" and only "Pickup: Unavailable". Delivery
  is sourced against the customer's address rather than their selected store,
  so that flag under-reports and would refuse orders that succeed.

Set it to `false` to skip the lookup entirely, which saves one Products call
per add.

**The API cannot see delivery stock at all.** On 2026-09-22 the storefront
showed Caribou Blend (0079849310365) as "Kroger Delivery: Unavailable" and
Caribou Daybreak (0079849310367) as available, while the API gave the two an
identical answer: no `stockLevel`, and the same fulfillment flags — all false
at store 62000084, all *true* at store 62000065, where the site also said
delivery was unavailable. The API describes a store's shelf; Kroger Delivery
is fulfilled from somewhere it does not expose. Other products do carry a real
`stockLevel` (roughly one search result in six reads
`TEMPORARILY_OUT_OF_STOCK`), so the check is worth keeping. It just cannot
catch a delivery-only shortage. The storefront gets its answer from an internal
endpoint behind bot protection, which this integration does not use.

#### `alternatives`

Fallback items, tried in order when the item cannot be had:

```yaml
action: kroger.add_to_cart
data:
  term: Caribou Blend Medium Roast Ground Coffee - 12oz Bag
  size: 12 oz
  alternatives:
    - term: Caribou Coffee, Daybreak Light Roast Ground Coffee - 12oz Bag
      size: 12 oz
response_variable: added
```

Each alternative is a UPC (a bare string of digits), a product name (any other
bare string), or a mapping with `term` or `upc` plus optional `size` and
`brand` — the mapping is only needed to give a name its size. The alternative
above could equally be written `- "0079849310367"`.

An item is passed over **only** when it is unavailable by the rules above or
Kroger cannot find it at all, which is what a discontinued product looks like.
An ambiguous name still fails immediately rather than falling through, because
that is a mistake in the call, and silently ordering the backup would hide it.
If nothing on the list can be had, nothing is added and the error lists what
was tried and why each was skipped.

Alternatives need `check_availability` on, since without the lookup nothing is
ever found unavailable, and a single primary item rather than a list of UPCs.
Given the delivery blind spot above, expect them to fire for pickup orders and
explicit out-of-stocks, not for a delivery-only shortage.

#### Response

`add_to_cart` optionally returns what it actually added, which is the only
confirmation available — the cart cannot be read back:

```yaml
added:
  - upc: "0079849310367"
    description: Caribou Coffee, Daybreak Light Roast Ground Coffee - 12oz Bag
    size: 12 oz
    price: 13.99
substituted: true
skipped:
  - item: Caribou Blend Medium Roast Ground Coffee - 12oz Bag
    reason: unavailable
```

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
will not come close. Note that `check_availability` and name resolution each
spend a Products call per add.

## There is no order history

Kroger's developer catalogue is 13 API products — five public (Authorization,
Cart, Identity, Location, Product) and eight partner. **None of them expose
orders.** Order numbers cannot be looked up, past orders cannot be listed, and
"reorder what I bought last time" cannot be built on this API at any tier. The
nearest partner APIs, Seamless Delivery and Unattended Locker Pickup, are for
delivery and locker vendors rather than customer order history.

## Installation

Add this repository to [HACS](https://hacs.xyz) as a custom repository of type
*Integration*, install **Kroger**, and restart Home Assistant.

**Requires Home Assistant 2026.3 or newer.** The OAuth2 implementation raises
`OAuth2TokenRequestError` and its siblings, which were added to
`homeassistant.exceptions` in 2026.3; on an older core the integration fails at
import.
