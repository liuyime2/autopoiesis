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
structural rather than a convention, so this module imports `autopoiesis.replay` for a read-only
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

import hashlib
import json
import pathlib
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from autopoiesis.regime import Bar
from autopoiesis.research import backtest as _backtest
from autopoiesis.research.trials import (
    DEFAULT_TRIALS_PATH,
    read_trials,
    record_trial,
    summarise,
)
from autopoiesis.research.walk_forward import WalkForwardResult, run_walk_forward

#: Thresholds swept by default, in fractions. The grid spans below the 0.1% that a 5-minute bar
#: typically moves to the 3.0% the live library is parameterised at, because the library's band
#: only fires on a multi-day move while the lower end trades often enough to be judged at all.
DEFAULT_THRESHOLDS: tuple[float, ...] = (
    0.0005, 0.001, 0.002, 0.005, 0.0075, 0.010, 0.015, 0.020, 0.025, 0.030,
)

#: Where a real bar cache lives. Deliberately the same directory `tools/fetch_replay_bars.py`
#: writes, so the driver consumes provenance the fetch tool recorded rather than inventing data.
DEFAULT_CACHE_DIR = pathlib.Path("runtime") / "autopoiesis" / "replay"


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


def _bar_price(bar) -> float:
    """The bar's price, under whichever name it carries it.

    `regime.Bar` spells it `price` and `replay.ReplayBar` spells it `close`; they are the same number
    to every consumer, and the driver receives either depending on whether it was called on a
    regime tape or on a fetched one. Reading only one spelling makes the fingerprint raise on the
    other, which is how this was found - the preregistration gate passed a real tape in and the
    fingerprint helper refused it.
    """
    value = getattr(bar, "price", None)
    if value is None:
        value = getattr(bar, "close", None)
    if value is None:
        raise ValueError(f"a bar with no price: {bar!r}")
    return float(value)


def dataset_fingerprint(bars: Sequence[Bar]) -> str:
    """Stable content identity for the ordered timestamp/price tape the evaluator consumes."""
    digest = hashlib.sha256(b"autopoiesis-bar-series-v1\n")
    for bar in bars:
        timestamp = bar.timestamp.isoformat(timespec="microseconds")
        price = _bar_price(bar).hex()
        digest.update(timestamp.encode("utf-8"))
        digest.update(b"\0")
        digest.update(price.encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _already_evaluated(
    recorded: list[dict[str, Any]],
    spec: dict[str, Any],
    bars: int,
    symbol: str | None,
    fingerprint: str,
) -> bool:
    """Whether this exact evaluation is already on the ledger.

    Identity is the rule, data fingerprint, and instrument, because all three determine the
    result. Re-running the sweep over an unchanged cache re-derives the same verdict for the
    same candidate, and recording it again would inflate `trials_run`, the denominator that sets
    `required_trades`. A static dataset must not raise its own evidence bar on a repeat run.

    The instrument is part of identity because it was not, for five days. Trials recorded while
    the merge interleaved seventeen symbols carry only a bar count that happens to be close to
    the count a single-symbol tape produces, so without this a verdict judged over a tape of
    nothing would be reused as a verdict over the real instrument. Re-evaluating them is the
    correct outcome, and the trial count rising with it is the direction the multiple-testing
    control wants: a bigger search raises its own bar.
    without any new candidate ever having been tried. A repeat is not a trial.

    Any changed ordered tape re-evaluates everything, even when its bar count is unchanged.
    """
    return _matching_trial(recorded, spec, bars, symbol, fingerprint) is not None


def _matching_trial(
    recorded: list[dict[str, Any]],
    spec: dict[str, Any],
    bars: int,
    symbol: str | None,
    fingerprint: str,
) -> dict[str, Any] | None:
    for row in recorded:
        if row.get("strategy_id") != spec["strategy_id"]:
            continue
        if row.get("kind") != spec["kind"] and row.get("strategy_kind") != spec["kind"]:
            continue
        if (
            row.get("bars") == bars
            and row.get("parameters") == spec["parameters"]
            and (row.get("symbol") or None) == symbol
            and row.get("dataset_fingerprint") == fingerprint
        ):
            return row
    return None


def search(
    bars: Sequence[Bar],
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    path: pathlib.Path | None = None,
    now: datetime | None = None,
    n_folds: int | None = None,
    symbol: str | None = None,
    source: str,
    fingerprint: str | None = None,
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
    if not source.strip():
        raise ValueError("a non-empty data source label is required for every research trial")
    fingerprint = dataset_fingerprint(bars)
    space = candidates(bars, thresholds=thresholds)
    recorded = read_trials(path)
    already = len(recorded)
    # Partition before evaluating, because the trial count decides the verdict and the verdict
    # cannot be known until the count is fixed. Counting repeats here is what made a no-op run
    # raise the bar: `already + len(space)` went from 2 to 4 on a second identical run and moved
    # `required_trades` from 15 to 20 without a single new candidate being tried.
    matches = {
        spec["strategy_id"]: _matching_trial(recorded, spec, len(bars), symbol, fingerprint)
        for spec in space
    }
    fresh = [spec for spec in space if matches[spec["strategy_id"]] is None]
    repeats = len(space) - len(fresh)
    trials = already + len(fresh)

    if not fresh:
        # The fetcher may run every day and correctly discover that the cache did not
        # change. Reusing the exact recorded outcomes avoids re-running every fold and
        # stress case merely to decide not to append a duplicate.
        cached_rows: list[dict[str, Any]] = []
        for spec in space:
            match = matches[spec["strategy_id"]]
            if match is None:
                raise RuntimeError("exact-repeat partition lost a matching trial")
            cached_rows.append(match)
        return {
            "searched_at": (now or datetime.now(timezone.utc)).isoformat(),
            "bars": len(bars),
            "symbol": symbol,
            # Which tape this verdict is about. Absent is not an option: a trial without it cannot
            # be told from another trial on different data with the same length, which is the
            # whole reason the field exists.
            "dataset_fingerprint": fingerprint,
            "candidates": already,
            "already_recorded": already,
            "repeats": repeats,
            "backtests_run": 0,
            "unrecorded": 0,
            "required_trades": cached_rows[0].get("required_trades", 0) if cached_rows else 0,
            "by_verdict": summarise(cached_rows),
            "results": cached_rows,
        }

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
            "source": source.strip(),
            "symbol": symbol,
            # Which tape this verdict is about. Absent is not an option: a trial without it cannot
            # be told from another trial on different data with the same length, which is the
            # whole reason the field exists.
            "dataset_fingerprint": fingerprint,
            **result.trial_payload(),
        }
        rows.append(payload)
        if _already_evaluated(recorded, spec, len(bars), symbol, fingerprint):
            continue
        recorded.append(payload)
        if not record_trial(payload, path=path, now=stamp):
            unrecorded += 1

    return {
        "searched_at": stamp.isoformat(),
        "bars": len(bars),
        "symbol": symbol,
        "dataset_fingerprint": fingerprint,
        "candidates": trials,
        "already_recorded": already,
        "repeats": repeats,
        "backtests_run": len(space),
        "unrecorded": unrecorded,
        "required_trades": rows[0]["required_trades"] if rows else 0,
        "by_verdict": summarise(rows),
        "results": rows,
    }


def _default_symbol() -> str:
    """The instrument the search is about, from the same config the daemon trades.

    The question this driver exists to answer is "does a threshold rule have an edge on what we
    trade". It is not "does one have an edge on a tape built by interleaving seventeen
    instruments" - that tape is not a price series of anything, so it answers neither question.
    """
    try:
        from autopoiesis.config import AgentConfig

        symbols = AgentConfig.from_env().symbols
        if symbols:
            return symbols[0]
    except Exception:
        pass
    return "SPY"


def _load_real_bars(
    cache_dir: pathlib.Path = DEFAULT_CACHE_DIR,
    symbol: str | None = None,
    window: tuple | None = None,
) -> tuple[list[Bar], str, list[str]]:
    """Real bars for **one symbol**, with the provenance it recorded.

    Every file for that symbol is merged and deduplicated by timestamp, because a rolling fetch
    names its file after the window it fetched - `SPY_5Min_2026-05-01_2026-10-03.json` - so a
    scheduled search accumulates one file per fetch and any single one of them is a partial view.
    Picking the largest file instead would silently prefer whichever window happened to be widest
    rather than whichever covers the most recent tape, and picking the newest would throw away
    everything before it.

    **One symbol, not the whole cache.** This used to merge every bar file it found and key the
    deduplication on the timestamp alone. That was right when the cache held five SPY files - the
    comment above still describes that world - and the cache now holds 17 symbols and 988,704
    rows. The timestamp-only key collapsed every instrument's five-minute bar at the same instant
    into whichever file was read first, so **852,937 of 988,704 bars were silently dropped** and
    the 135,767 that survived are not a series of any asset: they are fragments of seventeen
    different ones, interleaved. Every verdict recorded against that tape - all OVERFIT or
    INSUFFICIENT so far - is a measurement of a price series that never existed. A search over
    invented data can only show that code runs.

    So the merge is scoped to one symbol, deduplicated inside it, and every file left out is
    named with the reason. A cache holding another symbol is ordinary state - the fetch tool
    fetches what it is asked for - so it is reported rather than fatal.

    Fails loudly rather than falling back to a generator: `replay.py`'s rule is that a run over
    invented prices can only show that code runs, and the same is true of a search. An absent
    cache means the driver has nothing to decide, which is an error worth stopping for - not a
    reason to invent a series and report verdicts on it.
    """
    from autopoiesis.replay import bar_files, load_bars

    symbol = (symbol or _default_symbol()).strip().upper()
    if not cache_dir.exists():
        raise SystemExit(
            f"no replay cache at {cache_dir}; fetch real bars first with "
            "tools/fetch_replay_bars.py. This driver never generates prices."
        )

    # The bar-file predicate lives in `replay.bar_files`, because this is the second place that
    # needed it. `tools/fetch_replay_bars.py`'s `cached_last_bar` scanned the same directory the
    # same way, and both crashed on the research caches that sit beside the bars deliberately;
    # neither's exception tuple caught it, because `KeyError` is a `LookupError` and not a
    # `ValueError`. A third copy of the predicate is a third place for it to be wrong.
    #
    # They are reported and skipped, not skipped silently and not treated as fatal. A non-bar
    # file in the cache is an ordinary state - the research scripts write their caches beside
    # the bars on purpose - so exiting would permanently block a working search over 31 real bar
    # files because three caches sit next to them. What matters is that the run says which files
    # it ignored, so a cache that changed shape is visible rather than assumed.
    usable, ignored = bar_files(cache_dir)
    if not usable:
        raise SystemExit(f"no bar files in {cache_dir}; run tools/fetch_replay_bars.py first")

    by_timestamp: dict[Any, Bar] = {}
    provenance: set[str] = set()
    used: list[str] = []
    for path in usable:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ignored.append(f"{path.name} (unreadable)")
            continue
        file_symbol = str(payload.get("symbol") or path.name.split("_", 1)[0]).strip().upper()
        if file_symbol != symbol:
            ignored.append(f"{path.name} (symbol {file_symbol}, not {symbol})")
            continue
        rows = load_bars(path)
        if not rows:
            ignored.append(f"{path.name} (no bars)")
            continue
        provenance.add(str(payload.get("provenance", "unstated")))
        used.append(f"{path.name} ({len(rows)})")
        for row in rows:
            # Keyed on the timestamp only *within one symbol*, where it is the right key: one
            # symbol's windows genuinely overlap. Across symbols it is the wrong key, and it was
            # the whole of the defect described above.
            bar = Bar(timestamp=row.timestamp, price=row.close)
            by_timestamp.setdefault(row.timestamp, bar)

    bars = [by_timestamp[key] for key in sorted(by_timestamp)]
    if window is not None:
        # The tape consumed is the tape the spec declared, and nothing else.
        #
        # Found by the pre-registration gate on 2026-10-11: the merged SPY tape spans 2016 to 2026
        # - 3931 days, 134,425 bars - while the spec that recorded those trials declared six
        # months. 110,014 bars preceded the declared window and 50 followed it, so every verdict in
        # the ledger was a statement about data outside its own specification. The gate refused the
        # tape and the search did not start, which is the correct behaviour for a gate; this is the
        # other half, which makes the tape and the spec agree instead of merely detecting that they
        # do not.
        #
        # Filtering after the merge rather than before, because the merge is what makes each file's
        # overlapping windows harmless - and the window is a *claim about the series*, so it applies
        # to the merged series, not to whichever files happen to overlap it.
        start, end = window
        kept = [bar for bar in bars if start <= bar.timestamp <= end]
        dropped = len(bars) - len(kept)
        if dropped:
            ignored.append(f"{dropped} bar(s) outside the declared window "
                           f"{start.isoformat()} .. {end.isoformat()}")
        bars = kept
    if not bars:
        raise SystemExit(
            f"no bars for {symbol} in {cache_dir} inside the declared window; the cache holds: "
            f"{', '.join(ignored) or 'nothing'}. Fetch some with tools/fetch_replay_bars.py."
        )
    return (
        bars,
        f"{symbol}: {len(used)} file(s): {', '.join(used)} [{'; '.join(sorted(provenance))}]",
        ignored,
    )


def main(argv: list[str] | None = None) -> int:
    """`[cache_dir] [symbol] [spec]`. The symbol defaults to what the daemon trades.

    It is an argument because the cache holds more than one instrument, and because the answer to
    "which instrument am I searching" must be visible in the invocation rather than inferred
    from a directory listing.
    """
    argv = sys.argv[1:] if argv is None else argv
    cache_dir = pathlib.Path(argv[0]) if argv else DEFAULT_CACHE_DIR
    symbol = argv[1] if len(argv) > 1 else None
    spec_path = pathlib.Path(argv[2]) if len(argv) > 2 else None
    window = None
    if spec_path is not None:
        # The spec's declared window, applied to the tape. Passing it here rather than assuming the
        # cache is the window is what makes the run's data a property of the hypothesis rather than
        # of whatever happened to be on disk.
        from autopoiesis.research.prereg import _parse_utc, load_spec_from_text

        spec = load_spec_from_text(spec_path.read_text(encoding="utf-8"))
        if spec.get("window_start_utc") and spec.get("window_end_utc"):
            window = (_parse_utc(spec["window_start_utc"]), _parse_utc(spec["window_end_utc"]))
    bars, provenance, ignored = _load_real_bars(cache_dir, symbol, window)
    searched = symbol or _default_symbol()
    # The fingerprint of the tape actually consumed - after the window filter, because that is the
    # series the verdicts are about. Computing it here rather than inside `search` is what makes it
    # the fingerprint of what ran, not of what a caller happened to pass.
    #
    # It was not passed at all until now, which is why every row in the ledger reads
    # `dataset_fingerprint: null`: `search` has the parameter and the payload has the field, and the
    # one call site did not connect them. The deduplication this exists for was therefore falling
    # back to the bar count alone, which is exactly the weakness the fingerprint was added to fix.
    fingerprint = dataset_fingerprint(bars)
    report = search(bars, symbol=searched, source=provenance, fingerprint=fingerprint)
    summary = report["by_verdict"]
    print(f"bars: {report['bars']} from {provenance}")
    print(f"dataset_fingerprint: {report['dataset_fingerprint']}")
    if ignored:
        # Named, not counted silently, so a cache whose shape changed is visible in the log the
        # scheduled job writes rather than assumed away.
        label = "ignored (not this symbol's bar files)" if symbol else "ignored (not bar files)"
        print(f"{label}: {', '.join(ignored)}")
    print(
        f"candidates: {report['candidates']} "
        f"(cumulative: {report['already_recorded']} already in the ledger)  "
        f"required_trades: {report['required_trades']}  backtests_run: {report['backtests_run']}  "
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
