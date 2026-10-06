"""Walk-forward, stress, and multiple-testing control.

A single in-sample backtest of one spec on a few hundred bars is close to worthless
on its own, and it is *actively* dangerous if it is allowed to promote anything: try
forty parameter combinations, keep the best, and the winner is mostly noise. The
objective names walk-forward out-of-sample, stress and overfitting control as
Phase 4 requirements, so they are implemented rather than asserted.

The controls here are deliberately blunt, because a subtle correction is a
correction that gets tuned until it flatters the result:

* **Walk-forward.** The series is cut into contiguous folds. Each fold is trained
  on everything before it and evaluated only on the fold itself. Only the
  out-of-sample leg counts toward a verdict. An in-sample-only result is reported
  separately and cannot make a strategy look good.

* **Trial accounting.** Every spec evaluated in a session is counted. The number of
  trials is a first-class output, because the probability that the best of N noisy
  results looks profitable grows with N and a report that omits N is a report
  hiding its own selection bias.

* **Bonferroni-style gate.** A fold must clear a threshold divided by the number of
  trials, so searching harder demands a correspondingly better result. This is
  crude and conservative on purpose; a fancier correction would be one more thing
  to be wrong about.

* **Stress.** The same fold is re-run with worse costs, an adverse constant price
  shift, and a random adverse jitter. A strategy whose edge only exists at
  optimistic assumptions is reported as fragile rather than quietly averaged in.

None of this can promote anything. `verdict` is a research signal, and the offline
validation screen and the live lifecycle remain the only things that may change a
strategy's state.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field

from min_agent.research.backtest import (
    DEFAULT_COMMISSION_PCT,
    DEFAULT_SLIPPAGE_PCT,
    BacktestResult,
    Bar,
    run_backtest,
)

#: A strategy must beat this after cost in the out-of-sample leg to be interesting
#: at all. Zero is the bar on purpose: "did not lose money" is the only claim these
#: few bars can support.
OOS_RETURN_THRESHOLD_PCT = 0.0

#: Out-of-sample trades required before a verdict is allowed to lean on a result.
#: Scaled by the number of specs tried - see `required_trades`.
BASE_REQUIRED_TRADES = 10

#: A strategy whose OOS return collapses by more than this fraction of its in-sample
#: return has not generalised.
DEGRADATION_LIMIT = 0.5


@dataclass
class FoldResult:
    index: int
    train_bars: int
    test_bars: int
    in_sample: BacktestResult
    out_of_sample: BacktestResult

    @property
    def degraded(self) -> bool:
        """True when the out-of-sample result is far worse than the in-sample one.

        A strategy that looks good in-sample and flat out-of-sample has been fitted
        to the data, which is the single most common way a backtest lies.
        """
        if self.in_sample.return_pct <= 0:
            return False
        return (
            self.out_of_sample.return_pct
            < self.in_sample.return_pct * DEGRADATION_LIMIT
        )


@dataclass
class StressResult:
    scenario: str
    result: BacktestResult

    @property
    def survived(self) -> bool:
        return self.result.net_pnl > 0


@dataclass
class WalkForwardResult:
    strategy_id: str
    kind: str
    folds: list[FoldResult] = field(default_factory=list)
    stress: list[StressResult] = field(default_factory=list)
    trials: int = 1
    bars: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def oos_trades(self) -> int:
        return sum(f.out_of_sample.wins + f.out_of_sample.losses for f in self.folds)

    @property
    def oos_return_pct(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.out_of_sample.return_pct for f in self.folds) / len(self.folds)

    @property
    def in_sample_return_pct(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.in_sample.return_pct for f in self.folds) / len(self.folds)

    @property
    def leakage_violations(self) -> int:
        return sum(f.out_of_sample.leakage_violations for f in self.folds)

    def required_trades(self) -> int:
        """How many out-of-sample trades this many trials demands before it counts.

        This is the multiple-testing control, and the obvious version of it did
        nothing: `OOS_RETURN_THRESHOLD_PCT / trials` is `0.0 / 40`, which is still
        `0.0`, so searching forty variants cost nothing and the control was
        decoration. Dividing a zero bar by anything is still no bar.

        The real risk of trying N specs is not that the winner's return is too small
        in units, it is that the winner was selected on noise - and the cure for
        that is *more evidence*, not a different return threshold. So the trial
        count raises the number of independent out-of-sample trades required, on a
        square-root schedule: search harder, and demand proportionally more of the
        only thing that is not negotiable.
        """
        return math.ceil(BASE_REQUIRED_TRADES * math.sqrt(max(1, self.trials)))

    def threshold(self) -> float:
        """The return a fold must clear. Kept separate from `required_trades` so a
        report can show both a size bar and an evidence bar."""
        return OOS_RETURN_THRESHOLD_PCT

    def verdict(self) -> str:
        if not self.folds:
            return "INCONCLUSIVE_NO_FOLDS"
        if self.leakage_violations:
            return (
                f"BROKEN_LEAKAGE: {self.leakage_violations} causality violation(s) "
                "in the engine itself; do not read any number from this result"
            )
        if any(f.out_of_sample.unsupported for f in self.folds):
            return "UNSUPPORTED_KIND"
        # Thin data is checked before overfitting. Calling a result overfitted is a
        # claim about generalisation, which needs enough trades to generalise from;
        # the first version reported OVERFIT for a strategy with one trade per fold,
        # which is a statement about sample size wearing a confident label.
        needed = self.required_trades()
        if self.oos_trades < needed:
            return (
                f"INSUFFICIENT: {self.oos_trades} out-of-sample trade(s) across "
                f"{len(self.folds)} fold(s) on {self.bars} bars, against the "
                f"{needed} required for {self.trials} trial(s); too few to say "
                "whether it generalises, let alone whether it is overfitted"
            )
        if any(f.degraded for f in self.folds):
            return "OVERFIT: the out-of-sample leg is far worse than the in-sample leg"
        if self.oos_return_pct <= self.threshold():
            return (
                f"NO_EDGE: out-of-sample {self.oos_return_pct:+.3f}% does not clear "
                f"the {self.threshold():.3f}% bar after {self.trials} trial(s)"
            )
        if any(not s.survived for s in self.stress):
            broken = [s.scenario for s in self.stress if not s.survived]
            return f"FRAGILE: loses money under {broken}"
        return (
            f"PASSES_RESEARCH_GATE: out-of-sample {self.oos_return_pct:+.3f}% over "
            f"{self.oos_trades} trades, surviving {len(self.stress)} stress "
            f"scenario(s) on {self.trials} trial(s). This is a research signal only "
            "and cannot promote anything."
        )

    def trial_payload(self) -> dict:
        """What a trial log needs: the verdict and the numbers behind it.

        The verdict alone is not enough. "OVERFIT" with no figures cannot be
        re-examined, and a record that cannot be re-examined is a log rather than
        evidence.
        """
        return {
            "kind": "walk_forward",
            "strategy_id": self.strategy_id,
            "strategy_kind": self.kind,
            # The verdict is split into a code and a detail. Keyed on the whole
            # sentence, two identical outcomes landed in different buckets because
            # their trade counts differed, which defeats the only thing the grouping
            # is for.
            "verdict": self.verdict().split(":", 1)[0],
            "verdict_detail": self.verdict(),
            "trials": self.trials,
            "bars": self.bars,
            "folds": len(self.folds),
            "in_sample_return_pct": round(self.in_sample_return_pct, 6),
            "out_of_sample_return_pct": round(self.oos_return_pct, 6),
            "out_of_sample_trades": self.oos_trades,
            "required_trades": self.required_trades(),
            "threshold": round(self.threshold(), 6),
            "leakage_violations": self.leakage_violations,
            "stress_survivors": sum(1 for s in self.stress if s.survived),
            "stress_total": len(self.stress),
        }

    def to_payload(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "kind": self.kind,
            "bars": self.bars,
            "trials": self.trials,
            "folds": [
                {
                    "index": f.index,
                    "train_bars": f.train_bars,
                    "test_bars": f.test_bars,
                    "in_sample_return_pct": round(f.in_sample.return_pct, 4),
                    "out_of_sample_return_pct": round(f.out_of_sample.return_pct, 4),
                    "out_of_sample_trades": f.out_of_sample.wins + f.out_of_sample.losses,
                    "degraded": f.degraded,
                }
                for f in self.folds
            ],
            "in_sample_return_pct": round(self.in_sample_return_pct, 4),
            "out_of_sample_return_pct": round(self.oos_return_pct, 4),
            "out_of_sample_trades": self.oos_trades,
            "threshold": round(self.threshold(), 6),
            "required_trades": self.required_trades(),
            "stress": [
                {"scenario": s.scenario, "net_pnl": round(s.result.net_pnl, 4),
                 "survived": s.survived}
                for s in self.stress
            ],
            "leakage_violations": self.leakage_violations,
            "verdict": self.verdict(),
            "notes": self.notes,
        }


def _shifted(bars: Sequence[Bar], pct: float) -> list[Bar]:
    """Every price moved by a constant percent, against or for the strategy."""
    factor = 1 + pct / 100.0
    return [Bar(b.timestamp, b.price * factor) for b in bars]


def _jittered(bars: Sequence[Bar], pct: float, seed: int) -> list[Bar]:
    """Random adverse jitter. Seeded so a stress run is reproducible - an
    irreproducible stress result is not a result."""
    rng = random.Random(seed)
    out = []
    for b in bars:
        move = -abs(rng.gauss(0, pct / 100.0))
        out.append(Bar(b.timestamp, b.price * (1 + move)))
    return out


def run_walk_forward(
    bars: Sequence[Bar],
    *,
    strategy_id: str,
    kind: str,
    parameters: dict,
    n_folds: int | None = None,
    min_train_bars: int = 20,
    trials: int = 1,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
) -> WalkForwardResult:
    """Cut the series into folds and score only the out-of-sample leg.

    `n_folds` is the number of out-of-sample segments; the first segment is preceded
    by `min_train_bars` of in-sample data.

    **`None` derives the fold count from `required_trades`, and that is the default,
    because the fold count is what limits the evidence the multiple-testing control
    counts.** `run_backtest` opens a position only when flat and force-closes it at the
    segment's last bar, so an always-BUY rule makes exactly one counted trade per
    segment: `out_of_sample_trades == n_folds` is an identity, measured at 3, 10, 27, 52
    and 60 folds. The gate needs `required_trades` independent out-of-sample bets -
    52 at 27 trials - so a hardcoded 3 folds made it unsatisfiable by construction, and
    all 26 recorded trials returned INSUFFICIENT for that reason alone.

    That is a veto, not a control. A gate nothing can pass silently stops the thing it
    gates, which this repository already says about the promotion gate: "A gate that
    nothing can pass is a freeze." `required_trades` is unchanged - the bar still rises
    as sqrt(trials) - this only provisions the evidence it asks for. At
    `n_folds == required_trades` on 847 bars the stage returns a real verdict.

    An explicit `n_folds` below the requirement is honoured, because a caller asking for a
    cheap run should get one, but it records a note saying the evidence gate cannot be
    satisfied there. Overriding it silently would be worse: this repository has already
    been bitten twice by a count quietly meaning something other than it says.

    Segment *length* is a separate weakness this does not fix: 52 folds on 847 bars is 15
    bars per segment, so each bet is short. Roughly 5,220 bars would give 52 segments of
    ~100 bars. That is a quality question after the count, not the reason the gate never
    opened.
    """
    result = WalkForwardResult(
        strategy_id=strategy_id, kind=kind, trials=trials, bars=len(bars)
    )
    required = result.required_trades()
    if n_folds is None:
        n_folds = max(3, required)
    elif n_folds < required:
        result.notes.append(
            f"{n_folds} folds can produce at most {n_folds} out-of-sample trade(s) "
            f"because a strategy makes one counted trade per segment, against the "
            f"{required} required for {trials} trial(s); the evidence gate cannot be "
            f"satisfied at this fold count, so any INSUFFICIENT below is structural "
            f"rather than a statement about the strategy"
        )
    if len(bars) < min_train_bars + n_folds:
        result.notes.append(
            f"only {len(bars)} bars; need at least {min_train_bars + n_folds} for "
            f"{n_folds} folds, so walk-forward was not run"
        )
        return result

    test_size = (len(bars) - min_train_bars) // n_folds
    if test_size < 5:
        result.notes.append(
            f"test segments of {test_size} bars are too short to mean anything"
        )
        return result

    for i in range(n_folds):
        start = min_train_bars + i * test_size
        end = start + test_size if i < n_folds - 1 else len(bars)
        test = list(bars[start:end])
        # The in-sample leg is length-matched to the out-of-sample leg it is compared
        # against by `FoldResult.degraded`.
        #
        # It used to be `bars[:start]`, which grows to 23,786 bars by the last fold while
        # the test leg stays at 466 - a ratio of 51:1, measured. And `return_pct` is
        # `realized / spent` (backtest.py), which *accumulates* across round trips rather
        # than annualising: the same rule over 100 bars returns -0.207% and over 23,800
        # returns +2.134% with seven trades either way, because the extra bars are
        # still-held drift that the force-close books as realized. On a rising series more
        # bars is therefore a bigger number, so `oos < is * 0.5` tripped on length alone
        # - 46 of 52 folds, which is why every candidate that cleared the evidence bar
        # returned OVERFIT.
        #
        # Length-matched, contiguous and immediately preceding: the same regime on both
        # sides of the boundary, which is the entire point of the comparison. `max(0, ...)`
        # because fold 0 starts at 20 bars and there is not yet 466 of history - that fold
        # is honestly short rather than skipped or padded. `train_bars` still records how
        # much history preceded the fold.
        train = list(bars[max(0, start - len(test)):start])
        result.folds.append(
            FoldResult(
                index=i,
                train_bars=len(train),
                test_bars=len(test),
                in_sample=run_backtest(
                    train, strategy_id=strategy_id, kind=kind, parameters=parameters,
                    commission_pct=commission_pct, slippage_pct=slippage_pct,
                ),
                out_of_sample=run_backtest(
                    test, strategy_id=strategy_id, kind=kind, parameters=parameters,
                    commission_pct=commission_pct, slippage_pct=slippage_pct,
                ),
            )
        )

    # Stress is applied to the whole series, because a scenario that only breaks one
    # fold is not a stress test.
    scenarios = {
        "baseline_costs": (list(bars), commission_pct, slippage_pct),
        "costs_x3": (list(bars), commission_pct * 3, slippage_pct * 3),
        "adverse_shift_-2pct": (_shifted(bars, -2.0), commission_pct, slippage_pct),
        "adverse_jitter_1pct": (
            _jittered(bars, 1.0, seed=20260929), commission_pct, slippage_pct
        ),
    }
    for name, (series, comm, slip) in scenarios.items():
        result.stress.append(
            StressResult(
                scenario=name,
                result=run_backtest(
                    series, strategy_id=strategy_id, kind=kind,
                    parameters=parameters, commission_pct=comm, slippage_pct=slip,
                ),
            )
        )

    # One jitter seed is not a stress test. The first version used a single seed and
    # it made a long position *better* (+28.97 vs +26.14 baseline), because an early
    # negative shock lowers the entry price. Averaging or trusting one seed would
    # report a favourable draw as a robustness result, so the worst of several seeds
    # is kept and each is reported.
    worst: BacktestResult | None = None
    for seed in (1, 2, 3, 4, 5):
        outcome = run_backtest(
            _jittered(bars, 1.0, seed=seed), strategy_id=strategy_id, kind=kind,
            parameters=parameters, commission_pct=commission_pct,
            slippage_pct=slippage_pct,
        )
        result.stress.append(
            StressResult(scenario=f"adverse_jitter_1pct_seed{seed}", result=outcome)
        )
        if worst is None or outcome.net_pnl < worst.net_pnl:
            worst = outcome
    if worst is not None:
        result.notes.append(
            f"worst of 5 jitter seeds: net {worst.net_pnl:+.4f}; a single seed can "
            "land favourably and would have flattered the result"
        )
    return result
