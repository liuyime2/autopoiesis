"""The v6 forward basket follows a validated targets file inside a capped pool, or holds.

All fixtures are synthetic. Usage: pytest tests/autopoiesis/test_basket.py -q
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from autopoiesis import basket as b
from autopoiesis.config import AgentConfig
from autopoiesis.models import AccountSnapshot, DataSnapshot, OpenOrderSnapshot, PositionSnapshot

TODAY = date(2026, 11, 30)
NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)  # 10:00 New York


def _write(directory: Path, effective: date, symbols, edit: dict | None = None) -> Path:
    body = {"strategy_id": b.BASKET_STRATEGY_ID, "effective": effective.isoformat(), "symbols": list(symbols)}
    body["sha256"] = b.body_sha256(body)
    body.update(edit or {})  # after the digest: an edited file
    path = directory / f"targets-{effective.isoformat()}.json"
    path.write_text(json.dumps(body))
    return path


def _snap(symbol: str, *, price: float = 100.0, held: float = 0, market_open: bool = True, orders=()) -> DataSnapshot:
    positions = (PositionSnapshot(symbol=symbol, quantity=held, market_value=held * price),) if held else ()
    return DataSnapshot(
        symbol=symbol, timestamp=NOW, market_open=market_open, last_price=price, source="alpaca",
        account=AccountSnapshot(equity=100_000, cash=50_000, buying_power=50_000, portfolio_value=100_000, daily_loss=0),
        positions=positions, open_orders=tuple(orders),
    )


def _basket(directory: Path, pool: float = 30_000.0, **kw) -> b.BasketSleeve:
    return b.BasketSleeve(pool=pool, targets_dir=directory, clock=lambda: NOW, **kw)


def test_a_new_name_is_bought_to_its_equal_share_of_the_pool(tmp_path):
    _write(tmp_path, TODAY, ["AAA", "BBB", "CCC"])
    d = _basket(tmp_path).plan(_snap("AAA", price=100.0))
    assert d is not None and (d.action, d.quantity, d.strategy_id) == ("BUY", 100, b.BASKET_STRATEGY_ID)


def test_a_name_inside_the_band_is_left_and_one_outside_is_resized(tmp_path):
    _write(tmp_path, TODAY, ["AAA", "BBB"])  # $15,000 each at $100 = 150 shares, band $3,750
    s = _basket(tmp_path)
    assert s.plan(_snap("AAA", held=130)) is None
    d = s.plan(_snap("AAA", held=100))
    assert d is not None and (d.action, d.quantity) == ("BUY", 50)


def test_a_name_an_earlier_file_held_is_sold_in_full(tmp_path):
    _write(tmp_path, date(2026, 11, 23), ["AAA", "OLD"])
    _write(tmp_path, TODAY, ["AAA", "NEW"])
    s = _basket(tmp_path)
    d = s.plan(_snap("OLD", held=37))
    assert d is not None and (d.action, d.quantity) == ("SELL", 37)
    assert s.plan(_snap("OLD", held=0)) is None and s.owns("OLD")  # still owned, so held exclusively


def _names(n: int) -> list[str]:
    return [chr(65 + i // 26) + chr(65 + i % 26) + "X" for i in range(n)]  # AAX, ABX, ...


def test_shares_round_to_the_nearest_whole_share(tmp_path):
    _write(tmp_path, TODAY, _names(50))  # $600 a name
    d = _basket(tmp_path).plan(_snap("ABX", price=900.0))
    assert d is not None and d.quantity == 1  # round(0.67) - a floor would hold none


@pytest.mark.parametrize("problem", ["edited", "wrong_id", "too_many", "future", "stale"])
def test_an_unusable_newest_file_means_no_trade_and_no_fallback(tmp_path, problem):
    _write(tmp_path, date(2026, 11, 23), ["AAA"])  # a good older file is not traded in its place
    if problem == "edited":
        _write(tmp_path, TODAY, ["AAA"], edit={"symbols": ["ZZZ"]})
    elif problem == "wrong_id":
        p = _write(tmp_path, TODAY, ["AAA"])
        body = json.loads(p.read_text()) | {"strategy_id": "other"}
        body["sha256"] = b.body_sha256(body)
        p.write_text(json.dumps(body))
    elif problem == "too_many":
        _write(tmp_path, TODAY, _names(b.MAX_TARGETS + 1))
    elif problem == "future":
        for p in tmp_path.glob("*.json"):
            p.unlink()
        _write(tmp_path, date(2026, 12, 7), ["AAA"])
    else:
        for p in tmp_path.glob("*.json"):
            p.unlink()
        _write(tmp_path, date(2026, 11, 13), ["AAA"])
    s = _basket(tmp_path)
    assert s.plan(_snap("AAA")) is None
    assert s.state.current is None and s.state.problem


def test_excluded_symbols_are_dropped_from_the_targets(tmp_path):
    _write(tmp_path, TODAY, ["SPY", "AAA"])
    s = _basket(tmp_path, excluded={"spy"})
    assert s.plan(_snap("SPY")) is None and not s.owns("SPY")
    d = s.plan(_snap("AAA"))
    assert d is not None and d.quantity == 300  # the whole pool over the one name left


def test_closed_market_and_resting_orders_are_left_alone(tmp_path):
    _write(tmp_path, TODAY, ["AAA"])
    s = _basket(tmp_path)
    assert s.plan(_snap("AAA", market_open=False)) is None
    order = OpenOrderSnapshot(order_id="o1", symbol="AAA", side="buy", quantity=1, status="new")
    assert s.plan(_snap("AAA", orders=[order])) is None


def test_the_pool_is_capped_in_code_and_off_by_default():
    with pytest.raises(ValueError):
        b.BasketSleeve(pool=b.BASKET_POOL_CAP + 1, targets_dir=Path("."))
    assert AgentConfig.from_env({}).basket_enabled is False
    assert AgentConfig.from_env({"AUTOPOIESIS_BASKET_POOL": "30000"}).basket_enabled
    with pytest.raises(ValueError):
        AgentConfig.from_env({"AUTOPOIESIS_BASKET_POOL": "30001"})


def test_every_decision_carries_provenance_the_verifier_accepts(tmp_path):
    from autopoiesis.learning_evidence import valid_provenance

    _write(tmp_path, TODAY, ["AAA", "BBB"])
    s = _basket(tmp_path)
    for d in (s.plan(_snap("AAA")), s.hold(_snap("BBB"))):
        p = valid_provenance(d.strategy_provenance)
        assert p is not None and p.strategy_id == d.strategy_id and d.symbol in p.universe


def test_rounds_add_unheld_targets_early_and_hold_owned_names_back_later(tmp_path):
    _write(tmp_path, TODAY, ["AAA", "BBB"])
    s = _basket(tmp_path)
    assert b.round_symbols(["SPY", "AAA"], s, 0) == ["SPY", "AAA", "BBB"]
    assert b.round_symbols(["SPY", "AAA", "NVDA"], s, b.BASKET_ROUNDS_PER_DAY) == ["SPY", "NVDA"]


def test_the_loop_routes_owned_names_to_the_basket_and_the_gate_admits_it(tmp_path):
    from test_loop import FakeDataGateway, FakeDecisionEngine, FakeExecutor, FakeGuardian, FakeJournal

    from autopoiesis.daemon import AgentDaemon
    from autopoiesis.loop import TradingLoop

    _write(tmp_path, TODAY, ["AAA"])

    class Gateway(FakeDataGateway):
        def snapshot(self, symbol):
            return _snap(symbol)

    class Journal(FakeJournal):
        def read_events(self, event_type=None, limit=None, *, since=None):
            return []  # nothing in flight

        def read_all(self, *, since=None):
            return []

    loop = TradingLoop(data_gateway=Gateway(), decision_engine=FakeDecisionEngine(), guardian=FakeGuardian(),
                       executor=FakeExecutor(), journal=Journal(), mode="paper")
    gate = AgentDaemon._screen_passed.__get__(type("D", (), {"loop": loop, "_screen_verdicts": {}})())
    assert gate(b.BASKET_STRATEGY_ID) is False  # not configured: no exception
    loop.basket = _basket(tmp_path)
    loop.accumulation_allowed = gate
    record = loop.run_once("AAA")
    assert record.decision.strategy_id == b.BASKET_STRATEGY_ID and record.decision.action == "BUY"
    assert "has not passed the screen" not in record.decision.rationale
    other = loop.run_once("ZZZ")  # not owned: the ordinary engine decides
    assert other.decision.strategy_id != b.BASKET_STRATEGY_ID
