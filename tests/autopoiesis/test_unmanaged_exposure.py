"""The account is not only the agent's book, and the difference has to be visible.

This account holds BIL, TLT, XLB, XLE and XLF alongside the allowlist book, about
$37k of long market value the agent neither chose nor can attribute. The agent's
*exposure* limits are allowlist-scoped on purpose - it cannot increase exposure
outside the allowlist. But `daily_loss` is not exposure: it is
`day_start_equity - equity` over the whole account, so a drop in BIL or TLT eats
the agent's $500 daily-loss budget and trips a risk limit for a reason that appears
in no decision, no PnL record and no attribution.

The previous version of the broker-positions check reported only a count, so this
composition was invisible. Being conservative is the right failure direction; being
unable to explain why is not.
"""
from __future__ import annotations

import json
from datetime import datetime

from autopoiesis.basket import BASKET_STRATEGY_ID, NEW_YORK, body_sha256
from autopoiesis.doctor import DoctorReport, _position_quantity


class _Cfg:
    def __init__(self, allowlist, basket_targets_dir=None):
        self.allowlist = allowlist
        self.basket_enabled = basket_targets_dir is not None
        self.basket_targets_dir = basket_targets_dir


class _Pos:
    def __init__(self, symbol, qty, market_value):
        self.symbol = symbol
        self.qty = qty
        self.market_value = market_value


class _Client:
    def __init__(self, equity):
        self.equity = equity

    def get_account(self):
        class A:
            pass
        a = A()
        a.equity = self.equity
        return a


def _unmanaged(report, allowlist, positions, equity=100_000.0, targets_dir=None):
    from autopoiesis.doctor import _check_unmanaged_exposure

    rep = DoctorReport()
    _check_unmanaged_exposure(rep, _Cfg(allowlist, targets_dir), _Client(equity), positions)
    return next((c for c in rep.checks if c.name == "unmanaged exposure"), None)


def test_quantities_survive_alpaca_string_numbers():
    """alpaca-trade-api returns numbers as strings. Reporting 0 shares next to
    $37,000 of market value would be worse than reporting nothing."""
    assert _position_quantity(_Pos("BIL", "209", "19154.85")) == 209.0
    assert _position_quantity(_Pos("SPY", 10, "7654.40")) == 10.0
    assert _position_quantity(_Pos("X", "", "1")) == 0.0
    assert _position_quantity(_Pos("X", None, "1")) == 0.0


def test_non_allowlist_positions_are_reported_with_value_and_share():
    check = _unmanaged(
        None, ["SPY", "QQQ"],
        [_Pos("SPY", "10", "7654.40"), _Pos("BIL", "209", "19154.85"),
         _Pos("TLT", "226", "17754.56")],
    )
    assert check is not None and check.status.value == "warn"
    assert "BIL=209" in check.detail and "TLT=226" in check.detail, check.detail
    assert "SPY" not in check.detail, "an allowlist position is not unmanaged"
    assert "36,909" in check.detail, check.detail
    assert "37% of equity" in check.detail, check.detail


def test_an_account_within_the_allowlist_reports_nothing():
    check = _unmanaged(None, ["SPY", "QQQ"], [_Pos("SPY", "10", "7654.40")])
    assert check is None, "nothing to say about a clean book"


def test_daily_loss_coupling_is_stated():
    """The reason this matters has to travel with the number."""
    check = _unmanaged(None, ["SPY"], [_Pos("BIL", "209", "19154.85")])
    assert "daily_loss" in check.hint
    assert "without appearing in any PnL record" in check.hint


def _targets(directory, symbols):
    """SYNTHETIC targets file, effective today."""
    today = datetime.now(NEW_YORK).date().isoformat()
    body = {"strategy_id": BASKET_STRATEGY_ID, "effective": today, "symbols": symbols}
    body["sha256"] = body_sha256(body)
    (directory / f"targets-{today}.json").write_text(json.dumps(body))
    return directory


def test_basket_positions_are_not_unmanaged_and_others_still_are(tmp_path):
    """Measured 2026-11-25: 50 basket positions, attributed to the basket in PnL evidence, were
    reported as ones the agent cannot manage or attribute."""
    positions = [_Pos("MRNA", "3", "654.36"), _Pos("BIL", "209", "19154.85")]
    check = _unmanaged(None, ["SPY"], positions, targets_dir=_targets(tmp_path, ["MRNA"]))
    assert "BIL=209" in check.detail and "MRNA" not in check.detail, check.detail
    assert "19,154.85" in check.detail and "another 1 belong to the forward basket" in check.detail


def test_a_book_of_basket_names_reports_nothing(tmp_path):
    directory = _targets(tmp_path, ["MRNA"])
    assert _unmanaged(None, ["SPY"], [_Pos("MRNA", "3", "654.36")], targets_dir=directory) is None


def test_with_the_basket_off_its_names_are_reported(tmp_path):
    _targets(tmp_path, ["MRNA"])
    check = _unmanaged(None, ["SPY"], [_Pos("MRNA", "3", "654.36")])
    assert "MRNA=3" in check.detail
