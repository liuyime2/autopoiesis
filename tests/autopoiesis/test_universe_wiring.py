"""Config, gateway and daemon wiring for the wider universe. The broker is a FAKE with a few assets;
this proves the code filters and falls back as described, not anything about a real market."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autopoiesis.config import AgentConfig
from autopoiesis.daemon import AgentDaemon
from autopoiesis.data_gateway import AlpacaDataGateway


def _asset(sym, exchange="NASDAQ", tradable=True):
    return SimpleNamespace(symbol=sym, exchange=exchange, tradable=tradable)


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.assets = [_asset("AAA"), _asset("BBB"), _asset("CCC", "OTC"), _asset("DDD", tradable=False),
                       _asset("EEEEW"), _asset("PENNY"), _asset("FFF"), _asset("GGG")]

    def list_assets(self, **_):
        if self.fail:
            raise RuntimeError("down")
        return self.assets

    def data_get(self, path, params, api_version=None):
        if "most-actives" in path:
            return {"most_actives": [{"symbol": s} for s in ("PENNY", "EEEEW", "CCC", "AAA", "BBB", "HELD", "FFF", "GGG")]}
        return {"gainers": [{"symbol": "GGG", "price": 40}], "losers": []}

    def get_latest_trades(self, syms):
        px = {"PENNY": 0.4, "AAA": 120.0, "BBB": 30.0, "FFF": 9.0, "GGG": 40.0, "HELD": 80.0}
        return {s: SimpleNamespace(price=px.get(s, 0)) for s in syms}


def test_tradable_symbols_keep_major_exchange_tradable_equities_and_cache_for_the_day():
    gw = AlpacaDataGateway(client=FakeClient())
    got = gw.tradable_symbols()
    assert {"AAA", "BBB", "FFF"} <= got and not ({"CCC", "DDD"} & got)  # OTC and untradable are out
    gw.client.fail = True
    assert gw.tradable_symbols() == got                                   # cached, not refetched
    assert AlpacaDataGateway(client=FakeClient(fail=True)).tradable_symbols() is None  # unknown stays unknown


def test_attention_symbols_drop_pennies_warrants_otc_and_whatever_is_already_covered():
    gw = AlpacaDataGateway(client=FakeClient())
    got = gw.attention_symbols(5, min_price=5.0, exclude=frozenset({"HELD"}))
    assert got == ["AAA", "BBB", "FFF", "GGG"]      # PENNY (price), EEEEW (warrant), CCC (OTC), HELD (covered) are out
    assert gw.attention_symbols(0) == [] and gw.attention_symbols(2, exclude=frozenset({"HELD"})) == ["AAA", "BBB"]


def test_the_universe_setting_is_validated_and_a_wider_universe_means_the_whole_account(monkeypatch):
    # isolate from the operator's own env file, which sets every one of these
    for name in ("AUTOPOIESIS_UNIVERSE", "AUTOPOIESIS_MANAGE_ACCOUNT", "AUTOPOIESIS_ATTENTION_SLOTS", "AUTOPOIESIS_MIN_PRICE"):
        monkeypatch.delenv(name, raising=False)
    c = AgentConfig.from_env()
    assert (c.universe, c.min_price, c.attention_slots, c.whole_account) == ("allowlist", 5.0, 0, False)
    monkeypatch.setenv("AUTOPOIESIS_UNIVERSE", "tradable")
    monkeypatch.setenv("AUTOPOIESIS_ATTENTION_SLOTS", "4")
    c = AgentConfig.from_env()
    assert c.universe == "tradable" and c.attention_slots == 4 and c.whole_account
    monkeypatch.setenv("AUTOPOIESIS_UNIVERSE", "everything")
    with pytest.raises(ValueError):
        AgentConfig.from_env()
    monkeypatch.setenv("AUTOPOIESIS_UNIVERSE", "account")
    monkeypatch.setenv("AUTOPOIESIS_ATTENTION_SLOTS", "99")
    with pytest.raises(ValueError):
        AgentConfig.from_env()


def _daemon(universe, slots=0, held=("TLT",), attention=("AAA",), fail_held=False, served=("SPY", "QQQ")):
    gw = SimpleNamespace(
        held_symbols=(lambda: (_ for _ in ()).throw(RuntimeError("x")) if fail_held else list(held)),
        attention_symbols=lambda k, **kw: list(attention)[:k],
    )
    d = AgentDaemon.__new__(AgentDaemon)
    d.config = SimpleNamespace(symbols=("SPY", "QQQ"), universe=universe, attention_slots=slots, min_price=5.0)
    # The real daemon always has a library; these tests used to omit it, which is why the
    # "configured means priority" clause was never exercised here - it was only ever live.
    d.strategy_library = SimpleNamespace(list=lambda: [
        SimpleNamespace(strategy_id="s-" + s, symbols=(s,), enabled=True, lifecycle="ACTIVE") for s in served
    ])
    # None is the mechanical list, which is what these tests are about: the round's coverage.
    # The model's own choice is covered in test_attention_selection.py.
    d.attention_transport = None
    d._append_event = lambda *a, **k: None
    d.loop = SimpleNamespace(data_gateway=gw)
    d.events = []
    d._append_event = lambda *a, **k: d.events.append(k.get("message"))  # type: ignore[method-assign]
    return d


def test_a_round_decides_what_can_change_every_time_and_rotates_the_rest():
    assert _daemon("allowlist", 4)._round_symbols() == ["SPY", "QQQ"]
    assert _daemon("account", 4)._round_symbols() == ["SPY", "QQQ", "TLT"]            # attention needs `tradable`
    got = _daemon("tradable", 4, held=("TLT", "SPY"), attention=("AAA", "BBB"))._round_symbols()
    # Served and held symbols are decided every round; the watchlist fills one rotating slot.
    # Measured 2026-10-19: with every pick in every round, ten symbols at 1.75 minutes each decided
    # every symbol every 17.5 minutes, which is the cadence problem the rotation exists to fix.
    assert got == ["SPY", "QQQ", "TLT", "AAA"] and len(set(got)) == len(got)


def test_a_configured_symbol_with_nothing_to_run_rotates_instead_of_leading_the_round():
    """Intent is not capability. Measured 2026-10-22, live: 51 of 118 decisions were on XLB,
    XLE and XLF - configured, and served by no strategy at all - so each could only produce
    `no_strategy`: no order, no evidence, about 1.6 minutes of model time every round. That
    was most of the gap between the measured 12-minute per-symbol interval and the ten the
    review asks for. A configured symbol that nothing serves is discovery, and rotates."""
    d = _daemon("tradable", 4, held=("TLT",), attention=("AAA",), served=("SPY",))
    d.config = SimpleNamespace(symbols=("SPY", "QQQ", "ZZZ"), universe="tradable", attention_slots=4, min_price=5.0)
    d.strategy_library = SimpleNamespace(list=lambda: [
        SimpleNamespace(strategy_id="s-SPY", symbols=("SPY",), enabled=True, lifecycle="ACTIVE")
    ])
    d.attention_transport = None
    seen = set()
    for cycle in range(4):
        d.cycle_count = cycle
        decided = d._round_symbols()
        assert decided[0] == "SPY", decided            # served: every round
        assert "TLT" in decided, decided               # held: every round
        rest = [s for s in decided if s not in {"SPY", "TLT"}]
        assert len(rest) == 1, decided                 # one discovery slot, not three
        seen.update(rest)
    # QQQ and ZZZ are configured but unserved and unheld: they take turns, they do not lead.
    assert seen == {"QQQ", "ZZZ", "AAA"}, f"rotation must still cover them: {seen}"


def test_a_broker_failure_narrows_the_round_instead_of_stopping_it():
    d = _daemon("tradable", 4, fail_held=True)
    assert d._round_symbols()[:2] == ["SPY", "QQQ"] and any("could not list held symbols" in str(m) for m in d.events)


def test_the_round_uses_the_models_choice_and_records_it():
    """The integration the module needs: the daemon asks, obeys, and journals.

    Three things at once, because they are the whole point of wiring it in: the model's
    symbol reaches the round, the mechanical list does not, and an event is written even
    though the round continued. The plan's 24-hour evaluation compares chosen symbols
    against unchosen ones and cannot do that from a record that keeps only some rounds.
    """
    import json

    d = _daemon("tradable", slots=2, held=("TLT",), attention=("AAA", "BBB", "CCC"))
    d.attention_transport = lambda prompt, schema: json.dumps(
        {"choices": [{"symbol": "CCC", "reason": "the model picked this one"}]}
    )
    events = []
    d._append_event = lambda event_type, **kwargs: events.append((event_type, kwargs))

    symbols = d._round_symbols()

    assert "CCC" in symbols, f"the model's choice must reach the round: {symbols}"
    assert "AAA" not in symbols, (
        "the mechanical first-k must not also be included, or the model chose nothing"
    )
    kinds = [e[0] for e in events]
    assert kinds == ["ATTENTION_SELECTED"], kinds
    payload = events[0][1]["payload"]
    assert payload["source"] == "model"
    assert payload["symbols"] == ["CCC"]
    assert payload["reasons"] == {"CCC": "the model picked this one"}
    assert "AAA" in payload["candidates"], (
        "the candidates are recorded so the choice can be judged against what was offered"
    )


def test_a_model_that_does_not_answer_still_leaves_a_usable_watchlist():
    """The round must not depend on the model being reachable."""
    d = _daemon("tradable", slots=2, held=("TLT",), attention=("AAA", "BBB", "CCC"))

    def broken(prompt, schema):
        raise TimeoutError("slow")

    d.attention_transport = broken
    events = []
    d._append_event = lambda event_type, **kwargs: events.append((event_type, kwargs))

    symbols = d._round_symbols()

    assert {"SPY", "QQQ", "TLT"} <= set(symbols), (
        f"the configured and held symbols must stand whatever the model does: {symbols}"
    )
    others = [s for s in symbols if s not in {"SPY", "QQQ", "TLT"}]
    assert len(others) == 1, (
        f"one mechanical attention pick belongs in the round, not all of them: {symbols}"
    )
    assert events[0][1]["payload"]["source"] == "mechanical"
    assert "TimeoutError" in events[0][1]["payload"]["note"]
