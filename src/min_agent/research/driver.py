"""The research stage, driven.

Every verdict this repository has ever reported was produced by a person running a script: the
research layer had no caller. Measured on the live record, candidate *generation* is autonomous
(382 `CURRICULUM_PROPOSED` events over months of operation) and candidate *evaluation* does not
exist - there is no `record_trial()` call anywhere outside this package. So the pipeline in
`__init__.py` stops after backtest, and the only way to learn whether a rule has an edge was to
type something.

This module is the other half: a search that runs without a human, an LLM, or a scheduler
operator, over real bars, recording every trial - pass or fail - with the figures behind it.

Two things it deliberately does not do.

**It does not write production state, and cannot reach it.** The separation in `__init__.py` is
structural rather than a convention, so this module imports `min_agent.replay` for a read-only
bar loader and nothing that writes: no journal, no Guardian, no executor, no strategy engine. A
backtest that can reach the thing it grades stops being evidence.

**It does not judge its own winner against a bar set for one trial.** `summarise()` already
reports `trials_run` because that number is the denominator the multiple-testing control divides
by, and the failure this exists to prevent is keeping the best of forty variants and reporting it
as though it were the only candidate. So `trials` is passed as the **whole search's** candidate
count, which makes `required_trades` rise as the search grows. A bigger search raises its own
evidence bar; it cannot buy a cheaper verdict by trying harder.
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from min_agent.regime import Bar
from min_agent.research import backtest as _backtest
from min_agent.research.trials import (
    DEFAULT_TRIALS_PATH,
    read_trials,
    record_trial,
    summarise,
)
from min_agent.research.walk_forward import WalkForwardResult, run_walk_forward

#: Thresholds swept by default, in fractions. The grid spans below the 0.1% that a 5-minute bar
#: typically moves to the 3.0% the live library is parameterised at, because the library's band
#: only fires on a multi-day move while the lower end trades often enough to be judged at all.
DEFAULT_THRESHOLDS: tuple[float, ...] = (
    0.0005, 0.001, 0.002, 0.005, 0.0075, 0.010, 0.015, 0.020, 0.025, 0.030,
)

#: Where a real bar cache lives. Deliberately the same directory `tools/fetch_replay_bars.py`
#: writes, so the driver consumes provenance the fetch tool recorded rather than inventing data.
DEFAULT_CACHE_DIR = pathlib.Path("runtime") / "min_agent" / "replay"


def candidates(
    bars: Sequence[Bar],
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    """The search space, which is the rule space that already exists.

    Two kinds, because `backtest.SUPPORTED_KINDS` has two that trade. No rule is added here: the
    measurement in `STATUS.md` showed `TREND_FOLLOW` already round-trips at 3.92 trades per fold
    over 1571-bar segments, so the sweep varies the one parameter that decides whether it acts at
    all. `reference_price` is set from the first bar because the rule re-anchors on it regardless,
    and leaving it unset would make the parameter set differ between candidates for no reason.
    """
    if not bars:
        raise ValueError("a search over zero bars would decide nothing")
    anchor = bars[0].price
    out: list[dict[str, Any]] = []
    for threshold in thresholds:
        out.append(
            {
                "strategy_id": f"search-trend-follow-{threshold:.4f}".replace(".", "p"),
                "kind": _backtest.KIND_TREND_FOLLOW,
                "parameters": {
                    "reference_price": anchor,
                    "threshold_pct": threshold,
                    "quantity": 1,
                    "confidence": 0.6,
                },
            }
        )
    out.append(
        {
            "strategy_id": "search-fixed-size-buy-hold",
            "kind": _backtest.KIND_FIXED_SIZE,
            "parameters": {"action": "BUY", "quantity": 1, "confidence": 0.6},
        }
    )
    return out


def _already_evaluated(
    recorded: list[dict[str, Any]], spec: dict[str, Any], bars: int
) -> bool:
    """Whether this exact evaluation is already on the ledger.

    Identity is the rule *and* the amount of data it was judged on, because those two together
    determine the result. Re-running the sweep over an unchanged cache re-derives the same verdict
    for the same candidate, and recording it again would inflate `trials_run` - which is the
    denominator `required_trades` divides by - so the evidence bar would climb on a static dataset
    without any new candidate ever having been tried. That is the gate in `tools/verify.py`
    correctly refusing 11 duplicate `strategy_id`s, and the fix belongs here rather than in the
    gate: a repeat is not a trial.

    New data re-evaluates everything, which is correct - the cache grows when it is refetched and
    a candidate judged over 20448 bars has genuinely not been judged over 30000.
    """
    for row in recorded:
        if row.get("strategy_id") != spec["strategy_id"]:
            continue
        if row.get("kind") != spec["kind"] and row.get("strategy_kind") != spec["kind"]:
            continue
        if row.get("bars") == bars and row.get("parameters") == spec["parameters"]:
            return True
    return False


def search(
    bars: Sequence[Bar],
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    path: pathlib.Path | None = None,
    now: datetime | None = None,
    n_folds: int | None = None,
) -> dict[str, Any]:
    """Run the whole search and record every trial.

    `trials` counts **every trial the ledger already holds, plus this search's new candidates**, so
    `required_trades` rises with the project's cumulative attempt count rather than resetting per
    run. The windowed version of this was wrong in a way worth recording: computing the bar from
    this search's 11 candidates alone gave `required_trades` 34, while the ledger held 37 trials
    and would hold 48 by the end of the run. A daily driver that reset its bar would then buy a
    cheap verdict every day and the project could shop across runs for a winner - the exact
    selection bias `trials.py` exists to prevent, arriving through the accounting rather than
    through the statistics. This is the same windowed-versus-cumulative defect that once let
    probation count 50 cycles of reflection when the journal held 1179.

    A candidate already judged on this much data is re-evaluated but not re-recorded, so the
    search is idempotent and the report says how many were repeats.

    A trial that cannot be journalled is still evaluated and still reported - `record_trial`
    returns a bool instead of raising - but the count of unrecorded trials is reported so a search
    whose record silently failed cannot be mistaken for a search that ran.
    """
    space = candidates(bars, thresholds=thresholds)
    recorded = read_trials(path)
    already = len(recorded)
    # Partition before evaluating, because the trial count decides the verdict and the verdict
    # cannot be known until the count is fixed. Counting repeats here is what made a no-op run
    # raise the bar: `already + len(space)` went from 2 to 4 on a second identical run and moved
    # `required_trades` from 15 to 20 without a single new candidate being tried.
    fresh = [s for s in space if not _already_evaluated(recorded, s, len(bars))]
    repeats = len(space) - len(fresh)
    trials = already + len(fresh)
    stamp = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    unrecorded = 0

    for spec in space:
        result: WalkForwardResult = run_walk_forward(
            bars,
            strategy_id=spec["strategy_id"],
            kind=spec["kind"],
            parameters=spec["parameters"],
            n_folds=n_folds,
            trials=trials,
        )
        payload = {
            "search_id": f"driver-{stamp.date().isoformat()}",
            "parameters": spec["parameters"],
            "source": "alpaca historical bars; not synthetic",
            **result.trial_payload(),
        }
        rows.append(payload)
        if _already_evaluated(recorded, spec, len(bars)):
            continue
        recorded.append(payload)
        if not record_trial(payload, path=path, now=stamp):
            unrecorded += 1

    return {
        "searched_at": stamp.isoformat(),
        "bars": len(bars),
        "candidates": trials,
        "already_recorded": already,
        "repeats": repeats,
        "unrecorded": unrecorded,
        "required_trades": rows[0]["required_trades"] if rows else 0,
        "by_verdict": summarise(rows),
        "results": rows,
    }


def _load_real_bars(cache_dir: pathlib.Path = DEFAULT_CACHE_DIR) -> tuple[list[Bar], str]:
    """Real bars from the fetch tool's cache, with the provenance it recorded.

    Fails loudly rather than falling back to a generator: `replay.py`'s rule is that a run over
    invented prices can only show that code runs, and the same is true of a search. An absent
    cache means the driver has nothing to decide, which is an error worth stopping for - not a
    reason to invent a series and report verdicts on it.
    """
    from min_agent.replay import load_bars

    if not cache_dir.exists():
        raise SystemExit(
            f"no replay cache at {cache_dir}; fetch real bars first with "
            "tools/fetch_replay_bars.py. This driver never generates prices."
        )
    files = sorted(cache_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"no bar files in {cache_dir}; run tools/fetch_replay_bars.py first")
    chosen = max(files, key=lambda p: p.stat().st_size)
    payload = json.loads(chosen.read_text(encoding="utf-8"))
    provenance = str(payload.get("provenance", "unstated"))
    bars = [Bar(timestamp=b.timestamp, price=b.close) for b in load_bars(chosen)]
    if not bars:
        raise SystemExit(f"{chosen} holds no usable bars")
    return bars, f"{chosen.name} ({provenance})"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cache_dir = pathlib.Path(argv[0]) if argv else DEFAULT_CACHE_DIR
    bars, provenance = _load_real_bars(cache_dir)
    report = search(bars)
    summary = report["by_verdict"]
    print(f"bars: {report['bars']} from {provenance}")
    print(
        f"candidates: {report['candidates']} "
        f"(cumulative: {report['already_recorded']} already in the ledger)  "
        f"required_trades: {report['required_trades']}  "
        f"repeats: {report['repeats']}  unrecorded: {report['unrecorded']}"
    )
    for row in report["results"]:
        print(
            f"  {row['strategy_id']:34s} {row['verdict']:18s} "
            f"oos {row['out_of_sample_return_pct']:+7.3f}% "
            f"over {row['out_of_sample_trades']:4d} trades"
        )
    print(f"trials_run: {summary['trials_run']}  passed: {summary['passed']}")
    print(f"recorded in {DEFAULT_TRIALS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
