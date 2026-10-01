"""Phase 4: the backtest, walk-forward, cost, leakage and stress controls.

The rules these tests pin are the ones that stop a backtest from being believed:

* a rule may not see a price from after its own decision bar;
* cost is charged on both sides, because the paper broker charged nothing;
* a control that cannot be beaten means the harness is measuring noise;
* thin data is reported as thin, before any confident label is attached;
* searching more variants demands a better result, and the trial count is output.

A note on scale. There are 467 distinct real SPY observations, which is why several
of these tests assert that the verdict is *refusing to conclude*. That refusal is
the feature. A backtest that printed a confident verdict on this little data would
be the defect.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest

from min_agent.research import backtest as bt
from min_agent.research import walk_forward as wf

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _bars(prices):
    return [bt.Bar(T0 + timedelta(minutes=30 * i), p) for i, p in enumerate(prices)]


RISING = [100.0 + i for i in range(60)]
FALLING = [160.0 - i for i in range(60)]
#: A drift of 1% against a 20% threshold: the whole series stays inside it.
FLAT = [100.0 + i * 0.1 for i in range(60)]


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

def test_the_baseline_control_never_trades():
    """If holding cannot be described, the harness is measuring its own noise."""
    result = bt.run_backtest(
        RISING and _bars(RISING), strategy_id="h", kind=bt.KIND_HOLD_BASELINE,
        parameters={},
    )

    assert result.trades == 0
    assert result.net_pnl == 0.0
    assert result.power().startswith("INSUFFICIENT")


def test_fixed_size_buys_once_and_holds():
    result = bt.run_backtest(
        _bars(RISING), strategy_id="f", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
    )

    assert result.trades == 1
    assert result.wins + result.losses == 1, "the open position is closed at the end"
    assert result.net_pnl > 0, "a rising market must pay a long position"


def test_a_sell_with_no_position_is_a_no_op_not_a_short():
    """The engine is long-only, matching the production executor. The first version
    advertised a SELL rule and then quietly did nothing, which would have made a
    short strategy backtest as flat rather than as unsupported."""
    result = bt.run_backtest(
        _bars(RISING), strategy_id="s", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "SELL", "quantity": 1, "confidence": 0.6},
    )
    assert result.trades == 0
    assert result.net_pnl == 0.0


def test_trend_follow_does_not_trade_inside_its_threshold():
    """A 5% move with a 20% threshold must produce no trades at all. Firing inside
    the threshold is how a 'trend' strategy becomes a constant bet."""
    result = bt.run_backtest(
        _bars(FLAT), strategy_id="t", kind=bt.KIND_TREND_FOLLOW,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6,
                    "threshold_pct": 0.20},
    )
    assert result.trades == 0


def test_trend_follow_fires_once_the_move_clears_the_threshold():
    result = bt.run_backtest(
        _bars(RISING), strategy_id="t", kind=bt.KIND_TREND_FOLLOW,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6,
                    "threshold_pct": 0.05},
    )
    assert result.trades >= 1


def test_an_unexecutable_spec_is_reported_not_crashed():
    """A batch run over the whole registry must not die on one odd spec, and the odd
    spec must stay visible in the report."""
    for kind, params in (
        (bt.KIND_TREND_FOLLOW, {"threshold_pct": 0.9}),      # outside (0, 0.20]
        (bt.KIND_FIXED_SIZE, {"action": "HOLD", "quantity": 1}),
        ("SOMETHING_NEW", {}),
        (bt.KIND_FIXED_SIZE, {"action": "BUY", "quantity": 0}),
    ):
        result = bt.run_backtest(
            _bars(RISING), strategy_id="x", kind=kind, parameters=params
        )
        assert result.unsupported, f"{kind} {params} should be reported unsupported"
        assert result.net_pnl == 0.0


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------

def test_cost_is_charged_on_both_sides():
    """The paper broker reported zero commission on all 24 fills. A gross backtest
    would flatter every strategy by exactly what it would really have paid."""
    bars = _bars([100.0, 110.0])
    gross = bt.run_backtest(
        bars, strategy_id="c", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        commission_pct=0.0, slippage_pct=0.0,
    )
    charged = bt.run_backtest(
        bars, strategy_id="c", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        commission_pct=0.05, slippage_pct=0.05,
    )

    assert charged.net_pnl < gross.net_pnl
    assert charged.costs > 0
    assert gross.costs == 0.0
    # 10% gross move must not survive as 10% net once both sides are paid.
    assert charged.return_pct < gross.return_pct


def test_slippage_alone_reduces_a_round_trip():
    bars = _bars([100.0, 100.0])
    free = bt.run_backtest(
        bars, strategy_id="s", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        commission_pct=0.0, slippage_pct=0.0,
    )
    costly = bt.run_backtest(
        bars, strategy_id="s", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1, "confidence": 0.6},
        commission_pct=0.0, slippage_pct=0.25,
    )
    assert costly.net_pnl < free.net_pnl == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------
# leakage
# --------------------------------------------------------------------------

def test_the_causality_guard_catches_a_rule_that_reads_the_future(monkeypatch):
    """The guard is proved by planting a cheat, not by asserting the good rules are
    clean. A leakage check that has only ever seen honest rules has never been
    tested.

    The first version of this guard only noticed the loop index going backwards,
    which no rule in this module could do - it could not have detected exactly the
    bug it existed for.
    """
    def leaky(bars, i):
        # Looks one bar ahead to decide. Everything else about it is a valid rule.
        future = bars[min(i + 1, len(bars) - 1)].price
        action = "BUY" if future > bars[i].price else "HOLD"
        return bt.RuleDecision(bars[i].timestamp, action, 1, bars[i].price, "cheat")

    monkeypatch.setattr(bt, "_rule", lambda kind, params: leaky)
    result = bt.run_backtest(
        _bars(RISING), strategy_id="cheat", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1},
    )

    assert result.leakage_violations > 0
    assert "BROKEN_LEAKAGE" in wf.WalkForwardResult(
        strategy_id="cheat", kind=bt.KIND_FIXED_SIZE,
        folds=[wf.FoldResult(0, 0, 0, result, result)],
    ).verdict()


def test_honest_rules_report_no_leakage():
    for kind, params in (
        (bt.KIND_HOLD_BASELINE, {}),
        (bt.KIND_FIXED_SIZE, {"action": "BUY", "quantity": 1, "confidence": 0.6}),
        (bt.KIND_TREND_FOLLOW, {"action": "BUY", "quantity": 1, "confidence": 0.6,
                                "threshold_pct": 0.05}),
    ):
        result = bt.run_backtest(
            _bars(RISING), strategy_id="h", kind=kind, parameters=params
        )
        assert result.leakage_violations == 0, f"{kind} must be causal"


def test_repeated_quotes_collapse_to_real_observations():
    """49% of consecutive journaled quotes are byte-identical. Counting them makes
    the sample look twice its real size and measures the polling interval."""
    class _Snap:
        def __init__(self, ts, price):
            self.symbol, self.timestamp, self.last_price = "SPY", ts, price
    class _Rec:
        def __init__(self, ts, price):
            self.snapshot = _Snap(ts, price)

    records = [_Rec(T0 + timedelta(minutes=i), 100.0) for i in range(5)]
    records += [_Rec(T0 + timedelta(minutes=5 + i), 101.0) for i in range(3)]

    assert len(bt.bars_from_records(records)) == 2


# --------------------------------------------------------------------------
# walk-forward
# --------------------------------------------------------------------------

def test_walk_forward_refuses_to_run_on_too_little_data():
    result = wf.run_walk_forward(
        _bars([100.0] * 10), strategy_id="t", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1}, n_folds=3, min_train_bars=20,
    )

    assert result.folds == []
    assert result.verdict() == "INCONCLUSIVE_NO_FOLDS"
    assert result.notes, "the reason must be stated, not implied"


def test_each_fold_is_evaluated_only_on_bars_it_never_trained_on():
    bars = _bars([100.0 + i * 0.5 for i in range(200)])
    result = wf.run_walk_forward(
        bars, strategy_id="t", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1}, n_folds=3, min_train_bars=20,
    )

    assert len(result.folds) == 3
    previous = 0
    for fold in result.folds:
        assert fold.test_bars > 0
        assert fold.in_sample.bars == fold.train_bars
        assert fold.out_of_sample.bars == fold.test_bars
        assert fold.train_bars > previous, "folds must move forward in time"
        previous = fold.train_bars
    assert result.folds[-1].train_bars + result.folds[-1].test_bars == len(bars)


def test_thin_evidence_is_reported_before_any_confident_label():
    """Ordering matters. Calling a result overfitted claims it generalised enough to
    be judged, which needs more trades than this data can supply."""
    bars = _bars([100.0 + i * 0.5 for i in range(200)])
    result = wf.run_walk_forward(
        bars, strategy_id="t", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1}, n_folds=3, min_train_bars=20,
    )

    assert result.oos_trades < 10
    assert result.verdict().startswith("INSUFFICIENT")
    assert "OVERFIT" not in result.verdict()
    assert "generalises" in result.verdict()


def test_more_trials_demand_a_better_result():
    """Try forty variants, keep the best, and the winner is mostly noise. The trial
    count is a first-class output so a report cannot hide its own selection bias."""
    bars = _bars([100.0 + i * 0.5 for i in range(200)])
    kwargs = dict(strategy_id="t", kind=bt.KIND_FIXED_SIZE,
                  parameters={"action": "BUY", "quantity": 1}, n_folds=3,
                  min_train_bars=20)
    one = wf.run_walk_forward(bars, trials=1, **kwargs)
    many = wf.run_walk_forward(bars, trials=40, **kwargs)

    # Dividing a zero return bar by the trial count is `0.0 / 40`, which is still
    # `0.0`. The first version of this control did exactly that and so cost nothing;
    # the real penalty for searching N specs is that the winner may be noise, which
    # is answered by demanding more evidence, not a different return.
    assert many.threshold() == one.threshold() == 0.0
    assert many.required_trades() > one.required_trades()
    assert many.required_trades() == math.ceil(10 * 40 ** 0.5)
    assert "40 trial" in many.verdict()


def test_stress_keeps_the_worst_jitter_seed_not_one_arbitrary_one():
    """A single seed is not a stress test. The first run used one and it made a long
    position *better*; seeds here range widely, so the worst is what gets kept."""
    bars = _bars([100.0 + i * 0.5 for i in range(200)])
    result = wf.run_walk_forward(
        bars, strategy_id="t", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1}, n_folds=3, min_train_bars=20,
    )

    seeds = [
        s for s in result.stress if s.scenario.startswith("adverse_jitter_1pct_seed")
    ]
    assert len(seeds) >= 5
    assert any("worst of 5 jitter seeds" in n for n in result.notes)
    named = {s.scenario: s.result.net_pnl for s in seeds}
    assert min(named.values()) < max(named.values()), (
        "the seeds must actually differ, or worst-of-N means nothing"
    )


def test_a_cost_triple_is_a_separate_scenario():
    bars = _bars([100.0 + i * 0.5 for i in range(200)])
    result = wf.run_walk_forward(
        bars, strategy_id="t", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1}, n_folds=3, min_train_bars=20,
    )
    names = {s.scenario for s in result.stress}
    assert {"baseline_costs", "costs_x3", "adverse_shift_-2pct"} <= names


def test_a_passing_verdict_still_says_it_cannot_promote():
    """Even the best possible research outcome is a signal, not an authorisation."""
    result = wf.WalkForwardResult(
        strategy_id="t", kind=bt.KIND_FIXED_SIZE, bars=5000, trials=1,
        folds=[
            wf.FoldResult(i, 100, 100, _win(), _win()) for i in range(3)
        ],
        stress=[wf.StressResult("baseline_costs", _win())],
    )
    verdict = result.verdict()
    assert verdict.startswith("PASSES_RESEARCH_GATE")
    assert "cannot promote" in verdict


def _win():
    return bt.BacktestResult(
        strategy_id="t", kind=bt.KIND_FIXED_SIZE, bars=100, trades=10,
        wins=6, losses=4, net_pnl=100.0, return_pct=1.0,
    )
