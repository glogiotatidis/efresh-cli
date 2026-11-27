"""API and normalizer tests against fake responses (no network)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from efresh_cli import _http, api


def make_client(tmp_path: Path) -> api.Client:
    client = api.Client(session_file=tmp_path / "session.json", timeout=1)
    client._csrf = "token"
    return client


def response(body: dict, status: int = 200) -> _http.Response:
    return _http.Response(status, "application/json", json.dumps(body))


def envelope(data: dict, *, status: bool = True, logged_in: bool = True,
             message: str = "") -> _http.Response:
    return response({
        "status": status,
        "message": message,
        "is_loggedin": logged_in,
        "errors": [],
        "data": data,
    })


# --------------------------------------------------------------------- session

def test_session_roundtrip_and_permissions(tmp_path: Path) -> None:
    from efresh_cli import auth

    path = tmp_path / "nested" / "session.json"
    session = auth.Session(email="a@example.test")
    session.save(path)
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = auth.Session.load(path)
    assert loaded.email == "a@example.test"
    assert loaded.cookies == []


def test_save_keeps_an_existing_directory_mode(tmp_path: Path) -> None:
    from efresh_cli import auth

    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    auth.Session().save(shared / "session.json")
    assert (shared.stat().st_mode & 0o777) == 0o755


def test_save_creates_a_private_directory(tmp_path: Path) -> None:
    from efresh_cli import auth

    created = tmp_path / "cfg"
    auth.Session().save(created / "session.json")
    assert (created.stat().st_mode & 0o777) == 0o700


def test_clear_missing_session_is_false(tmp_path: Path) -> None:
    from efresh_cli import auth

    assert auth.Session.clear(tmp_path / "session.json") is False


def test_config_dir_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from efresh_cli import auth

    monkeypatch.setenv("EFRESH_CONFIG_DIR", str(tmp_path))
    assert auth.session_path() == tmp_path / "session.json"


def test_xsrf_header_is_url_decoded(tmp_path: Path) -> None:
    from efresh_cli import auth

    session = auth.Session(cookies=[{
        "name": "XSRF-TOKEN",
        "value": "abc%3D%3D",
        "domain": "www.e-fresh.gr",
        "path": "/",
    }])
    client = make_client(tmp_path)
    client.jar = session.jar()
    assert client._headers()["X-XSRF-TOKEN"] == "abc=="


# ------------------------------------------------------------------ normalizers

def test_offer_zero_falls_back_to_price() -> None:
    product = {
        "id": "1",
        "title": "Milk",
        "price": 9.36,
        "offer": 0,
        "price_old": 0,
        "in_stock": True,
        "is_saleable": True,
        "links": {"web": "https://www.e-fresh.gr/el/milk"},
    }
    trimmed = api.trim_product(product)
    assert trimmed["price"] == 9.36
    assert trimmed["price_before_discount"] is None
    assert trimmed["available"] is True


def test_unavailable_product() -> None:
    trimmed = api.trim_product(
        {"id": "2", "price": 1.0, "offer": 1.0, "in_stock": False}
    )
    assert trimmed["available"] is False


def test_only_available_slots_are_flattened_in_order() -> None:
    data = {
        "timeslots": [
            {
                "weekday": "2026-11-26",
                "weekday_title": "Πέμπτη",
                "slots": [
                    {"id": 1, "date": "2026-11-26", "is_avail": False},
                    {
                        "id": 2,
                        "date": "2026-11-26",
                        "from_time": "09:30",
                        "to_time": "11:30",
                        "is_avail": True,
                        "polygon_extra": {"min_order_amount": 25},
                    },
                ],
            },
            {
                "weekday": "2026-11-27",
                "weekday_title": "Παρασκευή",
                "slots": [{"id": 3, "date": "2026-11-27", "is_avail": True}],
            },
        ]
    }
    slots = api.available_slots(data)
    assert [slot["id"] for slot in slots] == [2, 3]
    assert slots[0]["minimum_order"] == 25


# --------------------------------------------------------------------- delivery

def test_existing_reservation_is_read_from_timeslot_get(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    reservation = {
        "id": 9,
        "timeslot_id": 75,
        "delivery_date": "2026-11-27",
        "from_time": "07:00",
        "to_time": "08:30",
        "address_id": 5,
    }
    view = {"reservation": None, "address": {"id": 5}}
    slots = {"reservation": reservation, "address": {"id": 5}, "timeslots": []}

    def fake_post(path, payload, **kwargs):
        return (slots if path == "timeslot/get" else view), True

    with mock.patch.object(client, "_post", side_effect=fake_post) as post:
        state = client.ensure_delivery()
    assert state["selected"] is False
    assert state["delivery"]["reservation"]["date"] == "2026-11-27"
    assert state["delivery"]["reservation"]["timeslot_id"] == 75
    assert post.call_count == 2
    assert [call.args[0] for call in post.call_args_list] == [
        "timeslot/view", "timeslot/get"
    ]


def test_books_first_available_slot(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    view = {"reservation": None, "address": {"id": 5}}
    slots = {
        "address": {"id": 5},
        "timeslots": [{
            "weekday": "2026-11-26",
            "slots": [
                {"id": 1, "date": "2026-11-26", "is_avail": False},
                {
                    "id": 2,
                    "date": "2026-11-26",
                    "from_time": "09:30",
                    "to_time": "11:30",
                    "is_avail": True,
                },
            ],
        }],
    }
    reserved = {
        "address": {"id": 5},
        "reservation": {
            "id": 10,
            "delivery_date": "2026-11-26",
            "from_time": "09:30",
            "to_time": "11:30",
        },
    }
    calls = []

    def fake_post(path, payload, **kwargs):
        calls.append((path, payload))
        if path == "timeslot/view":
            return view, True
        if path == "timeslot/get":
            return slots, True
        if path == "timeslot/reserve":
            return reserved, True
        raise AssertionError(path)

    with mock.patch.object(client, "_post", side_effect=fake_post):
        state = client.ensure_delivery()
    assert state["selected"] is True
    assert state["reservation"]["id"] == 10
    reserve = [payload for path, payload in calls if path == "timeslot/reserve"][0]
    assert reserve["timeslot_id"] == 2
    assert reserve["weekday"] == "2026-11-26"
    assert reserve["address_id"] == 5
    assert reserve["express"] is False


def test_reserve_delivery_posts_the_requested_slot(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = {
        "reservation": {
            "id": 1, "timeslot_id": 88, "delivery_date": "2026-01-03",
            "from_time": "07:00", "to_time": "08:30", "address_id": 5,
        },
        "address": {"id": 5},
    }
    with mock.patch.object(client, "_post",
                           return_value=(response, True)) as post:
        data = client.reserve_delivery("2026-01-03", 88, address_id=5)
    payload = post.call_args[0][1]
    assert payload["weekday"] == "2026-01-03"
    assert payload["timeslot_id"] == 88
    assert payload["address_id"] == 5
    assert payload["express"] is False
    assert data["reservation"]["timeslot_id"] == 88


def test_clear_delivery_returns_no_reservation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "_post",
                           return_value=({"reservation": None}, True)):
        data = client.clear_delivery()
    assert data["reservation"] is None


def test_no_slots_is_an_error(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    def fake_post(path, payload, **kwargs):
        if path == "timeslot/view":
            return {"reservation": None, "address": {"id": 5}}, True
        return {"address": {"id": 5}, "timeslots": []}, True

    with mock.patch.object(client, "_post", side_effect=fake_post):
        with pytest.raises(api.APIError) as ctx:
            client.ensure_delivery()
    assert ctx.value.code == "no_delivery_slot"


# -------------------------------------------------------------------- orders

def test_orders_are_trimmed_and_paginated(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    listing = {"orders": {
        "current_page": 2, "per_page": 10, "next_page_url": "https://example/3",
        "data": [{
            "order_id": "123456789012345678901",
            "is_completed": True, "is_cancelled": False, "status": "completed",
            "created_at": "2026-01-02 10:00:00",
            "total_amount": 12.5, "total_qty": 3,
            "shipping_street": "secret street",
            "order": {
                "timeslot_delivery_date": "2026-01-03",
                "timeslot_from_time": "07:00",
                "timeslot_to_time": "08:30",
            },
        }],
    }}
    with mock.patch.object(client, "_post", return_value=(listing, True)):
        data = client.orders(page=2)
    assert data["page"] == 2
    assert data["has_next"] is True
    order = data["orders"][0]
    assert order["id"] == "123456789012345678901"
    assert order["total"] == 12.5
    assert order["delivery"]["date"] == "2026-01-03"
    assert "shipping_street" not in order


def test_order_detail_reads_the_wrapped_order(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    detail = {"order": {
        "order_id": "123456789012345678901",
        "is_completed": True, "is_cancelled": False, "status": "completed",
        "created_at": "2026-01-02 10:00:00",
        "total_amount": 3.0, "total_qty": 2,
        "order_items": [{
            "id": "4300002601", "title": "Paper", "qty": 2,
            "price": 1.5, "total": 3.0,
        }],
        "order": {
            "timeslot_delivery_date": "2026-01-03",
            "timeslot_from_time": "07:00",
            "timeslot_to_time": "08:30",
        },
    }}
    with mock.patch.object(client, "_post", return_value=(detail, True)):
        data = client.order("123456789012345678901")
    assert data["item_count"] == 1
    assert data["items"][0]["name"] == "Paper"
    assert data["items"][0]["line_total"] == 3.0
    assert data["delivery"]["date"] == "2026-01-03"
    assert data["total"] == 3.0


def test_order_id_must_be_numeric() -> None:
    assert api.resolve_order_id(" 123456 ") == "123456"
    with pytest.raises(ValueError):
        api.resolve_order_id("https://example/order/123456")


# -------------------------------------------------------------------- quantities

def cart_payload(quantity: int) -> dict:
    return {"cart": {
        "items": [{
            "id": "42",
            "name": "Test",
            "qty": quantity,
            "item": {"kodikos": "42", "in_stock": True},
        }],
        "total_qty": quantity,
        "subtotal": quantity,
        "total": quantity,
    }}


def test_quantity_increase_adds_delta(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "find_item", return_value={"id": "42", "qty": 2}):
        with mock.patch.object(client, "ensure_delivery",
                               return_value={"selected": False, "delivery": {}}):
            with mock.patch.object(client, "_post",
                                   return_value=(cart_payload(5), True)) as post:
                result = client.set_quantity("42", 5)
    assert result["delta"] == 3
    assert post.call_args[0][0] == "cart/add"
    assert post.call_args[0][1]["qty"] == 3


def test_quantity_decrease_removes_delta(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "find_item", return_value={"id": "42", "qty": 5}):
        with mock.patch.object(client, "_post",
                               return_value=(cart_payload(1), True)) as post:
            client.set_quantity("42", 1)
    assert post.call_args[0][0] == "cart/remove"
    assert post.call_args[0][1]["qty"] == 4


def test_quantity_zero_removes_whole_line(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "find_item", return_value={"id": "42", "qty": 3}):
        with mock.patch.object(client, "_post",
                               return_value=(cart_payload(0), True)) as post:
            client.set_quantity("42", 0)
    assert post.call_args[0][0] == "cart/remove"
    assert post.call_args[0][1]["qty"] == 9999


def test_same_quantity_does_not_mutate(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "find_item", return_value={"id": "42", "qty": 2}):
        with mock.patch.object(client, "_post",
                               return_value=(cart_payload(2), True)) as post:
            result = client.set_quantity("42", 2)
    assert result["delta"] == 0
    assert [call.args[0] for call in post.call_args_list] == ["cart/view"]


def test_remove_all_uses_the_server_clamp(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "find_item", return_value={"id": "42", "qty": 3}):
        with mock.patch.object(client, "_post",
                               return_value=(cart_payload(0), True)) as post:
            result = client.remove("42")
    assert post.call_args[0][1]["qty"] == 9999
    assert result["removed"] == 3


def test_remove_missing_product_is_an_error(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    with mock.patch.object(client, "find_item", return_value=None):
        with pytest.raises(api.APIError) as ctx:
            client.remove("42")
    assert ctx.value.code == "not_in_cart"


# ---------------------------------------------------------------------- envelope

def test_auth_required_maps_to_exit_3(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client._send = mock.Mock(return_value=envelope(
        {}, status=False, logged_in=False, message="Απαιτείται Σύνδεση!"
    ))
    with pytest.raises(api.AuthRequired) as ctx:
        client._post("timeslot/view", {}, require_auth=True)
    assert ctx.value.exit_code == 3


def test_csrf_419_retries_once(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client._send = mock.Mock(side_effect=[
        _http.Response(419, "text/html", "csrf"),
        envelope({"ok": 1}, logged_in=False),
    ])
    client.bootstrap = mock.Mock()
    data, logged_in = client._post("search", {"q": "x"})
    assert data == {"ok": 1}
    assert logged_in is False
    assert client._send.call_count == 2
    assert client.bootstrap.call_count == 1


def test_api_error_keeps_the_server_message(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client._send = mock.Mock(return_value=envelope(
        {}, status=False, message="Το προϊόν δεν βρέθηκε"
    ))
    with pytest.raises(api.APIError) as ctx:
        client._post("cart/add", {})
    assert str(ctx.value) == "Το προϊόν δεν βρέθηκε"
    assert ctx.value.exit_code == 1


def test_resolve_product_id_accepts_embedded_ids() -> None:
    assert api.resolve_product_id(
        "https://www.e-fresh.gr/el/cart?product=4300002601"
    ) == "4300002601"
    assert api.resolve_product_id("id 4300002601") == "4300002601"
    assert api.resolve_product_id("4300002601") == "4300002601"
    with pytest.raises(ValueError):
        api.resolve_product_id("no digits here")
    with pytest.raises(ValueError):
        api.resolve_product_id(
            "https://www.e-fresh.gr/el/xarti-pshsimatos-bake-paper-new-logo-sanitas-8-m"
        )
