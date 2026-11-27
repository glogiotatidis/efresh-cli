"""The e-Fresh.gr JSON API, in one place.

Everything the CLI needs is here; if e-Fresh changes a payload, this is the
only file to touch. Endpoints were read from the site's own client:

    POST /api/account/login        {email, password, remember, os, lang, ...}
    GET  /el/account/logout
    POST /api/search               {q, page}      full text, 50 per page
    POST /api/cart/view            {}
    POST /api/cart/add             {id, qty, list_id}
    POST /api/cart/remove          {id, qty}      qty=9999 clears the line
    POST /api/timeslot/view        {}             current address + reservation
    POST /api/timeslot/get         {date, type, address_id}
    POST /api/timeslot/reserve     {weekday, timeslot_id, address_id, express}

The market wants a delivery slot before it lets you shop; ``ensure_delivery``
reserves the nearest available standard slot on demand, so adding to the cart
is a single command.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Optional

from . import _http, auth

DEFAULT_SITE = "https://www.e-fresh.gr"
API_PREFIX = "/api"
REFERER = "https://www.e-fresh.gr/el"

# Cosmetic fields the web client sends with every call. Kept identical so the
# server sees what it expects, including from the mobile breakpoint.
CLIENT_CONTEXT: dict[str, Any] = {
    "os": "web",
    "lang": "el",
    "screen_width": 1440,
    "screen_height": 784,
}

CSRF_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.IGNORECASE)
PRODUCT_RE = re.compile(r"(\d{5,})")


class APIError(RuntimeError):
    """An API call failed. ``exit_code`` is the CLI process status."""

    def __init__(self, message: str, *, code: str = "api_error", exit_code: int = 1,
                 details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details


class AuthRequired(APIError):
    """The command needs a logged-in session."""

    def __init__(self, message: str = "login required — run `efresh auth login`") -> None:
        super().__init__(message, code="auth_required", exit_code=3)


def resolve_product_id(target: str) -> str:
    """Accept a bare id, or any string that contains one (e.g. an app link)."""
    match = PRODUCT_RE.search(target)
    if not match:
        raise ValueError(f"cannot find an e-Fresh product id in {target!r}")
    return match.group(1)


def resolve_order_id(target: str) -> str:
    """Order ids are long digit strings; reject anything else before a round trip."""
    candidate = target.strip()
    if not candidate.isdigit() or len(candidate) < 5:
        raise ValueError(f"invalid e-Fresh order id {target!r}")
    return candidate


class Client:
    def __init__(self, *, site: Optional[str] = None, timeout: int = 40,
                 verbose: bool = False, session_file: Optional[Path] = None) -> None:
        self.site = (site or DEFAULT_SITE).rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self.session = auth.Session.load(session_file)
        self.jar: CookieJar = self.session.jar()
        self.session_file = session_file or auth.session_path()
        self._csrf: Optional[str] = None

    # ------------------------------------------------------------------ http
    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    def _xsrf(self) -> Optional[str]:
        values = [cookie.value for cookie in self.jar if cookie.name == "XSRF-TOKEN"]
        return values[-1] if values else None

    def _headers(self, referer: str = REFERER) -> dict[str, str]:
        headers = {
            "Accept-Language": "el-GR,el;q=0.9,en;q=0.5",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }
        if self._csrf:
            headers["X-CSRF-TOKEN"] = self._csrf
        xsrf = self._xsrf()
        if xsrf:
            headers["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf)
        return headers

    def _send(self, method: str, url: str, *, json_body: Any = None,
              referer: str = REFERER) -> _http.Response:
        self._log(f"{method} {url}")
        try:
            response = _http.request(
                url,
                method=method,
                json_body=json_body,
                headers=self._headers(referer),
                timeout=self.timeout,
                jar=self.jar,
            )
        except _http.TransportError as exc:
            raise APIError(str(exc), code="network_error", exit_code=4) from exc
        self._log(f"  -> {response.status}")
        return response

    # --------------------------------------------------------------- session
    def bootstrap(self) -> None:
        """Load the home page to obtain the CSRF token bound to this session."""
        response = self._send("GET", f"{self.site}/el")
        if response.status >= 400:
            raise APIError(f"home page returned HTTP {response.status}",
                           code="http_error", exit_code=4)
        match = CSRF_RE.search(response.text)
        if not match:
            raise APIError("could not find the CSRF token on the home page",
                           code="csrf_token_missing", exit_code=4)
        self._csrf = match.group(1)

    def _post(self, path: str, payload: dict[str, Any], *,
              referer: str = REFERER, require_auth: bool = False
              ) -> tuple[dict[str, Any], bool]:
        if self._csrf is None:
            self.bootstrap()

        response = self._send("POST", f"{self.site}{API_PREFIX}/{path.lstrip('/')}",
                              json_body=payload, referer=referer)

        # Laravel answers 419 with a fresh page when the CSRF token went stale.
        if response.status == 419:
            self._csrf = None
            self.bootstrap()
            response = self._send("POST", f"{self.site}{API_PREFIX}/{path.lstrip('/')}",
                                  json_body=payload, referer=referer)

        try:
            envelope = response.json()
        except ValueError as exc:
            if response.status >= 400:
                raise APIError(f"{path} returned HTTP {response.status}",
                               code="http_error", exit_code=4) from exc
            raise APIError(f"{path} returned a non-JSON response",
                           code="invalid_response", exit_code=4) from exc

        if not isinstance(envelope, dict):
            raise APIError(f"{path} returned an unexpected response",
                           code="invalid_response", exit_code=4)

        logged_in = bool(envelope.get("is_loggedin"))
        if envelope.get("status") is not True:
            message = str(envelope.get("message") or f"{path} failed")
            details = envelope.get("errors") or None
            if not logged_in and (require_auth or "Σύνδεση" in message
                                  or response.status in (401, 403)):
                raise AuthRequired()
            raise APIError(message, details=details)

        data = envelope.get("data")
        return (data if isinstance(data, dict) else {}), logged_in

    # --------------------------------------------------------------- account
    def login(self, email: str, password: str) -> dict[str, Any]:
        # A clean jar means a stale session cannot mask a failed login.
        self.jar = CookieJar()
        self._csrf = None
        self.bootstrap()
        try:
            _, logged_in = self._post(
                "account/login",
                {"email": email, "password": password, "remember": True,
                 **CLIENT_CONTEXT},
                referer=f"{self.site}/el/account/login",
            )
        except APIError as exc:
            raise APIError(str(exc), code="login_failed", exit_code=1,
                           details=exc.details) from exc
        if not logged_in:
            raise APIError("the server did not create a logged-in session",
                           code="login_failed")
        self._csrf = None
        self.bootstrap()  # the session id changed; bind a fresh CSRF token
        self.session.email = email
        self.session.adopt(self.jar)
        self.session.save(self.session_file)
        return {"logged_in": True, "email": email,
                "session_file": str(self.session_file)}

    def logout(self) -> dict[str, Any]:
        try:
            if self._csrf is None:
                self.bootstrap()
        except APIError:
            pass
        try:
            self._send("GET", f"{self.site}/el/account/logout")
        except APIError:
            pass  # local state is cleared regardless; logout is idempotent
        removed = auth.Session.clear(self.session_file)
        self.jar = CookieJar()
        self._csrf = None
        return {"logged_in": False, "removed": removed}

    def status(self) -> dict[str, Any]:
        _, logged_in = self._post("cart/view", dict(CLIENT_CONTEXT))
        return {
            "logged_in": logged_in,
            "email": self.session.email if logged_in else None,
            "session_file": str(self.session_file),
        }

    # ---------------------------------------------------------------- orders
    def orders(self, page: int = 1) -> dict[str, Any]:
        data, _ = self._post(
            "account/orders",
            {"page": page, **CLIENT_CONTEXT},
            referer=f"{self.site}/el/account/orders",
            require_auth=True,
        )
        listing = data.get("orders") or {}
        rows = listing.get("data") or []
        return {
            "page": int(listing.get("current_page") or page),
            "per_page": int(listing.get("per_page") or len(rows) or 0),
            "count": len(rows),
            "has_next": bool(listing.get("next_page_url")),
            "orders": [trim_order(row) for row in rows],
        }

    def order(self, order_id: str) -> dict[str, Any]:
        data, _ = self._post(
            "account/order",
            {"id": order_id, **CLIENT_CONTEXT},
            require_auth=True,
        )
        return trim_order(data, with_items=True)

    # ---------------------------------------------------------------- search
    def search(self, query: str, page: int = 1) -> dict[str, Any]:
        data, _ = self._post(
            "search",
            {"q": query, "page": page, **CLIENT_CONTEXT},
            referer=f"{self.site}/el/search?q={urllib.parse.quote(query)}",
        )
        products = data.get("products") or {}
        rows = products.get("data") or []
        return {
            "query": query,
            "page": int(products.get("current_page") or page),
            "pages": int(products.get("last_page") or 1),
            "count": len(rows),
            "total": int(products.get("total") or len(rows)),
            "results": [trim_product(row) for row in rows],
        }

    # ------------------------------------------------------------------ cart
    def cart(self) -> dict[str, Any]:
        data, logged_in = self._post(
            "cart/view", dict(CLIENT_CONTEXT), referer=f"{self.site}/el/cart",
            require_auth=True,
        )
        if not logged_in:
            raise AuthRequired()
        result = trim_cart(data.get("cart") or {})
        result["logged_in"] = True
        try:
            result["delivery"] = self.delivery_state(auto=False)["delivery"]
        except APIError:
            result["delivery"] = None
        return result

    def _items(self) -> list[dict[str, Any]]:
        data, logged_in = self._post(
            "cart/view", dict(CLIENT_CONTEXT), require_auth=True
        )
        if not logged_in:
            raise AuthRequired()
        return (data.get("cart") or {}).get("items") or []

    def find_item(self, product_id: str) -> Optional[dict[str, Any]]:
        for item in self._items():
            inner = item.get("item") or {}
            if (str(item.get("id")) == product_id
                    or str(inner.get("kodikos")) == product_id):
                return item
        return None

    def add(self, product_id: str, quantity: int = 1) -> dict[str, Any]:
        if quantity < 1:
            raise APIError("quantity must be >= 1", code="usage", exit_code=2)
        delivery = self.ensure_delivery()
        data, _ = self._post(
            "cart/add",
            {"id": product_id, "qty": quantity, "list_id": "search",
             **CLIENT_CONTEXT},
            referer=f"{self.site}/el/search",
            require_auth=True,
        )
        return {
            "product_id": product_id,
            "quantity": quantity,
            "delivery_selected": delivery["selected"],
            "delivery": delivery["delivery"],
            "cart": trim_cart(data.get("cart") or {}),
        }

    def remove(self, product_id: str, quantity: Optional[int] = None) -> dict[str, Any]:
        item = self.find_item(product_id)
        if item is None:
            raise APIError(f"product {product_id} is not in the cart",
                           code="not_in_cart")
        current = int(item.get("qty") or 0)
        if quantity is None:
            remove_qty = 9999  # the server clamps; mirrors remove-all in the UI
            removed = current
        else:
            if quantity < 1:
                raise APIError("quantity must be >= 1", code="usage", exit_code=2)
            remove_qty = quantity
            removed = min(quantity, current)
        data, _ = self._post(
            "cart/remove",
            {"id": product_id, "qty": remove_qty, "list_id": "",
             **CLIENT_CONTEXT},
            referer=f"{self.site}/el/cart",
            require_auth=True,
        )
        return {
            "product_id": product_id,
            "removed": removed,
            "cart": trim_cart(data.get("cart") or {}),
        }

    def set_quantity(self, product_id: str, quantity: int) -> dict[str, Any]:
        if quantity < 0:
            raise APIError("quantity must be >= 0", code="usage", exit_code=2)
        item = self.find_item(product_id)
        previous = int(item.get("qty") or 0) if item else 0
        if quantity == previous:
            return {
                "product_id": product_id,
                "previous_quantity": previous,
                "quantity": previous,
                "resulting_quantity": previous,
                "delta": 0,
                "cart": trim_cart((self._post(
                    "cart/view", dict(CLIENT_CONTEXT), require_auth=True
                )[0].get("cart") or {})),
            }
        if quantity > previous:
            # Increasing a line is still "adding", and the market requires a
            # delivery slot for that; reuse the existing one when present.
            self.ensure_delivery()
            path, payload = "cart/add", {
                "id": product_id, "qty": quantity - previous,
                "list_id": "search", **CLIENT_CONTEXT,
            }
        else:
            path, payload = "cart/remove", {
                "id": product_id,
                "qty": 9999 if quantity == 0 else previous - quantity,
                "list_id": "", **CLIENT_CONTEXT,
            }
        data, _ = self._post(path, payload,
                             referer=f"{self.site}/el/cart", require_auth=True)
        cart = trim_cart(data.get("cart") or {})
        resulting = 0
        for row in cart["items"]:
            if row["id"] == product_id:
                resulting = int(row["quantity"] or 0)
                break
        return {
            "product_id": product_id,
            "previous_quantity": previous,
            "quantity": quantity,
            "resulting_quantity": resulting,
            "delta": quantity - previous,
            "cart": cart,
        }

    # -------------------------------------------------------------- delivery
    def delivery_state(self, *, auto: bool = False) -> dict[str, Any]:
        """Read the reservation; with ``auto`` reserve the nearest free slot.

        ``timeslot/view`` only carries the address; the reservation and the
        windows come from ``timeslot/get``. Reading the reservation from
        ``view`` is how a chosen slot looks "lost" while the site still has it.
        """
        view, _ = self._post("timeslot/view", dict(CLIENT_CONTEXT),
                             require_auth=True)
        address = view.get("address")
        reservation = view.get("reservation")

        slots_data, _ = self._post(
            "timeslot/get",
            {"date": None, "type": "next",
             "address_id": (address or {}).get("id"), **CLIENT_CONTEXT},
            require_auth=True,
        )
        address = slots_data.get("address") or address
        reservation = slots_data.get("reservation") or reservation
        slots = available_slots(slots_data)

        result: dict[str, Any] = {
            "reservation": trim_reservation(reservation),
            "address": trim_address(address),
            "available_slots": slots,
            "selected": False,
        }
        if reservation:
            result["delivery"] = {
                "reservation": result["reservation"],
                "address": result["address"],
            }
            return result

        result["delivery"] = {"reservation": None, "address": result["address"]}
        if not auto:
            return result
        if not slots:
            raise APIError("no delivery slot is currently available for this address",
                           code="no_delivery_slot",
                           details={"address": result["address"]})
        chosen = slots[0]
        reserved, _ = self._post(
            "timeslot/reserve",
            {"weekday": chosen["date"], "timeslot_id": chosen["id"],
             "address_id": (address or {}).get("id"), "express": False,
             **CLIENT_CONTEXT},
            require_auth=True,
        )
        result["reservation"] = trim_reservation(reserved.get("reservation"))
        result["address"] = trim_address(reserved.get("address") or address)
        result["selected"] = True
        result["delivery"] = {
            "reservation": result["reservation"],
            "address": result["address"],
        }
        return result

    def reserve_delivery(self, date: str, slot_id: int,
                         address_id: Optional[int] = None) -> dict[str, Any]:
        """Reserve a specific slot; used to repair a slot a test run cleared."""
        if address_id is None:
            view, _ = self._post("timeslot/view", dict(CLIENT_CONTEXT),
                                 require_auth=True)
            address_id = (view.get("address") or {}).get("id")
        data, _ = self._post(
            "timeslot/reserve",
            {"weekday": date, "timeslot_id": slot_id,
             "address_id": address_id, "express": False, **CLIENT_CONTEXT},
            require_auth=True,
        )
        return {
            "reservation": trim_reservation(data.get("reservation")),
            "address": trim_address(data.get("address")),
        }

    def clear_delivery(self) -> dict[str, Any]:
        data, _ = self._post(
            "timeslot/clear", dict(CLIENT_CONTEXT), require_auth=True
        )
        return {"reservation": trim_reservation(data.get("reservation"))}

    def ensure_delivery(self) -> dict[str, Any]:
        state = self.delivery_state(auto=True)
        if not state.get("delivery") or not state["delivery"].get("reservation"):
            raise APIError("could not obtain a delivery slot", code="no_delivery_slot")
        return state


# ---------------------------------------------------------------- normalizers
def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trim_product(product: dict[str, Any]) -> dict[str, Any]:
    price = product.get("offer") or product.get("price")
    old = product.get("price_old")
    return {
        "id": str(product.get("id") or product.get("kodikos") or ""),
        "name": product.get("title") or "",
        "price": _number(price) or 0.0,
        "price_before_discount": _number(old) if old else None,
        "unit_price": _number(product.get("price_per_unit")),
        "package_size": _number(product.get("pkg_value")),
        "stock": product.get("stock_qty"),
        "available": bool(product.get("in_stock", True))
        and bool(product.get("is_saleable", True)),
        "url": (product.get("links") or {}).get("web"),
    }


def trim_order(row: dict[str, Any], *, with_items: bool = False) -> dict[str, Any]:
    # The list returns the order fields at the top level; the detail endpoint
    # wraps the full order in `order`, with the delivery header one level deeper.
    inner = row.get("order") or {}
    header = inner.get("order") if isinstance(inner.get("order"), dict) else {}

    def first(key: str) -> Any:
        for source in (row, inner, header):
            value = source.get(key)
            if value is not None:
                return value
        return None

    summary = {
        "id": first("order_id") or first("id"),
        "created_at": first("created_at"),
        "status": first("status"),
        "completed": bool(first("is_completed")),
        "cancelled": bool(first("is_cancelled")),
        "total": _number(first("total_amount") or first("total")),
        "quantity": first("total_qty"),
        "delivery": {
            "date": first("timeslot_delivery_date"),
            "from_time": first("timeslot_from_time"),
            "to_time": first("timeslot_to_time"),
        },
    }
    if with_items:
        items = []
        for item in (row.get("order_items") or inner.get("order_items") or []):
            items.append({
                "id": str(item.get("id") or ""),
                "name": item.get("title") or "",
                "quantity": item.get("qty"),
                "unit_price": _number(item.get("price")),
                "line_total": _number(item.get("total")),
            })
        summary["item_count"] = len(items)
        summary["items"] = items
    return summary


def trim_cart(cart: dict[str, Any]) -> dict[str, Any]:
    items = []
    for item in cart.get("items") or []:
        inner = item.get("item") or {}
        items.append({
            "id": str(item.get("id") or inner.get("kodikos") or ""),
            "name": item.get("name") or inner.get("title") or "",
            "quantity": item.get("qty"),
            "free_quantity": item.get("qty_free") or 0,
            "unit_price": _number(item.get("price")),
            "line_total": _number(item.get("total")),
            "promo": bool(item.get("promo")),
            "available": bool(inner.get("in_stock", True))
            and bool(inner.get("is_saleable", True)),
        })
    notices = []
    for key, kind in (("errors", "error"), ("infos", "info")):
        for notice in cart.get(key) or []:
            notices.append({"type": notice.get("type") or kind,
                            "message": notice.get("message") or ""})
    packaging = cart.get("product_packaging") or {}
    return {
        "items": items,
        "item_count": len(items),
        "total_quantity": cart.get("total_qty"),
        "subtotal": _number(cart.get("subtotal")),
        "discount": _number(cart.get("discount")),
        "shipping": _number(cart.get("shipping_amount")),
        "packaging": _number(packaging.get("amount")),
        "total": _number(cart.get("total")),
        "minimum_order": _number(cart.get("min_purchase_amount")),
        "currency": "EUR",
        "notices": notices,
    }


def trim_address(address: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not address:
        return None
    return {
        "id": address.get("id"),
        "title": address.get("title"),
        "street": address.get("street"),
        "city": address.get("city"),
        "postal_code": address.get("postal_code"),
    }


def trim_reservation(reservation: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not reservation:
        return None
    timeslot = reservation.get("timeslot") or {}
    return {
        "id": reservation.get("id"),
        "timeslot_id": reservation.get("timeslot_id") or timeslot.get("id"),
        "date": reservation.get("delivery_date") or timeslot.get("date"),
        "from_time": reservation.get("from_time") or timeslot.get("from_time"),
        "to_time": reservation.get("to_time") or timeslot.get("to_time"),
        "address_id": reservation.get("address_id"),
        "express": bool(reservation.get("delivery_express")),
        "message": reservation.get("message") or timeslot.get("message"),
    }


def available_slots(slots_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the day buckets into free windows, chronologically."""
    result = []
    for day in slots_data.get("timeslots") or []:
        for slot in day.get("slots") or []:
            if not slot.get("is_avail"):
                continue
            polygon = slot.get("polygon_extra") or {}
            result.append({
                "id": slot.get("id"),
                "date": slot.get("date") or day.get("weekday"),
                "weekday": day.get("weekday_title"),
                "from_time": slot.get("from_time"),
                "to_time": slot.get("to_time"),
                "title": slot.get("title"),
                "minimum_order": _number(polygon.get("min_order_amount")),
                "shipping": _number(polygon.get("shipping_amount")),
                "free_shipping_from": _number(
                    polygon.get("free_shipping_order_amount")
                ),
            })
    return result
