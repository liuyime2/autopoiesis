from __future__ import annotations

import ast
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from urllib3.util import Timeout

from autopoiesis.broker_client import REQUEST_TIMEOUT, create_alpaca_client

ROOT = Path(__file__).resolve().parents[2]


def _config():
    return SimpleNamespace(
        alpaca_base_url="https://paper-api.alpaca.markets",
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
    )


class _CaptureSend:
    def __init__(self):
        self.timeouts = []

    def __call__(self, prepared, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.url = prepared.url
        response.request = prepared
        return response


def test_sdk_request_path_passes_a_fresh_timeout_to_requests_send(monkeypatch):
    import alpaca_trade_api as tradeapi

    assert importlib.metadata.version("alpaca-trade-api") == "3.2.0"
    monkeypatch.setenv("APCA_RETRY_MAX", "9")
    monkeypatch.setenv("APCA_RETRY_WAIT", "30")
    monkeypatch.setenv("APCA_RETRY_CODES", "400,429,504")
    config = _config()
    client = create_alpaca_client(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
    )
    capture = _CaptureSend()
    monkeypatch.setattr(client._session, "send", capture)

    client.get_clock()
    client.get_clock()

    assert len(capture.timeouts) == 2
    assert all(isinstance(timeout, Timeout) for timeout in capture.timeouts)
    assert capture.timeouts[0] is not capture.timeouts[1]
    assert capture.timeouts[0].total == REQUEST_TIMEOUT.total
    assert capture.timeouts[0]._connect == REQUEST_TIMEOUT.connect
    assert capture.timeouts[0]._read == REQUEST_TIMEOUT.read
    assert client._retry == 0
    assert client._retry_wait == 0
    assert client._retry_codes == []
    assert isinstance(client, tradeapi.REST)


def test_factory_rejects_non_paper_endpoints_and_unreviewed_sdk(monkeypatch):
    config = _config()
    with pytest.raises(ValueError, match="paper"):
        create_alpaca_client(
            key_id=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            base_url="https://api.alpaca.markets",
        )

    import autopoiesis.broker_client as broker_client

    monkeypatch.setattr(broker_client, "_sdk_version", lambda: "3.3.0")
    with pytest.raises(RuntimeError, match="only audited"):
        config = _config()
        create_alpaca_client(
            key_id=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            base_url=config.alpaca_base_url,
        )


def test_all_app_and_tool_rest_construction_uses_the_factory():
    direct_construction = []
    for root in (ROOT / "src" / "autopoiesis", ROOT / "tools"):
        for path in root.rglob("*.py"):
            if path.name == "broker_client.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                direct = isinstance(function, ast.Name) and function.id == "REST"
                direct = direct or (
                    isinstance(function, ast.Attribute) and function.attr == "REST"
                )
                if direct:
                    direct_construction.append(str(path.relative_to(ROOT)))
    assert direct_construction == []


def test_the_session_override_forwards_positional_arguments_and_forces_the_timeout():
    """The signature that mypy demanded, checked for the bug it could have introduced.

    CI failed type-check on this class for three Python versions while the same `mypy
    src/autopoiesis` passed here. The reason is not the version: `requests` on this host ships
    without `py.typed`, so with `ignore_missing_imports` the superclass resolved to `Any` and the
    override could not be checked. In a clean `pip install -e ".[dev]"` - which is what CI does -
    the installed `requests` is typed, and mypy sees that the subclass does not accept every call
    the base does.

    The fix is `*args: Any`, because `Session.request`'s parameters after `url` are
    positional-or-keyword and a caller may pass `params`, `data`, `headers` positionally.
    **`*args` also silently changes behaviour if it is not forwarded**, which is the trap this
    test exists for: `super().request(method, url, **kwargs)` drops every positional argument the
    caller passed, so the first broker call that used one would have sent a request missing its
    body and nothing in the type checker would have complained.

    Both properties are pinned here: the extras reach the base, and the timeout this class
    exists to supply is still the one that is used.
    """
    from autopoiesis.broker_client import TimedRequestsSession

    sent: dict[str, object] = {}

    def fake_send(self, prepared, **kwargs):  # signature copied from requests' Session.send
        sent["prepared"] = prepared
        sent["send_kwargs"] = kwargs
        return "response"

    session = TimedRequestsSession()
    monkeypatch_send = TimedRequestsSession.send
    TimedRequestsSession.send = fake_send
    try:
        # A call shaped like one a caller may make: `params` and `data` passed positionally.
        result = session.request("POST", "https://example.test/v2/orders", {"limit": 1}, "body")
    finally:
        TimedRequestsSession.send = monkeypatch_send

    assert result == "response"
    prepared = sent["prepared"]
    # The positional arguments must have reached the request, not been dropped by `*args`.
    assert prepared is not None and "limit=1" in (prepared.url or ""), (
        "`*args` must be forwarded to super(); a positional parameter that is accepted and then "
        "dropped is worse than the signature error it fixed"
    )
    body = prepared.body
    body_text = body.decode() if isinstance(body, bytes) else str(body or "")
    assert "body" in body_text, "the positional body must survive the override"
    assert isinstance(sent["send_kwargs"].get("timeout"), Timeout), (
        "the override's whole purpose is to bound every call with a fresh timeout"
    )


@pytest.mark.parametrize("bad_orders", [None, {}, [object()], [SimpleNamespace(id="x", symbol="SPY", side="buy", qty="nan", status="new")]])
def test_synthetic_open_order_reader_refuses_unknown_contents(bad_orders):
    from autopoiesis.broker_client import list_open_orders

    client = SimpleNamespace(list_orders=lambda **kwargs: bad_orders)
    with pytest.raises(RuntimeError):
        list_open_orders(client)


def test_synthetic_smoke_inventories_all_classes_without_quotes(monkeypatch, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("alpaca_smoke", ROOT / "tools" / "alpaca_smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    calls = []

    def asset(symbol):
        calls.append(("asset", symbol))
        if symbol == "MISSING":
            raise LookupError("synthetic metadata outage")
        return SimpleNamespace(symbol=symbol, **{"class": {
            "SPY": "us_equity", "OPTION": "us_option", "BTCUSD": "crypto",
        }[symbol]}, tradable=True, fractionable=False)

    positions = [SimpleNamespace(symbol=symbol, asset_class=kind, side="long", qty="1",
                                 market_value="10", avg_entry_price="10", unrealized_pl="0") for symbol, kind in (
        ("SPY", "us_equity"), ("OPTION", "us_option"), ("BTCUSD", "crypto"))]
    client = SimpleNamespace(
        get_clock=lambda: SimpleNamespace(timestamp="synthetic", is_open=False, next_open="later", next_close="later"),
        get_account=lambda: SimpleNamespace(status="ACTIVE", equity="100", cash="50", buying_power="50",
                                            portfolio_value="100", last_equity="100", trading_blocked=False,
                                            account_blocked=False, trade_suspended_by_user=False,
                                            options_approved_level=2, options_buying_power="20",
                                            non_marginable_buying_power="30", crypto_status="ACTIVE"),
        list_positions=lambda: positions,
        list_orders=lambda **kwargs: calls.append(("orders", kwargs)) or [],
        get_asset=asset,
    )
    # This synthetic read-only broker deliberately exposes no quote/model/mutation method.
    config = _config()
    config.symbols = ("SPY", "MISSING")
    monkeypatch.setattr(smoke.AgentConfig, "from_env", lambda: config)
    monkeypatch.setattr(smoke, "create_alpaca_client", lambda **kwargs: client)
    assert smoke.main() == 0
    output = capsys.readouterr().out
    for observed in ("us_equity", "us_option", "crypto", "options_approved_level: 2",
                     "options_trading_level: UNKNOWN", "trading_blocked: False",
                     "account_blocked: False", "trade_suspended_by_user: False",
                     "options_buying_power: 20", "non_marginable_buying_power: 30", "crypto_status: ACTIVE",
                     "MISSING", "UNKNOWN", "UNSUPPORTED", "Open orders complete: True"):
        assert observed in output
    assert {symbol for name, symbol in calls if name == "asset"} == {"SPY", "OPTION", "BTCUSD", "MISSING"}
    assert ("orders", {"status": "open", "limit": 500}) in calls
    assert "test-key" not in output and "test-secret" not in output


@pytest.mark.parametrize("orders", [None, [object()], [object()] * 500])
def test_synthetic_smoke_reports_incomplete_open_orders(monkeypatch, capsys, orders):
    import importlib.util

    spec = importlib.util.spec_from_file_location("alpaca_smoke", ROOT / "tools" / "alpaca_smoke.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    client = SimpleNamespace(get_clock=lambda: SimpleNamespace(), get_account=lambda: SimpleNamespace(),
                             list_positions=list, list_orders=lambda **kwargs: orders,
                             get_asset=lambda symbol: SimpleNamespace(**{"class": "us_equity"}))
    config = _config()
    config.symbols = ("SPY",)
    monkeypatch.setattr(smoke.AgentConfig, "from_env", lambda: config)
    monkeypatch.setattr(smoke, "create_alpaca_client", lambda **kwargs: client)
    assert smoke.main() == 1
    output = capsys.readouterr().out
    assert "Open orders complete: False" in output
    for field in ("crypto_status", "non_marginable_buying_power", "options_buying_power"):
        assert f"{field}: UNKNOWN" in output
