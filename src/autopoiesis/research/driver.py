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
reports `trials_run` because the sample-size heuristic depends on cumulative attempts, and the failure this exists to prevent is keeping the best of forty variants and reporting it
as though it were the only candidate. So `trials` is passed as the **whole search's** candidate
count, which makes `required_trades` rise as the search grows. A bigger search raises its own
evidence bar; it cannot buy a cheaper verdict by trying harder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from autopoiesis.regime import Bar
from autopoiesis.research import backtest as _backtest
from autopoiesis.research import prereg
from autopoiesis.research.trials import (
    read_trials,
    record_trial,
    summarise,
    trial_session,
    trials_path,
)
from autopoiesis.research.walk_forward import BASE_REQUIRED_TRADES, WalkForwardResult, run_walk_forward

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

    One TREND_FOLLOW per threshold and one FIXED_SIZE buy-hold. No rule is added here: the
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
    evaluation_identity: str | None = None,
) -> bool:
    """Whether this exact evaluation is already on the ledger.

    Identity includes rule, tape, symbol, contract, actual evaluator bytes and settings.
    Re-running an unchanged evaluation reuses its verdict, and recording it again would inflate `trials_run`, the denominator that sets
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
    return _matching_trial(recorded, spec, bars, symbol, fingerprint, evaluation_identity) is not None


def _matching_trial(
    recorded: list[dict[str, Any]],
    spec: dict[str, Any],
    bars: int,
    symbol: str | None,
    fingerprint: str,
    evaluation_identity: str | None = None,
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
            and row.get("evaluation_identity") == evaluation_identity
        ):
            if evaluation_identity is not None:
                _validate_cached_result(row)
            return row
    return None


def _validate_cached_result(row: dict[str, Any]) -> None:
    """A content match does not make a malformed stored outcome usable evidence."""
    verdicts = {"INCONCLUSIVE_NO_FOLDS", "BROKEN_LEAKAGE", "UNSUPPORTED_KIND",
                "INSUFFICIENT", "OVERFIT", "NO_EDGE", "FRAGILE", "PASSES_RESEARCH_GATE"}
    if row.get("verdict") not in verdicts:
        raise ValueError("corrupt cached trial: unsupported verdict")
    for key in ("in_sample_return_pct", "out_of_sample_return_pct", "threshold"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"corrupt cached trial: invalid {key}")
    for key in ("trials", "bars", "folds", "out_of_sample_trades", "required_trades",
                "leakage_violations", "stress_survivors", "stress_total"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"corrupt cached trial: invalid {key}")
    if (row["trials"] < 1 or row["bars"] < 1
            or row["stress_survivors"] > row["stress_total"]):
        raise ValueError("corrupt cached trial: inconsistent counts")
    if row["verdict"] == "PASSES_RESEARCH_GATE" and (
        row["out_of_sample_trades"] < row["required_trades"]
        or row["required_trades"] < math.ceil(BASE_REQUIRED_TRADES * math.sqrt(row["trials"]))
        or row["out_of_sample_return_pct"] <= row["threshold"]
        or row["leakage_violations"] != 0 or row["folds"] == 0
        or row["stress_total"] == 0 or row["stress_survivors"] != row["stress_total"]
    ):
        raise ValueError("corrupt cached trial: passing verdict contradicts result")


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
    min_train_bars: int = 20,
    commission_pct: float = _backtest.DEFAULT_COMMISSION_PCT,
    slippage_pct: float = _backtest.DEFAULT_SLIPPAGE_PCT,
    evaluation_identity: str | None = None,
    contract: dict[str, Any] | None = None,
    registration: dict[str, Any] | None = None,
    preregistration_scope: str | None = None,
    evaluator_sha256: str | None = None,
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

    Exact repeats reuse stored verdicts. This public function is for explicitly labelled
    diagnostics and unit tests; the CLI uses the mandatory registered prepare gate.

    A trial that cannot be journalled is still evaluated and still reported - `record_trial`
    returns a bool instead of raising - but the count of unrecorded trials is reported so a search
    whose record silently failed cannot be mistaken for a search that ran.
    """
    if not source.strip():
        raise ValueError("a non-empty data source label is required for every research trial")
    fingerprint = dataset_fingerprint(bars)
    if evaluation_identity is None:
        evaluation_identity = hashlib.sha256(json.dumps({"mode": "diagnostic",
            "evaluator_sha256": evaluator_digest(), "n_folds": n_folds,
            "min_train_bars": min_train_bars, "commission_pct": commission_pct,
            "slippage_pct": slippage_pct}, sort_keys=True).encode()).hexdigest()
    space = candidates(bars, thresholds=thresholds)
    recorded = read_trials(path, strict=True)
    already = len(recorded)
    # Partition before evaluating, because the trial count decides the verdict and the verdict
    # cannot be known until the count is fixed. Counting repeats here is what made a no-op run
    # raise the bar: `already + len(space)` went from 2 to 4 on a second identical run and moved
    # `required_trades` from 15 to 20 without a single new candidate being tried.
    matches = {
        spec["strategy_id"]: _matching_trial(recorded, spec, len(bars), symbol, fingerprint, evaluation_identity)
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
        cached = matches[spec["strategy_id"]]
        if cached is not None:
            rows.append(cached)
            continue
        result: WalkForwardResult = run_walk_forward(
            bars,
            strategy_id=spec["strategy_id"],
            kind=spec["kind"],
            parameters=spec["parameters"],
            n_folds=n_folds,
            trials=trials,
            min_train_bars=min_train_bars, commission_pct=commission_pct, slippage_pct=slippage_pct,
        )
        payload = {
            "search_id": f"driver-{stamp.date().isoformat()}",
            "parameters": spec["parameters"],
            "source": source.strip(),
            "evaluation_identity": evaluation_identity,
            "evaluator_sha256": evaluator_sha256,
            "contract": contract,
            "registration": registration,
            "preregistration_scope": preregistration_scope,
            "evaluation_settings": {"n_folds": n_folds, "min_train_bars": min_train_bars,
                                    "commission_pct": commission_pct, "slippage_pct": slippage_pct},
            "evidence_mode": "registered_integrity_diagnostic" if contract else "diagnostic",
            "symbol": symbol,
            # Which tape this verdict is about. Absent is not an option: a trial without it cannot
            # be told from another trial on different data with the same length, which is the
            # whole reason the field exists.
            "dataset_fingerprint": fingerprint,
            **result.trial_payload(),
        }
        rows.append(payload)
        if not record_trial(payload, path=path, now=stamp):
            unrecorded += 1
        else:
            recorded.append(payload)

    return {
        "searched_at": stamp.isoformat(),
        "bars": len(bars),
        "symbol": symbol,
        "dataset_fingerprint": fingerprint,
        "candidates": trials,
        "already_recorded": already,
        "repeats": repeats,
        "backtests_run": len(fresh),
        "unrecorded": unrecorded,
        "required_trades": max(row["required_trades"] for row in rows) if rows else 0,
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
    interval: str | None = None,
) -> tuple[list[Bar], str, list[str]]:
    """Load one real symbol/interval/window, refusing conflicts and invalid selected rows.

    Non-bar research caches are reported. Corrupt matching bar files fail closed. Interval
    selection precedes timestamp parsing; window selection precedes price checks and merging.
    """
    from autopoiesis.replay import ReplayBar

    symbol = (symbol or _default_symbol()).strip().upper()
    if not cache_dir.exists():
        raise ValueError(f"no replay cache at {cache_dir}; this driver never generates prices")
    merged: dict[datetime, Bar] = {}
    provenance: set[str] = set()
    ignored: list[str] = []
    used: list[str] = []
    bar_files = 0
    for path in sorted(cache_dir.glob("*.json")):
        filename_symbol = path.name.split("_", 1)[0].upper()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if filename_symbol == symbol:
                raise ValueError(f"corrupt bar file {path.name}: {exc}") from exc
            ignored.append(f"{path.name} (unreadable)")
            continue
        if not isinstance(payload, dict) or "bars" not in payload:
            if filename_symbol == symbol:
                raise ValueError(f"corrupt bar file {path.name}: no bars")
            ignored.append(f"{path.name} (no bars)")
            continue
        bar_files += 1
        file_symbol = str(payload.get("symbol", "")).strip().upper()
        if not file_symbol:
            raise ValueError(f"bar file {path.name} has no symbol")
        if file_symbol != symbol:
            ignored.append(f"{path.name} (symbol {file_symbol}, not {symbol})")
            continue
        filename_interval = path.name.split("_")[1] if "_" in path.name else None
        file_interval = payload.get("interval") or filename_interval
        if interval is not None and file_interval != interval:
            ignored.append(f"{path.name} (interval {file_interval}, not {interval})")
            continue
        if (payload.get("interval") and filename_interval
                and re.fullmatch(r"[1-9][0-9]*(?:Min|Hour|Day)", filename_interval)
                and payload["interval"] != filename_interval):
            raise ValueError(f"bar file {path.name} interval disagrees with filename")
        source = payload.get("provenance")
        if source != "alpaca historical bars; not synthetic":
            raise ValueError(f"bar file {path.name} has untrusted provenance {source!r}")
        if not isinstance(payload["bars"], list):
            raise ValueError(f"bar file {path.name} has invalid bars")
        selected: list[Bar] = []
        dropped = 0
        for index, raw in enumerate(payload["bars"]):
            try:
                if not isinstance(raw, dict):
                    raise ValueError("bar must be an object")
                timestamp = (prereg._parse_utc(raw["t"]) if isinstance(raw["t"], str)
                             else datetime.fromtimestamp(float(raw["t"]), timezone.utc))
                timestamp = timestamp.astimezone(timezone.utc)
                if window is not None and not window[0] <= timestamp <= window[1]:
                    dropped += 1
                    continue
                # Parsing all OHLC prevents malformed bars from being disguised as a close tape.
                row = ReplayBar.from_dict(raw)
                prices = (raw["o"], raw["h"], raw["l"], raw["c"])
                if any(isinstance(price, bool) or not isinstance(price, (int, float))
                       or not math.isfinite(price) or price <= 0 for price in prices):
                    raise ValueError("invalid OHLC price")
                if not row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high:
                    raise ValueError("inconsistent OHLC prices")
                if not math.isfinite(row.volume) or row.volume < 0:
                    raise ValueError("invalid volume")
                selected.append(Bar(timestamp, row.close))
            except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
                raise ValueError(f"invalid bar {index} in {path.name}: {exc}") from exc
        report = prereg.check_tape(selected)
        if not report.usable:
            raise ValueError(f"invalid tape in {path.name}: {report.render()}")
        if dropped:
            ignored.append(f"{path.name}: {dropped} bar(s) outside the declared window")
        for bar in selected:
            old = merged.get(bar.timestamp)
            if old is not None and old.price != bar.price:
                raise ValueError(f"conflicting overlapping price at {bar.timestamp} in {path.name}")
            merged[bar.timestamp] = bar
        if selected:
            provenance.add(source)
            used.append(f"{path.name} ({len(selected)})")
    if not bar_files:
        raise ValueError(f"no bar files in {cache_dir}")
    bars = [merged[key] for key in sorted(merged)]
    if not bars:
        raise ValueError(f"no bars for {symbol} interval {interval} in {cache_dir} "
                         f"inside the declared window; {', '.join(ignored)}")
    return (bars, f"{symbol}: {len(used)} file(s): {', '.join(used)} "
            f"[{'; '.join(sorted(provenance))}]", ignored)


@dataclass(frozen=True)
class PreparedResearch:
    bars: list[Bar]
    symbol: str
    source: str
    ignored: list[str]
    spec_digest: str
    evaluator_sha256: str
    evaluation_identity: str
    settings: dict[str, Any]
    contract: dict[str, str]
    registration: dict[str, Any]
    preregistration_scope: str


def execution_settings(spec: dict[str, str]) -> dict[str, Any]:
    """The existing evaluator supports this versioned grid, not freeform experiment prose."""
    problems = prereg.check_spec(spec)
    if problems:
        raise ValueError("invalid research contract: " + "; ".join(problems))
    allowed = set(prereg.REQUIRED_FIELDS) | {"interval", "thresholds", "candidate_budget",
        "evaluator", "n_folds", "min_train_bars", "commission_pct", "slippage_pct"}
    if set(spec) - allowed:
        raise ValueError(f"unsupported contract fields: {sorted(set(spec) - allowed)}")
    if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*", spec["instrument"]):
        raise ValueError("unsupported instrument: use an uppercase broker equity symbol")
    supported = {"rule": "threshold_grid_v1", "primary_metric": "oos_return_pct_after_cost_v1",
                 "evaluator": "walk_forward_v1"}
    for key, value in supported.items():
        if spec.get(key) != value:
            raise ValueError(f"unsupported {key}: {spec.get(key)!r}; supported: {value}")
    if not re.fullmatch(r"[1-9][0-9]*(?:Min|Hour|Day)", spec.get("interval", "")):
        raise ValueError("contract requires an explicit supported interval")
    try:
        thresholds = tuple(float(value.strip()) for value in spec["thresholds"].split(","))
        if (not thresholds or len(set(thresholds)) != len(thresholds)
                or any(not math.isfinite(value) or value <= 0 for value in thresholds)
                or len({f"{value:.4f}" for value in thresholds}) != len(thresholds)):
            raise ValueError("thresholds must be positive, finite, unique with distinct candidate IDs")
        settings: dict[str, Any] = {"thresholds": thresholds, "candidate_budget": int(spec["candidate_budget"]),
                    "n_folds": int(spec["n_folds"]), "min_train_bars": int(spec["min_train_bars"]),
                    "commission_pct": float(spec["commission_pct"]),
                    "slippage_pct": float(spec["slippage_pct"])}
        if settings["candidate_budget"] != len(thresholds) + 1:
            raise ValueError("candidate_budget must equal thresholds + one buy-hold baseline")
        if settings["n_folds"] < 1 or settings["min_train_bars"] < 2:
            raise ValueError("n_folds must be positive and min_train_bars at least 2")
        if any(not math.isfinite(settings[key]) or settings[key] < 0
               for key in ("commission_pct", "slippage_pct")):
            raise ValueError("costs must be finite non-negative percentages")
        if int(spec["expected_trades"]) < 1:
            raise ValueError("expected_trades intent must be positive")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid execution settings: {exc}") from exc
    return settings


def evaluator_digest() -> str:
    """Actual implementation bytes make evaluator changes invalidate repeat outcomes."""
    from autopoiesis.research import walk_forward

    digest = hashlib.sha256()
    for module in (sys.modules[__name__], walk_forward, _backtest,
                   sys.modules[_backtest.rule_signal.__module__],
                   sys.modules[Bar.__module__]):
        module_file = module.__file__
        if module_file is None:
            raise ValueError("evaluator implementation has no source identity")
        source_path = pathlib.Path(module_file)
        digest.update(source_path.name.encode())
        digest.update(source_path.read_bytes())
    return digest.hexdigest()


def registered_contract(spec_path: pathlib.Path, symbol: str | None = None,
                        registry: pathlib.Path | None = None) -> tuple[dict[str, str], dict[str, Any], str, dict]:
    """Verify frozen supported declaration bytes before loading or fetching their data."""
    spec, digest, registration = prereg.load_verified_spec(spec_path, registry)
    settings = execution_settings(spec)
    if symbol is not None and symbol.strip().upper() != spec["instrument"]:
        raise ValueError(f"symbol {symbol} differs from declared instrument {spec['instrument']}")
    return spec, settings, digest, registration


def prepare_research(cache_dir: pathlib.Path, symbol: str | None, spec_path: pathlib.Path,
                     registry: pathlib.Path | None = None) -> PreparedResearch:
    """Shared mandatory registration, execution contract and tape gate for both CLIs."""
    spec, settings, spec_digest, registration = registered_contract(spec_path, symbol, registry)
    symbol = spec["instrument"]

    window = (prereg._parse_utc(spec["window_start_utc"]), prereg._parse_utc(spec["window_end_utc"]))
    bars, source, ignored = _load_real_bars(cache_dir, symbol, window, spec["interval"])
    report = prereg.check_tape(bars, spec)
    if not report.usable:
        raise ValueError("unusable declared tape: " + report.render())
    if len(bars) < settings["min_train_bars"] + settings["n_folds"] * 5:
        raise ValueError("declared folds require at least five bars per out-of-sample segment")
    implementation = evaluator_digest()
    identity = hashlib.sha256(json.dumps({"spec_sha256": spec_digest,
        "dataset_fingerprint": dataset_fingerprint(bars),
        "evaluator_sha256": implementation, "settings": settings},
        sort_keys=True).encode()).hexdigest()
    scope = ("registered_before_window" if prereg._parse_utc(registration["registered_at"]) <= window[0]
             else "registered_after_window_start")
    return PreparedResearch(bars=bars, symbol=symbol, source=source, ignored=ignored,
                            spec_digest=spec_digest, evaluator_sha256=implementation,
                            evaluation_identity=identity, settings=settings, contract=spec,
                            registration=registration, preregistration_scope=scope)


def main(argv: list[str] | None = None) -> int:
    """An explicit registered supported spec is mandatory for every CLI search."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("cache", nargs="?", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("symbol", nargs="?", default=None)
    parser.add_argument("spec", nargs="?", default=None)
    parser.add_argument("--registry", type=pathlib.Path)
    parser.add_argument("--trials", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.spec is None:
        print("research refused: an explicit registered SPEC is required", file=sys.stderr)
        return 2
    try:
        spec_path = pathlib.Path(args.spec)
        prepared = prepare_research(pathlib.Path(args.cache), args.symbol, spec_path, args.registry)
        settings = prepared.settings
        with trial_session(args.trials):
            report = search(prepared.bars, symbol=prepared.symbol, source=prepared.source,
                            path=args.trials, thresholds=settings["thresholds"],
                            n_folds=settings["n_folds"], min_train_bars=settings["min_train_bars"],
                            commission_pct=settings["commission_pct"], slippage_pct=settings["slippage_pct"],
                            evaluation_identity=prepared.evaluation_identity,
                            evaluator_sha256=prepared.evaluator_sha256,
                            contract={"spec_sha256": prepared.spec_digest, **prepared.contract},
                            registration=prepared.registration,
                            preregistration_scope=prepared.preregistration_scope)
    except (OSError, ValueError) as exc:
        print(f"research refused: {exc}", file=sys.stderr)
        return 2
    print(f"bars: {report['bars']} from {prepared.source}")
    print(f"spec_sha256: {prepared.spec_digest}")
    print(f"registered_at: {prepared.registration['registered_at']}")
    print(f"preregistration_scope: {prepared.preregistration_scope}; integrity diagnostic only")
    print(f"evaluator_sha256: {prepared.evaluator_sha256}")
    print(f"evaluation_identity: {prepared.evaluation_identity}")
    print(f"dataset_fingerprint: {report['dataset_fingerprint']}")
    if prepared.ignored:
        print(f"ignored: {', '.join(prepared.ignored)}")
    print(f"candidates: {report['candidates']} (cumulative: {report['already_recorded']} already) "
          f"required_trades: {report['required_trades']} backtests_run: {report['backtests_run']} "
          f"repeats: {report['repeats']} unrecorded: {report['unrecorded']}")
    for row in report["results"]:
        print(f"  {row['strategy_id']:34s} {row['verdict']:18s} "
              f"oos {row['out_of_sample_return_pct']:+7.3f}% "
              f"over {row['out_of_sample_trades']:4d} trades")
    print(f"recorded in {trials_path(args.trials)}")
    if report["unrecorded"]:
        print("research failed: trial persistence incomplete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
