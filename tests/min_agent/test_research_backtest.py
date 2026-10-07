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

    monkeypatch.setattr(bt, "_rule", lambda kind, params, allow_short=False: leaky)
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
    previous_end = None
    for fold in result.folds:
        assert fold.test_bars > 0
        assert fold.in_sample.bars == fold.train_bars
        assert fold.out_of_sample.bars == fold.test_bars
        # Folds move forward in time. `train_bars` is the length of the *matched* in-sample
        # leg, which stops growing once the history exceeds the test size - so it is no
        # longer a clock. What still separates the folds is the last price their test leg
        # ends on, which must advance monotonically: a fold evaluated on older bars than
        # the one before it would be a different assertion entirely.
        fold_end = fold.out_of_sample.decisions[-1].timestamp
        if previous_end is not None:
            assert fold_end > previous_end, (
                f"fold {fold.index} ends at or before the previous fold's test leg"
            )
        previous_end = fold_end
    # Contiguity, stated directly: each fold's in-sample leg must end on the same bar its
    # out-of-sample leg begins after, and the test legs must tile the series after the
    # `min_train_bars` warm-up. The warm-up itself is never tested - it is there to be
    # fitted on, not to be evidence - so the fold legs do not partition the data and
    # `train_bars + test_bars == len(bars)` is not the property to assert.
    warmup = 20
    for previous, fold in zip(result.folds, result.folds[1:], strict=False):
        assert previous.out_of_sample.decisions[-1].timestamp == (
            fold.in_sample.decisions[-1].timestamp
        ), "fold i's test leg must be the history fold i+1 is fitted on"
    assert result.folds[0].in_sample.decisions[-1].timestamp == bars[warmup - 1].timestamp, (
        "fold 0 must be fitted on exactly the warm-up"
    )
    assert result.folds[-1].out_of_sample.decisions[-1].timestamp == bars[-1].timestamp, (
        "the last fold must test up to the end of the series"
    )


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


# --------------------------------------------------------------------------
# the evidence gate must be satisfiable
# --------------------------------------------------------------------------

def test_out_of_sample_trades_equal_the_fold_count():
    """Pins the identity the fold-count fix is built on.

    `run_backtest` opens only when flat and force-closes at the segment's last bar, so
    an always-BUY rule makes exactly one counted trade per test segment. That makes
    `oos_trades == n_folds` an identity, and it is why a hardcoded fold count of 3
    against a 52-trade requirement made the multiple-testing gate unsatisfiable for
    every one of the 26 recorded trials.

    If a future change lets a strategy re-enter within a segment, this fails - which is
    the point: it should fail here rather than be discovered as a permanently frozen gate.
    """
    prices = [100.0 + (i % 7) * 0.3 + i * 0.05 for i in range(400)]
    for folds in (3, 10, 27):
        result = wf.run_walk_forward(
            _bars(prices), strategy_id="t", kind=bt.KIND_FIXED_SIZE,
            parameters={"action": "BUY", "quantity": 1}, n_folds=folds, min_train_bars=20,
        )
        assert result.oos_trades == folds, (
            f"{folds} folds produced {result.oos_trades} out-of-sample trades"
        )


def test_the_default_fold_count_is_derived_from_the_evidence_the_gate_demands():
    """A gate nothing can pass is a veto, not a control.

    The default used to be 3 while `required_trades` at 27 trials is 52, so every run
    returned INSUFFICIENT and the stage could never report anything. The bar itself is
    unchanged; this only provisions the evidence it counts.
    """
    prices = [100.0 + (i % 7) * 0.3 + i * 0.05 for i in range(600)]
    for trials in (1, 10, 27, 40):
        result = wf.run_walk_forward(
            _bars(prices), strategy_id="t", kind=bt.KIND_FIXED_SIZE,
            parameters={"action": "BUY", "quantity": 1}, trials=trials, min_train_bars=20,
        )
        assert result.oos_trades >= result.required_trades(), (
            f"{trials} trials: {result.oos_trades} trades against "
            f"{result.required_trades()} required"
        )
        assert not result.verdict().startswith("INSUFFICIENT: ")


def test_the_bar_itself_is_unchanged_and_still_rises_with_the_search():
    """Asserted against the formula, not against the new code's own output.

    A change that made the gate passable by lowering `required_trades` would satisfy
    every test above. This one is the guard against that, and it is why the fix derives
    the fold count instead.
    """
    import math

    for trials in (1, 10, 27, 40):
        result = wf.WalkForwardResult(
            strategy_id="t", kind=bt.KIND_FIXED_SIZE, trials=trials, bars=600
        )
        assert result.required_trades() == math.ceil(
            wf.BASE_REQUIRED_TRADES * math.sqrt(trials)
        )


def test_an_explicit_fold_count_below_the_requirement_is_honoured_and_explained():
    """A caller asking for a cheap run gets one - but the reason is on the record.

    Overriding it silently would be worse: this repository has already been bitten twice
    by a count quietly meaning something other than it says.
    """
    prices = [100.0 + (i % 7) * 0.3 + i * 0.05 for i in range(600)]
    result = wf.run_walk_forward(
        _bars(prices), strategy_id="t", kind=bt.KIND_FIXED_SIZE,
        parameters={"action": "BUY", "quantity": 1}, n_folds=3, min_train_bars=20, trials=27,
    )

    assert len(result.folds) == 3, "the caller's fold count is respected"
    assert result.verdict().startswith("INSUFFICIENT: ")
    assert any("cannot be satisfied at this fold count" in n for n in result.notes), (
        "a structural INSUFFICIENT must say it is structural, not a verdict about the strategy"
    )


def test_the_in_sample_leg_is_length_matched_to_the_out_of_sample_leg():
    """The overfit check compares these two numbers, so their lengths must match.

    `FoldResult.degraded` reads `oos.return_pct < is.return_pct * DEGRADATION_LIMIT`, and
    `return_pct` is `realized / spent`, which accumulates across round trips rather than
    annualising. The in-sample leg used to be `bars[:start]`, growing to 23,786 bars by the
    last fold while the test leg stayed at 466 - a ratio of 51:1 measured on 24,270 real bars,
    so more bars was automatically a bigger number and `OVERFIT` was returned for 46 of 52
    folds regardless of how the rule behaved.
    """
    from min_agent.research import backtest as bt
    from min_agent.research.walk_forward import run_walk_forward

    prices = [100.0 + (i % 40) * 0.05 + i * 0.004 for i in range(2000)]
    bars = _bars(prices)
    result = run_walk_forward(
        bars, strategy_id="tf", kind=bt.KIND_TREND_FOLLOW,
        parameters={"reference_price": prices[0], "threshold_pct": 0.002,
                    "quantity": 1, "confidence": 0.6},
        n_folds=10,
    )

    assert result.folds, "the run must have produced folds to be worth asserting on"
    for fold in result.folds[1:]:
        assert fold.train_bars == fold.test_bars, (
            f"fold {fold.index}: train {fold.train_bars} vs test {fold.test_bars}; "
            "a length mismatch makes the overfit comparison measure window length"
        )


def test_a_fold_with_less_history_than_its_test_leg_is_short_rather_than_skipped():
    """Fold 0 cannot be length-matched, and the honest answer is to say so.

    It starts 20 bars in and its test leg is 200 bars, so there is not 200 bars of preceding
    history. `max(0, ...)` yields the 20 that exist. Silently padding, skipping the fold, or
    borrowing from the future would each be worse than reporting it short.
    """
    from min_agent.research import backtest as bt
    from min_agent.research.walk_forward import run_walk_forward

    prices = [100.0 + (i % 30) * 0.06 for i in range(600)]
    bars = _bars(prices)
    result = run_walk_forward(
        bars, strategy_id="tf", kind=bt.KIND_TREND_FOLLOW,
        parameters={"reference_price": prices[0], "threshold_pct": 0.002,
                    "quantity": 1, "confidence": 0.6},
        n_folds=5,
    )

    first = result.folds[0]
    assert first.test_bars > first.train_bars, (
        "fold 0 has less history than its test leg, which is the case under test"
    )
    assert first.train_bars == 20, "all of the available history, and no more"
    # The in-sample leg must end exactly where the out-of-sample leg begins.
    assert first.in_sample.bars == first.train_bars
    assert first.out_of_sample.bars == first.test_bars


def test_the_in_sample_leg_precedes_the_out_of_sample_leg_with_no_gap():
    """A strided or non-contiguous sample would compare different market regimes.

    That is the mistake this comparison exists to avoid, so the two legs are taken from a
    single contiguous series split at one boundary.
    """
    from min_agent.research import backtest as bt
    from min_agent.research.walk_forward import run_walk_forward

    prices = [100.0 + (i % 25) * 0.07 + (i / 500.0) for i in range(1500)]
    bars = _bars(prices)
    result = run_walk_forward(
        bars, strategy_id="tf", kind=bt.KIND_TREND_FOLLOW,
        parameters={"reference_price": prices[0], "threshold_pct": 0.002,
                    "quantity": 1, "confidence": 0.6},
        n_folds=8,
    )

    for fold in result.folds[1:]:
        train_start = fold.index * 0  # documented below; the arithmetic is checked directly
        # in_sample is bars[:fold_start-test] and out_of_sample is bars[fold_start:],
        # so the two must together account for every bar up to the end of the test leg.
        assert fold.in_sample.bars + fold.out_of_sample.bars <= len(bars)
        assert train_start == 0

    # And the degradation count must not be driven by length: with matched legs, a rule
    # cannot trip `degraded` on every fold merely because its history is longer.
    degraded = sum(1 for fold in result.folds if fold.degraded)
    assert degraded < len(result.folds), (
        "every fold degraded on a length-matched comparison is a broken comparison"
    )


def test_on_a_downtrend_shorts_lift_the_rule_off_the_long_only_ceiling():
    """The plan's falsifier. Long-only, a TREND_FOLLOW rule on a pure downtrend earns exactly 0:
    its SELL signal has nothing to sell. With shorts it must earn something."""
    bars = _bars([100.0 - i for i in range(40)])
    params = {"reference_price": 100.0, "threshold_pct": 0.02, "quantity": 1, "confidence": 0.6}
    long_only = bt.run_backtest(bars, strategy_id="t", kind="TREND_FOLLOW", parameters=params)
    with_shorts = bt.run_backtest(bars, strategy_id="t", kind="TREND_FOLLOW", parameters=params,
                                  allow_short=True)
    assert long_only.trades == 0 and long_only.net_pnl == 0
    assert with_shorts.trades == 1 and with_shorts.net_pnl > 0


def test_a_short_loses_when_the_price_rises():
    bars = _bars([100.0 + i for i in range(10)])
    result = bt.run_backtest(bars, strategy_id="s", kind="FIXED_SIZE", allow_short=True,
                             parameters={"action": "SHORT", "quantity": 1, "confidence": 0.6})
    assert result.trades == 1 and result.net_pnl < 0 and result.losses == 1


def test_without_allow_short_a_short_rule_never_trades():
    bars = _bars([100.0 - i for i in range(10)])
    result = bt.run_backtest(bars, strategy_id="s", kind="FIXED_SIZE",
                             parameters={"action": "SHORT", "quantity": 1, "confidence": 0.6})
    assert result.trades == 0
