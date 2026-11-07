"""G4 v3 building blocks. Every series here is SYNTHETIC and labelled; none is evidence about markets."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace

from autopoiesis.research.portfolio_experiment import (
    MarkovTiming,
    Momentum,
    SmaBand,
    TargetEngine,
    WalkForwardSignal,
    long_runs,
    portfolio_invariants,
    search_grid,
    signal_path,
)


def synthetic_closes(n: int, *, seed: float = 0.0) -> list[float]:
    """SYNTHETIC: a deterministic wavy upward path."""
    return [100.0 * (1 + 0.0004 * i) * (1 + 0.03 * math.sin(i / 17.0 + seed)) for i in range(n)]


def test_the_grid_is_the_registered_seventeen():
    assert len(search_grid()) == 17


def test_signal_states_are_causal():
    closes = synthetic_closes(400)
    changed = list(closes)
    changed[300:] = [c * 3 for c in changed[300:]]  # SYNTHETIC future shock
    for config in search_grid():
        assert signal_path(config.fresh, closes)[:300] == signal_path(config.fresh, changed)[:300], config.name


def test_walk_forward_selection_never_reads_the_embargoed_or_future_bars():
    """Inject a spike after (refit - embargo): the first refit's choice must not change."""
    train, embargo = 504, 5
    closes = synthetic_closes(700)
    spiked = list(closes)
    first_fit = train + embargo
    for i in range(first_fit - embargo + 1, 700):
        spiked[i] = closes[i] * (5.0 if i % 2 else 0.2)  # SYNTHETIC: wild moves the fit must not see

    def first_choice(series):
        wf = WalkForwardSignal(grid=search_grid(), train=train, step=126, embargo=embargo)
        seen = []
        for c in series[: first_fit + 1]:
            seen.append(c)
            wf(seen)
        return wf.choices[0]

    assert first_choice(closes) == first_choice(spiked)


def test_walk_forward_is_flat_before_its_first_fit():
    wf = WalkForwardSignal(grid=[Momentum(20)], train=50, step=10, embargo=5)
    seen = []
    states = []
    for c in synthetic_closes(80):
        seen.append(c)
        states.append(wf(seen))
    assert not any(states[:55])
    assert wf.choices and wf.choices[0][0] == 55


def test_random_timing_matches_the_target_exposure_and_run_length():
    timing = MarkovTiming(long_fraction=0.62, mean_run=30, seed=3)
    path = [timing([]) for _ in range(200_000)]
    share, mean_run = long_runs(path)
    assert abs(share - 0.62) < 0.02
    assert abs(mean_run - 30) / 30 < 0.1


def _snapshot(price: float, held: float):
    return SimpleNamespace(
        symbol="XLE", last_price=price,
        positions=[SimpleNamespace(symbol="XLE", quantity=held)] if held else [],
        timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def test_target_sizing_is_long_flat_with_a_rebalance_band():
    always_long = TargetEngine(signal=lambda closes: True, pool=5_000, strategy_id="t")
    first = always_long(_snapshot(50.0, 0))
    assert (first.action, first.quantity) == ("BUY", 98)  # floor(0.98 * 5000 / 50)
    near = always_long(_snapshot(52.0, 98))  # target 94, gap 4 < 10% of 94
    assert near.action == "HOLD"
    flat = TargetEngine(signal=lambda closes: False, pool=5_000, strategy_id="t")
    exit_ = flat(_snapshot(50.0, 98))
    assert (exit_.action, exit_.quantity) == ("SELL", 98)


def test_sma_band_keeps_its_state_inside_the_band():
    band = SmaBand(3, 0.05)
    assert band([10, 10, 12]) is True        # above 10.67 * 1.05
    assert band([10, 12, 11.5]) is True      # inside the band: unchanged
    assert band([12, 11.5, 10]) is False     # below 11.17 * 0.95


def test_account_invariants_are_checked_on_the_sum():
    ok = SimpleNamespace(equity_curve=[100.0, 101.0], max_gross_exposure=40.0, min_cash=5.0)
    bad = SimpleNamespace(equity_curve=[100.0, -500.0], max_gross_exposure=60.0, min_cash=-1.0)
    good = portfolio_invariants([ok, ok], max_gross=90.0, max_daily_loss=500.0)
    broken = portfolio_invariants([ok, bad], max_gross=90.0, max_daily_loss=500.0)
    assert good["gross_within_limit"] and good["cash_never_negative"] and good["worst_day_within_limit"]
    assert not broken["gross_within_limit"] and not broken["cash_never_negative"] and not broken["worst_day_within_limit"]


def test_a_book_that_lost_money_sizes_from_what_it_has():
    """Smoke run 2026-11-07: sizing from the pool alone made every later buy unaffordable."""
    engine = TargetEngine(signal=lambda closes: True, pool=5_000, strategy_id="t")
    snap = _snapshot(50.0, 0)
    snap.account = SimpleNamespace(equity=4_000.0)
    order = engine(snap)
    assert (order.action, order.quantity) == ("BUY", 78)  # floor(0.98 * 4000 / 50)
