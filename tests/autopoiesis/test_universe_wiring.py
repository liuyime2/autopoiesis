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


def _daemon(universe, slots=0, held=("TLT",), attention=("AAA",), fail_held=False):
    gw = SimpleNamespace(
        held_symbols=(lambda: (_ for _ in ()).throw(RuntimeError("x")) if fail_held else list(held)),
        attention_symbols=lambda k, **kw: list(attention)[:k],
    )
    d = AgentDaemon.__new__(AgentDaemon)
    d.config = SimpleNamespace(symbols=("SPY", "QQQ"), universe=universe, attention_slots=slots, min_price=5.0)
    d.loop = SimpleNamespace(data_gateway=gw)
    d.events = []
    d._append_event = lambda *a, **k: d.events.append(k.get("message"))  # type: ignore[method-assign]
    return d


def test_a_round_covers_configured_then_held_then_attention_symbols_without_duplicates():
    assert _daemon("allowlist", 4)._round_symbols() == ["SPY", "QQQ"]
    assert _daemon("account", 4)._round_symbols() == ["SPY", "QQQ", "TLT"]            # attention needs `tradable`
    got = _daemon("tradable", 4, held=("TLT", "SPY"), attention=("AAA", "BBB"))._round_symbols()
    assert got == ["SPY", "QQQ", "TLT", "AAA", "BBB"] and len(set(got)) == len(got)


def test_a_broker_failure_narrows_the_round_instead_of_stopping_it():
    d = _daemon("tradable", 4, fail_held=True)
    assert d._round_symbols()[:2] == ["SPY", "QQQ"] and any("could not list held symbols" in str(m) for m in d.events)
