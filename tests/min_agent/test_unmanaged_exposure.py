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

from min_agent.doctor import DoctorReport, _position_quantity


class _Cfg:
    def __init__(self, allowlist):
        self.allowlist = allowlist


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


def _unmanaged(report, allowlist, positions, equity=100_000.0):
    from min_agent.doctor import _check_unmanaged_exposure

    rep = DoctorReport()
    _check_unmanaged_exposure(rep, _Cfg(allowlist), _Client(equity), positions)
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
