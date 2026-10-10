"""A NaN limit silently disables the Guardian, and the reports stay green.

Measured 2026-10-10 with an offline synthetic fixture - no broker, no order: a BUY of 10,000
against a configured 5,000 limit is refused, and **approved** when the limit is NaN. `NaN <= 0` is
False, so the positivity check passes it; then every comparison the Guardian makes against it is
also False, so the check that "cannot be bypassed" stops comparing without anything reporting it.

The tests below pin both directions: the non-finite value is refused, and a finite one behaves
exactly as before. A fix that only did the first half would be a way to reject a value nobody
sets while leaving the path intact.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from autopoiesis.config import _finite, _positive_float
from autopoiesis.guardian import Guardian
from autopoiesis.models import (
    AccountSnapshot,
    DataSnapshot,
    TradeDecision,
)

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_risk_limit_is_refused_at_the_loader(bad):
    """Where the value enters, not where it is used: a Guardian that stops comparing is
    indistinguishable from one that approved."""
    with pytest.raises(ValueError):
        _finite("MIN_AGENT_MAX_POSITION_VALUE", bad)


def test_a_finite_limit_is_unchanged():
    """The fix must not narrow what a legitimate operator can configure."""
    assert _finite("x", 25000.0) == 25000.0
    assert _finite("x", 5000.0) == 5000.0
    assert _positive_float("x", 0.05) == pytest.approx(0.05)


def test_a_nan_limit_cannot_silently_approve_an_order_it_should_refuse():
    """The defect, end to end, on the synthetic fixture that found it."""
    snap = DataSnapshot(
        symbol="SPY", timestamp=NOW, market_open=True, last_price=100.0, source="alpaca",
        account=AccountSnapshot(equity=100000.0, cash=50000.0, buying_power=150000.0,
                                portfolio_value=100000.0, daily_loss=0.0),
    )
    order = TradeDecision(symbol="SPY", action="BUY", quantity=100, confidence=0.9, rationale="t")
    refused = Guardian(allowlist=frozenset({"SPY"}), max_position_value=5000.0,
                       max_daily_loss=500.0, max_total_exposure=20000.0,
                       max_trades_per_day=10, min_confidence=0.5).review(order, snap, now=NOW)
    assert refused.approved is False
    # The same order under a limit that cannot be expressed is not a limit at all. Constructing
    # the Guardian with one now raises rather than producing a checker that approves everything.
    with pytest.raises(ValueError):
        Guardian(allowlist=frozenset({"SPY"}), max_position_value=float("nan"),
                 max_daily_loss=500.0, max_total_exposure=20000.0,
                 max_trades_per_day=10, min_confidence=0.5)
