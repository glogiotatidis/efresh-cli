"""CLI surface tests: JSON default when piped, exit codes, rendering."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from efresh_cli import api, cli


def test_search_is_json_when_piped(capsys: pytest.CaptureFixture[str]) -> None:
    client = mock.Mock()
    client.search.return_value = {
        "query": "sanitas",
        "page": 1,
        "pages": 1,
        "count": 1,
        "total": 1,
        "results": [{
            "id": "4300002601",
            "name": "Paper",
            "price": 2.09,
            "price_before_discount": None,
            "unit_price": None,
            "package_size": None,
            "stock": 5,
            "available": True,
            "url": "https://www.e-fresh.gr/el/paper",
        }],
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["search", "sanitas", "-n", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["query"] == "sanitas"
    assert out["count"] == 1
    assert out["results"][0]["id"] == "4300002601"


def test_usage_error_is_structured(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "   "]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "usage"


def test_zero_limit_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["search", "milk", "-n", "0"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "usage"


def test_login_reads_credentials_from_the_environment(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EFRESH_EMAIL", "user@example.test")
    monkeypatch.setenv("EFRESH_PASSWORD", "secret")
    client = mock.Mock()
    client.login.return_value = {
        "logged_in": True,
        "email": "user@example.test",
        "session_file": "/tmp/session.json",
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["auth", "login"]) == 0
    client.login.assert_called_once_with("user@example.test", "secret")
    out = json.loads(capsys.readouterr().out)
    assert out["logged_in"] is True


def test_unknown_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["nonsense"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "usage"


def test_auth_required_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch.object(cli, "build_client") as build:
        build.return_value.cart.side_effect = api.AuthRequired()
        assert cli.main(["cart"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "auth_required"


def test_auth_status_logged_out_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    client = mock.Mock()
    client.status.return_value = {
        "logged_in": False,
        "email": None,
        "session_file": "/tmp/session.json",
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["auth", "status"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["logged_in"] is False


def test_cart_add_uses_the_resolved_id(capsys: pytest.CaptureFixture[str]) -> None:
    client = mock.Mock()
    client.add.return_value = {
        "product_id": "4300002601",
        "quantity": 2,
        "delivery_selected": True,
        "delivery": {"reservation": {"date": "2026-11-27",
                                     "from_time": "07:00", "to_time": "08:30"}},
        "cart": {"items": [], "item_count": 0, "total_quantity": 0,
                 "total": 0, "notices": [], "delivery": None},
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main([
            "cart", "add", "https://www.e-fresh.gr/el/cart?product=4300002601",
            "-q", "2",
        ]) == 0
    client.add.assert_called_once_with("4300002601", quantity=2)
    out = json.loads(capsys.readouterr().out)
    assert out["quantity"] == 2
    assert out["delivery_selected"] is True


def test_json_flag_before_a_cart_subcommand(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    client = mock.Mock()
    client.add.return_value = {
        "product_id": "4300002601", "quantity": 1, "delivery_selected": False,
        "delivery": {}, "cart": {"items": [], "item_count": 0,
                                 "total_quantity": 0, "total": 0, "notices": []},
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["cart", "--json", "add", "4300002601"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["product_id"] == "4300002601"


def test_orders_are_json_when_piped(capsys: pytest.CaptureFixture[str]) -> None:
    client = mock.Mock()
    client.orders.return_value = {
        "page": 1, "per_page": 10, "count": 1, "has_next": False,
        "orders": [{
            "id": "123456789012345678901", "created_at": "2026-01-02 10:00:00",
            "status": "completed", "completed": True, "cancelled": False,
            "total": 12.5, "quantity": 3,
            "delivery": {"date": "2026-01-03", "from_time": "07:00",
                         "to_time": "08:30"},
        }],
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["orders", "-n", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["orders"][0]["status"] == "completed"


def test_order_rejects_a_non_numeric_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["order", "https://example/order/123456"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "usage"


def test_order_detail_is_json_when_piped(capsys: pytest.CaptureFixture[str]) -> None:
    client = mock.Mock()
    client.order.return_value = {
        "id": "123456789012345678901", "created_at": "2026-01-02 10:00:00",
        "status": "completed", "completed": True, "cancelled": False,
        "total": 3.0, "quantity": 2,
        "delivery": {"date": "2026-01-03", "from_time": "07:00",
                     "to_time": "08:30"},
        "item_count": 1,
        "items": [{"id": "4300002601", "name": "Paper", "quantity": 2,
                   "unit_price": 1.5, "line_total": 3.0}],
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["order", "123456789012345678901"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["item_count"] == 1


def test_delivery_set_is_json_when_piped(
    capsys: pytest.CaptureFixture[str]
) -> None:
    client = mock.Mock()
    client.reserve_delivery.return_value = {
        "reservation": {
            "id": 1, "timeslot_id": 75, "date": "2026-11-27",
            "from_time": "07:00", "to_time": "08:30", "address_id": 5,
        },
        "address": {"id": 5},
    }
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main([
            "delivery", "set", "--date", "2026-11-27", "--slot", "75",
            "--address-id", "5",
        ]) == 0
    client.reserve_delivery.assert_called_once_with(
        "2026-11-27", 75, address_id=5
    )
    out = json.loads(capsys.readouterr().out)
    assert out["reservation"]["timeslot_id"] == 75


def test_delivery_set_rejects_a_bad_date(
    capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["delivery", "set", "--date", "27-11-2026", "--slot", "1"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"]["code"] == "usage"


def test_delivery_clear_is_json_when_piped(
    capsys: pytest.CaptureFixture[str]
) -> None:
    client = mock.Mock()
    client.clear_delivery.return_value = {"reservation": None}
    with mock.patch.object(cli, "build_client", return_value=client):
        assert cli.main(["delivery", "clear"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reservation"] is None


def test_money_is_greek(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.money(1234.5) == "1.234,50 €"
    assert cli.money(None) == "—"


def test_json_mode_auto_detects_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert cli.json_mode(None) is False
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert cli.json_mode(None) is True
