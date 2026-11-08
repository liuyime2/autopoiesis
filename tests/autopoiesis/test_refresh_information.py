"""tools/refresh_information.py. Fixtures below are SYNTHETIC, shaped like the real responses
probed on 2026-11-07; none of this is market evidence."""
from __future__ import annotations

import importlib.util
import sys
import urllib.error
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location("refresh_information_under_test", TOOLS / "refresh_information.py")
ri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ri
_spec.loader.exec_module(ri)

TODAY = date(2026, 11, 7)


def _snap(iv):
    return {"impliedVolatility": iv, "latestQuote": {}}


def test_the_atm_call_is_the_one_nearest_30_days_and_nearest_spot():
    snapshots = {
        "XLE261204C00060500": _snap(0.3089),   # dte 27, strike 60.5
        "XLE261204C00062000": _snap(0.2900),   # dte 27, strike 62.0 (nearer spot 61.64)
        "XLE261211C00061500": _snap(0.3021),   # dte 34
        "XLE261204P00061500": _snap(0.9999),   # a put: never chosen
        "XLE261204C00061500": _snap(None),     # no IV: skipped
    }
    chosen = ri.pick_atm_call(snapshots, 61.64, TODAY)
    assert chosen["contract"] == "XLE261204C00062000" and chosen["iv"] == 0.29 and chosen["dte"] == 27


def test_options_iv_reports_ok_empty_and_error_separately():
    def ok(path, params):
        if "trades/latest" in path:
            return {"trade": {"p": 61.64}}
        assert params["type"] == "call" and params["feed"] == "indicative"
        return {"snapshots": {"XLE261204C00062000": _snap(0.29)}}

    fields, status = ri.fetch_options_iv("XLE", ok, TODAY)
    assert status["status"] == "ok" and fields["options_iv"] == 0.29

    empty = lambda path, params: {"trade": {"p": 61.64}} if "trades" in path else {"snapshots": {}}  # noqa: E731
    fields, status = ri.fetch_options_iv("XLE", empty, TODAY)
    assert fields == {} and status["status"] == "empty"

    def broken(path, params):
        raise OSError("404 Not Found")

    fields, status = ri.fetch_options_iv("XLE", broken, TODAY)
    assert fields == {} and status["status"] == "error" and "404" in status["detail"]


def test_dividends_report_the_next_ex_date_and_failures():
    future = date.today().replace(year=date.today().year + 1).isoformat()
    fields, status = ri.fetch_dividends("SPY", lambda s: {"2025-01-02": 1.0, future: 2.0})
    assert status["status"] == "ok" and fields["ex_dividend_date"] == future
    assert fields["dividends"][-1] == {"ex_date": future, "amount": 2.0}
    assert ri.fetch_dividends("SPY", lambda s: {})[1]["status"] == "empty"

    def down(symbol):
        raise RuntimeError("no credentials")

    assert ri.fetch_dividends("SPY", down)[1]["status"] == "error"


def test_one_failing_source_does_not_stop_the_others_and_is_recorded():
    data = ri.refresh_one(
        "XLE",
        http_get=lambda path, params: (_ for _ in ()).throw(OSError("down")),
        yahoo=lambda s: ({}, {"status": "empty", "detail": "", "fetched_at": "t"}),
        dividends=lambda s: ({"dividends": [{"ex_date": "2026-09-01", "amount": 0.5}]}, {"status": "ok", "detail": "", "fetched_at": "t"}),
    )
    assert {k: v["status"] for k, v in data["sources"].items()} == {"yahoo": "empty", "dividends": "ok", "options_iv": "error"}
    assert data["dividends"] and "options_iv" not in data
    assert data["symbol"] == "XLE" and data["as_of"] == data["available_at"]


def test_yahoo_404_is_empty_for_an_etf_and_other_failures_are_errors(monkeypatch):
    class Fake:
        def __init__(self, exc):
            self.exc = exc

        @property
        def info(self):
            raise self.exc

        calendar = None

    import types

    for code, expected in ((404, "empty"), (500, "error")):
        exc = urllib.error.HTTPError("https://x", code, "msg", None, None)
        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda s, e=exc: Fake(e)))
        assert ri.fetch_yahoo("SPY")[1]["status"] == expected

    monkeypatch.setitem(sys.modules, "yfinance", None)  # import fails -> ImportError
    assert ri.fetch_yahoo("SPY")[1]["status"] == "error"


def test_the_cache_dir_argument_keeps_the_runtime_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "refresh_one", lambda symbol, **kw: {"symbol": symbol, "sources": {"yahoo": {"status": "ok"}}})
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    assert ri.main(["--cache-dir", str(tmp_path / "scratch"), "spy", "XLE"]) == 0
    assert sorted(p.name for p in (tmp_path / "scratch").iterdir()) == ["SPY.json", "XLE.json"]


def test_no_blanket_except_is_left():
    import re

    text = (TOOLS / "refresh_information.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*except\s*(Exception\b|BaseException\b)?\s*(as \w+)?\s*:", text, flags=re.M)
