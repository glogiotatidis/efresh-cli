"""Command line interface: `efresh search`, `efresh cart`, `efresh delivery`.

JSON is the default when stdout is not a TTY (pipes, agent harnesses, cron), so
personal assistants can call the CLI directly without flags; a terminal gets
the pretty form. `--json` forces JSON, `--format` does not exist on purpose:
one switch, one behaviour.

The market requires a delivery slot before anything can be added; `cart add`
reserves the nearest available one automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from typing import Any, Optional

from . import __version__, api

MAX_RESULTS = 50  # the search endpoint returns 50 per page


class UsageError(RuntimeError):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse that reports usage errors through the same channel as the API."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def money(value: Optional[float]) -> str:
    """Greek formatting, same as the website: 1.234,56 €."""
    if value is None:
        return "—"
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} €"


def json_mode(args: Optional[argparse.Namespace]) -> bool:
    return bool(getattr(args, "json", False)) or not sys.stdout.isatty()


def emit(data: dict[str, Any]) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


def resolve_target(target: str) -> str:
    try:
        return api.resolve_product_id(target)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def build_client(args: argparse.Namespace) -> api.Client:
    return api.Client(timeout=getattr(args, "timeout", 40),
                      verbose=getattr(args, "verbose", False))


# --------------------------------------------------------------------------- search

def cmd_search(args: argparse.Namespace) -> int:
    if not args.query.strip():
        raise UsageError("query must not be empty")
    if args.page < 1:
        raise UsageError("--page must be >= 1")
    if args.limit < 1:
        raise UsageError("--limit must be >= 1")
    limit = min(args.limit, MAX_RESULTS)
    if args.limit > MAX_RESULTS and not json_mode(args):
        print(f"note: one page holds {MAX_RESULTS} results, showing {MAX_RESULTS}",
              file=sys.stderr)
    client = build_client(args)
    data = client.search(args.query, page=args.page)
    results = data["results"][:limit]
    data["count"] = len(results)
    data["results"] = results

    if json_mode(args):
        emit(data)
        return 0
    if not results:
        print(f"no results for {args.query!r}")
        return 1
    print(f"{len(results)} results for {args.query!r} "
          f"(page {data['page']}/{data['pages']}, {data['total']} total)")
    for index, item in enumerate(results, 1):
        flag = "" if item["available"] else "  [unavailable]"
        print(f"{index:>2}. {money(item['price']):>12}  {item['name']}{flag}")
        print(f"    id {item['id']} · {item['url']}")
    return 0


# ----------------------------------------------------------------------------- auth

def cmd_auth(args: argparse.Namespace) -> int:
    action = args.auth_command
    client = build_client(args)

    if action == "login":
        email = args.email or None
        password = args.password
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\n")
        if not email:
            raise UsageError("provide --email or set EFRESH_EMAIL")
        if not password:
            raise UsageError("provide --password-stdin, --password or set EFRESH_PASSWORD")
        data = client.login(email, password)
        if json_mode(args):
            emit(data)
            return 0
        print(f"logged in to e-Fresh.gr as {data['email']}")
        print(f"  session: {data['session_file']}")
        return 0

    if action == "logout":
        data = client.logout()
        if json_mode(args):
            emit(data)
            return 0
        print("logged out" if data.get("removed") else "not logged in")
        return 0

    data = client.status()
    if json_mode(args):
        emit(data)
        return 0 if data["logged_in"] else 1
    if not data["logged_in"]:
        print("not logged in — run `efresh auth login`")
        print(f"  session: {data['session_file']}")
        return 1
    print(f"logged in to e-Fresh.gr as {data['email']}")
    print(f"  session: {data['session_file']}")
    return 0


# ----------------------------------------------------------------------------- cart

def print_cart(cart: dict[str, Any]) -> None:
    items = cart.get("items") or []
    if not items:
        print("your e-Fresh cart is empty")
        return
    for index, item in enumerate(items, 1):
        quantity = item.get("quantity") or 1
        total = item.get("line_total")
        price = (total / quantity) if isinstance(total, (int, float)) and quantity else total
        price_txt = money(float(price)) if isinstance(price, (int, float)) else "—"
        flags = []
        if item.get("promo"):
            flags.append("gift")
        if item.get("free_quantity"):
            flags.append(f"+{item['free_quantity']} free")
        if not item.get("available", True):
            flags.append("unavailable")
        suffix = f"  ({', '.join(flags)})" if flags else ""
        print(f"{index:>2}. {price_txt:>11} ×{quantity:<2} {item.get('name')}{suffix}")
        print(f"    id {item.get('id')}")
    total = cart.get("total")
    if isinstance(total, (int, float)):
        print(f"\n  total: {money(float(total))}")
    notices = cart.get("notices") or []
    for notice in notices:
        print(f"  ! {notice.get('message')}")
    delivery = cart.get("delivery") or {}
    reservation = delivery.get("reservation")
    if reservation:
        print(f"  delivery: {reservation.get('date')} "
              f"{reservation.get('from_time')}-{reservation.get('to_time')}")


def cmd_cart(args: argparse.Namespace) -> int:
    action = getattr(args, "cart_command", None)
    client = build_client(args)

    if action == "add":
        product_id = resolve_target(args.target)
        data = client.add(product_id, quantity=args.quantity)
        if json_mode(args):
            emit(data)
            return 0
        print(f"added {product_id} ×{data['quantity']} to your cart")
        reservation = (data.get("delivery") or {}).get("reservation") or {}
        if reservation:
            note = ("nearest slot, selected automatically"
                    if data.get("delivery_selected") else "already selected")
            print(f"  delivery: {reservation.get('date')} "
                  f"{reservation.get('from_time')}-{reservation.get('to_time')}"
                  f" ({note})")
        print()
        print_cart(data["cart"])
        return 0

    if action == "remove":
        product_id = resolve_target(args.target)
        data = client.remove(product_id, quantity=args.quantity)
        if json_mode(args):
            emit(data)
            return 0
        print(f"removed {product_id} ×{data['removed']}")
        print()
        print_cart(data["cart"])
        return 0

    if action == "qty":
        product_id = resolve_target(args.target)
        data = client.set_quantity(product_id, args.quantity)
        if json_mode(args):
            emit(data)
            return 0
        print(f"quantity {product_id}: {data['previous_quantity']} -> "
              f"{data['resulting_quantity']}")
        print()
        print_cart(data["cart"])
        return 0

    data = client.cart()
    if json_mode(args):
        emit(data)
        return 0
    print_cart(data)
    return 0


# --------------------------------------------------------------------------- orders

def cmd_orders(args: argparse.Namespace) -> int:
    if args.page < 1:
        raise UsageError("--page must be >= 1")
    if args.limit < 1:
        raise UsageError("--limit must be >= 1")
    data = build_client(args).orders(page=args.page)
    if args.limit > data["per_page"] and not json_mode(args):
        print(f"note: one page holds {data['per_page']} orders", file=sys.stderr)
    data["orders"] = data["orders"][: args.limit]
    data["count"] = len(data["orders"])
    if json_mode(args):
        emit(data)
        return 0
    if not data["orders"]:
        print("no orders")
        return 0
    print(f"orders (page {data['page']}, {data['count']} shown)")
    for index, order in enumerate(data["orders"], 1):
        placed = (order.get("created_at") or "")[:16]
        print(f"{index:>2}. {placed}  {money(order.get('total')):>10}  "
              f"{order.get('status') or '?'}  {order.get('quantity') or 0} items")
        print(f"    id {order.get('id')}")
    return 0


def cmd_order(args: argparse.Namespace) -> int:
    try:
        order_id = api.resolve_order_id(args.order_id)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    data = build_client(args).order(order_id)
    if json_mode(args):
        emit(data)
        return 0
    placed = (data.get("created_at") or "")[:16]
    print(f"order {data.get('id')}")
    print(f"  status: {data.get('status') or '?'}   placed {placed}")
    print(f"  total: {money(data.get('total'))} ({data.get('quantity') or 0} items)")
    delivery = data.get("delivery") or {}
    if delivery.get("date"):
        print(f"  delivery: {delivery['date']} "
              f"{delivery.get('from_time')}-{delivery.get('to_time')}")
    items = data.get("items") or []
    if items:
        print("  items:")
        for index, item in enumerate(items, 1):
            print(f"  {index:>2}. {money(item.get('unit_price')):>10} "
                  f"×{item.get('quantity') or 0:<2} {item.get('name')}")
    return 0


# ------------------------------------------------------------------------- delivery

def cmd_delivery(args: argparse.Namespace) -> int:
    action = getattr(args, "delivery_command", None)
    if action == "clear":
        data = build_client(args).clear_delivery()
        if json_mode(args):
            emit(data)
            return 0
        print("delivery cleared")
        return 0
    if action == "set":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            raise UsageError("--date must be YYYY-MM-DD")
        if args.slot < 1:
            raise UsageError("--slot must be >= 1")
        data = build_client(args).reserve_delivery(
            args.date, args.slot, address_id=args.address_id
        )
        if json_mode(args):
            emit(data)
            return 0
        reservation = data.get("reservation") or {}
        print(f"delivery set: {reservation.get('date')} "
              f"{reservation.get('from_time')}-{reservation.get('to_time')} "
              f"(slot {reservation.get('timeslot_id')})")
        return 0
    data = build_client(args).delivery_state(auto=False)
    data.pop("delivery", None)  # `reservation`/`address` are already at the top
    if json_mode(args):
        emit(data)
        return 0
    address = data.get("address") or {}
    if address:
        street = " ".join(part for part in (
            address.get("title"), address.get("street"), address.get("city"),
            address.get("postal_code"),
        ) if part)
        print(f"address: {street}")
    reservation = data.get("reservation")
    if reservation:
        print(f"delivery: {reservation.get('date')} "
              f"{reservation.get('from_time')}-{reservation.get('to_time')}")
    else:
        print("delivery: not selected — chosen automatically by `efresh cart add`")
    slots = data.get("available_slots") or []
    if slots:
        print("available:")
        for slot in slots:
            print(f"  {slot['date']} {slot['from_time']}-{slot['to_time']}  "
                  f"id {slot['id']}")
    else:
        print("available: none")
    return 0


# ------------------------------------------------------------------------------ cli

def main(argv: Optional[list[str]] = None) -> int:
    p = _Parser(
        prog="efresh",
        description="e-Fresh.gr in the terminal: log in, search groceries, manage "
                    "your real cart, and let the nearest delivery slot be chosen for you.",
    )
    p.add_argument("--version", action="version", version=f"efresh-cli {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS keeps a flag given before a subcommand from being overwritten by
    # the subparser's default: `efresh cart --json add ...` stays JSON.
    common.add_argument("--timeout", type=int, default=argparse.SUPPRESS,
                        help="per-request timeout in seconds (default: 40)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine readable output (default when stdout is not a TTY)")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="log HTTP requests on stderr")

    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", parents=[common], help="search groceries")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=20,
                   help=f"max results to print, {MAX_RESULTS} is one page (default: 20)")
    s.add_argument("-p", "--page", type=int, default=1, help="result page (default: 1)")
    s.set_defaults(func=cmd_search)

    # ---- history
    lo = sub.add_parser("orders", parents=[common],
                        help="your past orders, newest first")
    lo.add_argument("-n", "--limit", type=int, default=10,
                    help="max orders to print (one page holds 10)")
    lo.add_argument("-p", "--page", type=int, default=1, help="page (default: 1)")
    lo.set_defaults(func=cmd_orders)

    lod = sub.add_parser("order", parents=[common],
                         help="one past order with its products")
    lod.add_argument("order_id")
    lod.set_defaults(func=cmd_order)

    # ---- session
    a = sub.add_parser("auth", parents=[common],
                       help="log in to your e-Fresh account, or inspect the stored session")
    auth_sub = a.add_subparsers(dest="auth_command", required=True)
    al = auth_sub.add_parser("login", parents=[common],
                             help="log in with the account email and password")
    al.add_argument("--email", default=os.environ.get("EFRESH_EMAIL"),
                    help="account email (default: $EFRESH_EMAIL)")
    al.add_argument("--password", default=os.environ.get("EFRESH_PASSWORD"),
                    help="password (prefer --password-stdin or $EFRESH_PASSWORD)")
    al.add_argument("--password-stdin", action="store_true",
                    help="read the password from the first line of stdin")
    al.set_defaults(func=cmd_auth)
    ast = auth_sub.add_parser("status", parents=[common], help="show the stored login")
    ast.set_defaults(func=cmd_auth)
    alo = auth_sub.add_parser("logout", parents=[common],
                              help="log out and delete the stored session")
    alo.set_defaults(func=cmd_auth)

    # ---- cart
    c = sub.add_parser("cart", parents=[common],
                       help="your real cart; without a subcommand, show it")
    c.set_defaults(func=cmd_cart)
    cart_sub = c.add_subparsers(dest="cart_command")
    ca = cart_sub.add_parser("add", parents=[common],
                             help="add a product by id (selects the nearest delivery slot)")
    ca.add_argument("target", help="product id from `efresh search`")
    ca.add_argument("-q", "--quantity", type=int, default=1)
    ca.set_defaults(func=cmd_cart)
    cr = cart_sub.add_parser("remove", parents=[common],
                             help="remove a product from the cart")
    cr.add_argument("target", help="product id from `efresh cart`")
    cr.add_argument("-q", "--quantity", type=int, default=None,
                    help="remove only this many units (default: the whole line)")
    cr.set_defaults(func=cmd_cart)
    cq = cart_sub.add_parser("qty", parents=[common],
                             help="set the absolute quantity of a cart line")
    cq.add_argument("target", help="product id from `efresh cart`")
    cq.add_argument("quantity", type=int, help="target quantity; 0 removes the line")
    cq.set_defaults(func=cmd_cart)

    # ---- delivery
    d = sub.add_parser("delivery", parents=[common],
                       help="show the selected delivery slot and what is available next")
    d.set_defaults(func=cmd_delivery)
    delivery_sub = d.add_subparsers(dest="delivery_command")
    dc = delivery_sub.add_parser("clear", parents=[common],
                                 help="clear the selected delivery slot")
    dc.set_defaults(func=cmd_delivery)
    ds = delivery_sub.add_parser("set", parents=[common],
                                 help="reserve a specific slot (date + slot id)")
    ds.add_argument("--date", required=True, help="YYYY-MM-DD")
    ds.add_argument("--slot", type=int, required=True,
                    help="timeslot id from `efresh delivery`")
    ds.add_argument("--address-id", type=int, default=None,
                    help="address id (default: the account's delivery address)")
    ds.set_defaults(func=cmd_delivery)

    args: Optional[argparse.Namespace] = None
    try:
        args = p.parse_args(argv)
        return args.func(args)
    except UsageError as exc:
        return _fail(exc, args, code="usage", exit_code=2)
    except api.AuthRequired as exc:
        return _fail(exc, args, code="auth_required", exit_code=3)
    except api.APIError as exc:
        return _fail(exc, args, code=exc.code, exit_code=exc.exit_code,
                     details=exc.details)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - never traceback at an agent
        if getattr(args, "verbose", False):
            traceback.print_exc()
        return _fail(exc, args, code="internal_error", exit_code=1)


def _fail(exc: Exception, args: Optional[argparse.Namespace], *, code: str,
          exit_code: int,
          details: Any = None) -> int:
    if json_mode(args):
        error: dict[str, Any] = {"code": code, "message": str(exc)}
        if details is not None:
            error["details"] = details
        emit({"error": error})
    else:
        print(f"error: {exc}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
