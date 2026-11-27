# efresh-cli

[![tests](https://github.com/glogiotatidis/efresh-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/glogiotatidis/efresh-cli/actions/workflows/tests.yml)
[![daily e2e](https://github.com/glogiotatidis/efresh-cli/actions/workflows/daily-e2e.yml/badge.svg)](https://github.com/glogiotatidis/efresh-cli/actions/workflows/daily-e2e.yml)
[![license: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

[e-Fresh.gr](https://www.e-fresh.gr) in the terminal: log in to your real account, search
groceries, manage the cart, look through past orders, and let the nearest delivery slot be reserved
for you. The market refuses to sell without a delivery date, so `cart add` picks one implicitly.

The site has no public API; this CLI talks to the same JSON endpoints its own web client uses.
**No scraping, no browser automation, no API key, and no runtime dependencies** — the package
speaks `urllib` and keeps a Laravel session cookie.

> Unofficial. Not affiliated with e-Fresh. Use it on your own account, within the site's terms.

## Install

```bash
pipx install git+https://github.com/glogiotatidis/efresh-cli
# or, from a checkout
python3 -m venv .venv && .venv/bin/pip install -e . && ln -s "$PWD/.venv/bin/efresh" /usr/local/bin/efresh
```

## Log in once

```bash
export EFRESH_EMAIL='you@example.com'
efresh auth login --password-stdin      # paste the password, it is not echoed or stored
efresh auth status
efresh auth logout                      # deletes the local session
```

The session is the same one a browser gets: cookies in
`~/.config/efresh-cli/session.json` (mode 0600, override the directory with
`EFRESH_CONFIG_DIR`). It lasts as long as the site's session cookie, about six hours. No password
is written anywhere; `--password`, `EFRESH_PASSWORD` and `--password-stdin` all work, and stdin is
the one that keeps the secret out of shell history and `ps`.

## Search

```bash
efresh search "σανιτας"                 # 20 results, human output on a terminal
efresh search γαλα -n 10 -p 2           # page 2
efresh search "καφες" --json | jq '.results[0]'
```

The server returns 50 products per page; `-n` only trims what is printed. `price` is what you pay
(`offer` when there is one, `price` otherwise). `id` is the product id every cart command wants.

## Your cart

```bash
efresh cart                             # lines, quantities, total, delivery slot
efresh cart add 4700000028              # nearest free delivery slot is reserved first
efresh cart add 4700000028 -q 3         # same product, three units
efresh cart qty 4700000028 1            # absolute quantity: 3 -> 1
efresh cart qty 4700000028 0            # 0 removes the line
efresh cart remove 4700000028           # remove the whole line
efresh cart remove 4700000028 -q 1      # remove one unit
efresh delivery                         # current slot + the next available ones
efresh delivery set --date 2026-11-27 --slot 75   # reserve a specific slot
efresh delivery clear                   # drop the selected slot (no cart change)
```

`add` accepts the bare id, or any string containing it (the `app_web` link in the JSON does).

### Delivery

The checkout flow is slot-first: pick a delivery window, then shop. There is no "no slot" mode, so
`add` resolves the address with `timeslot/view`, then reads the reservation and the next windows
from `timeslot/get`; if a reservation already exists it is reused, otherwise the **first available
standard slot** is reserved with `timeslot/reserve`. Express slots are never chosen automatically
(they have a different minimum and shipping fee). `efresh delivery` shows the address, the current
reservation, and what is free next if you want to wait.

## Past orders

```bash
efresh orders                           # 10 per page, newest first
efresh orders -n 5 -p 2                 # page 2
efresh order 123456789012345678901      # one order, with its products
```

Orders are read-only. The CLI prints the order id, status, total, quantity, delivery date and the
product lines; it deliberately drops the recipient name, street address, invoice URLs and order
links from the JSON.

## Agents

The CLI is built for a personal assistant driving it, not for a human only:

* **JSON by default when stdout is not a TTY.** A terminal gets readable text; a pipe, a tool
  harness or cron gets one JSON object. `--json` forces JSON anywhere.
* **No prompts, ever.** Everything needed comes from flags or environment variables.
* **Stable exit codes:** `0` ok, `1` API/business error, `2` usage, `3` login required,
  `4` network. On error the JSON object is `{"error": {"code", "message", "details"}}`.
* **No state outside the session file.** Each command is a fresh process; the session is the only
  mutable state. `search` works anonymously; `cart`, `delivery` and every mutation need the login.
* The installable skill in [`skills/efresh`](skills/efresh/SKILL.md) teaches an agent the commands,
  the JSON shapes, and when to ask the user before spending money. Copy it to your harness:
  `mkdir -p ~/.claude/skills && cp -r skills/efresh ~/.claude/skills/`.

JSON shapes:

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
auth    -> {logged_in, email, session_file} / {logged_in, removed}
```

## Notes that cost time to learn

- Laravel CSRF is mandatory on every `POST /api/*`. The token is the `<meta name="csrf-token">` on
  the home page; `X-XSRF-TOKEN` is the **URL-decoded** `XSRF-TOKEN` cookie. A stale token answers
  `419`; the client refreshes once and retries.
- There is no `cart/update`. Setting a quantity means `cart/add` with the delta or `cart/remove`
  with the delta; `qty 0` and `remove` send `qty: 9999`, which the server clamps to "remove all".
- `cart/view` may contain a free gift line (`promo: true`, price 0). It is not yours to remove and
  is included in `total_quantity`.
- `total` in `cart/view` includes shipping and packaging when the account has an address;
  `subtotal` is the products. The `minimum_order` warning is separate, and checkout itself is not
  implemented.
- The search endpoint ignores `per_page` and `order_by`; pagination is `page` only, 50 per page.
- `offer` is `0` when there is no discount, so the payable price is `offer or price`.
- The session cookie expires after ~6 hours (`Max-Age=21600`), and logging in rotates it. If a
  command exits `3`, `efresh auth login` again; nothing else is lost.
- Product ids are strings (`"4700000028"`), not the site's internal numeric keys.
- `timeslot/view` carries the address but **not** the chosen reservation; the reservation and the
  windows come from `timeslot/get`. Reading only `view` makes a real slot look lost. `delivery`,
  `cart` and `add` all go through `get`, and `delivery set` re-reserves a specific slot by date and
  slot id (slot ids repeat across days, so the date is required).
- Past orders are 10 per page and the endpoint ignores `per_page`; `orders --page` walks them.
  Order ids are long digit strings. The detail response wraps everything in `order` and nests the
  delivery header one level deeper, which the CLI flattens.

## Tests and CI

```bash
pytest                       # offline suite: fake responses, no network
pytest -m live               # against the real site (public search; no credentials)
```

Three workflows:

- **`tests.yml`** (push / PR): the offline suite on Python 3.10, 3.12 and 3.13, a CLI smoke test,
  and a wheel built and installed into a clean venv.
- **`live.yml`** (daily 05:30 UTC, manual dispatch): `-m live`, public search only, so a fork needs
  no secrets.
- **`daily-e2e.yml`** (daily 06:00 UTC, manual dispatch): the authenticated loop — login, search,
  old orders (list and detail), add, quantity change, remove, delivery-slot restore, logout. The
  cart and the delivery slot are captured first; afterwards the cart must compare equal, a slot
  this run created is cleared, and a slot that already existed is reserved again by date and slot
  id from the baseline and then verified. A difference fails the run. Configure `EFRESH_EMAIL` and
  `EFRESH_PASSWORD` as repository secrets. Without them the authenticated half is skipped and only
  the public search runs, so forks stay green. Use a dedicated account.

## Layout

```
efresh_cli/_http.py   urllib transport, one place, no dependencies
efresh_cli/auth.py    session cookies on disk, 0600
efresh_cli/api.py     the site's JSON endpoints, CSRF, cart and delivery logic
efresh_cli/cli.py     argparse surface and output formatting
skills/efresh/        agent skill: commands, JSON shapes, safety rules
```

If e-Fresh changes a payload, `api.py` is the only file to touch; rendering lives in `cli.py`.

## License

GPL-3.0, see [LICENSE](LICENSE).
