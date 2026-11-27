---
name: efresh
description: Shop at e-Fresh.gr from a terminal or agent harness — log in, search Greek supermarket products, add or remove cart lines, set quantities, and inspect the delivery slot. Use when the user wants groceries from e-Fresh, asks what is in their e-Fresh cart, or wants a product added to it.
---

# e-Fresh CLI

`efresh` drives a real e-Fresh.gr account. The cart is not a sandbox: adding a line changes what
the user will be charged if they check out in the browser. Confirm before any cart mutation unless
the user explicitly asked for that exact change.

## Setup

```bash
pipx install git+https://github.com/glogiotatidis/efresh-cli
efresh --version
```

The account is authenticated once, outside the agent:

```bash
export EFRESH_EMAIL='user@example.com'
efresh auth login --password-stdin     # the user types the password into stdin
efresh auth status                     # {"logged_in": true, "email": ...}
```

Never put a password in a command-line flag, a prompt, or a log. Use `EFRESH_EMAIL` and
`EFRESH_PASSWORD`, or `--password-stdin`. The session file
(`~/.config/efresh-cli/session.json`, mode 0600) is a secret; do not read it, copy it, or print it.

## Calling it

- JSON is returned automatically when stdout is not a TTY; add `--json` when in doubt.
- No command prompts. Every argument is a flag or environment variable.
- Exit codes: `0` ok, `1` API/business error, `2` usage, `3` login required, `4` network.
- Errors are `{"error": {"code", "message", "details"}}` on stdout.
- Run `efresh --help` or `efresh cart --help` for the authoritative flags.

## Commands

```bash
efresh auth status
efresh auth login --email "$EFRESH_EMAIL" --password-stdin
efresh auth logout

efresh search "γαλα" -n 10            # first page
efresh search "καφες" -p 2            # 50 per page
efresh delivery                       # current reservation + free slots
efresh delivery set --date YYYY-MM-DD --slot <id>   # reserve a specific slot
efresh delivery clear                 # drop the selected slot (destructive to the slot only)

efresh orders -n 5                    # past orders, newest first
efresh order <order_id>               # one order, with products

efresh cart                           # lines, quantities, totals, delivery
efresh cart add 4700000028            # nearest delivery slot is reserved first
efresh cart add 4700000028 -q 3
efresh cart qty 4700000028 1          # absolute quantity
efresh cart qty 4700000028 0          # remove the line
efresh cart remove 4700000028         # whole line
efresh cart remove 4700000028 -q 1    # one unit
```

`<id>` is the `results[].id` from `search` or `items[].id` from `cart`. Any string containing
the id also works; the first 5+ digit number wins.

## Reading the JSON

```
search  -> {query, page, pages, count, total, results: [{id, name, price,
            price_before_discount, unit_price, package_size, stock, available, url}]}
cart    -> {items: [{id, name, quantity, free_quantity, unit_price, line_total,
            promo, available}], item_count, total_quantity, subtotal, discount,
            shipping, packaging, total, minimum_order, currency, notices,
            delivery, logged_in}
add     -> {product_id, quantity, delivery_selected, delivery, cart}
remove  -> {product_id, removed, cart}
qty     -> {product_id, previous_quantity, quantity, resulting_quantity, delta, cart}
orders  -> {page, per_page, count, has_next, orders: [{id, created_at, status,
            completed, cancelled, total, quantity, delivery}]}
order   -> the same fields plus {item_count, items: [{id, name, quantity,
            unit_price, line_total}]}
delivery-> {reservation, address, available_slots, selected}
delivery set -> {reservation, address}
delivery clear -> {reservation}
```

`price` is the payable price (`offer` when present, otherwise `price`). `available: false` means
out of stock or not sellable; do not add it. `items[].promo` is a free gift the site inserts; leave
it alone. `cart.total` includes shipping and packaging when the account has an address; `subtotal`
is the products. The `minimum_order` notice is separate, and checkout is not implemented.

## Behaviour worth knowing

- **Delivery is mandatory.** `cart add` reserves the nearest free standard slot automatically
  unless one is already selected. It never picks an express slot. `delivery_selected: true` means
  the CLI just reserved it; tell the user the date and window from `delivery.reservation`.
- **Quantities are absolute in `cart qty`.** The CLI computes the delta from the live cart. Check
  `resulting_quantity` in the response; the server may clamp stock.
- **The cart changes immediately.** There is no "prepare" or "dry run". To undo, call `remove`
  with the same id.
- **Login expires** after roughly six hours, or when the site rotates the session. Exit code `3`
  means run `efresh auth login`; the cart is not affected.
- **No checkout.** This CLI stops at the cart. Purchases and payment happen on e-fresh.gr; say so
  rather than implying an order was placed.
- **Orders are read-only and private.** `orders` / `order` expose purchase history. Fetch only what
  the user asked for, and do not paste order ids, totals or item lists into public channels. The
  CLI already omits the recipient name, address, invoice URLs and order links.
- **`delivery clear` is destructive to the chosen slot**, though it does not touch the cart. Use it
  only on request, or to restore a slot a test run created. `delivery set` needs both the date and
  the slot id because slot ids repeat across days; take them from `delivery.reservation`.

## Good agent loop

1. `efresh auth status`; on `logged_in: false`, ask the user to run `efresh auth login` (never ask
   for the password yourself).
2. `efresh search "<what they want>"`, show a few `name`, `price`, `available` rows, and ask which
   `id` when it is ambiguous.
3. `efresh cart add <id> -q <n>`; report the line and the delivery date/window.
4. `efresh cart` to confirm the final basket and `total`, `notices`, and `minimum_order`.
5. `efresh orders` when the user asks what they bought before; `efresh order <id>` for the lines.
6. Never check out, never log out, and never clear the cart or delivery slot without an explicit
   request.
