#!/usr/bin/env python3
"""Print the provenance of one run of autopoiesis.

Called by `make reproduce`. The objective asks for config, commit hash, environment,
seed, dataset version, output paths and core metrics to be recorded so any result can be
traced back to the code and configuration that produced it. Each value is read from live
state rather than typed in, so the record cannot drift from the run it describes.

This lives in a file rather than inline in the Makefile because a double-quoted recipe
cannot express a multi-line Python program: a trailing backslash inside `$(...)` is
consumed by make rather than continuing the shell line, and the failure is silent - the
recipe runs, prints nothing, and the field simply reads "unavailable". That happened, and
a missing field in a provenance record is worse than an obviously broken one.
"""
from __future__ import annotations

import ast
import collections
import glob
import json
import os
import pathlib
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "autopoiesis"


def _journal_path() -> str:
    """The journal path, resolved through AgentConfig rather than written out again.

    `MIN_AGENT_JOURNAL` is a documented setting; spelling the path out here made it a lie
    for this tool even though the agent honoured it.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from autopoiesis.config import AgentConfig
    return str(AgentConfig.from_env().journal_path)



def _run(*cmd: str) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=ROOT)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _broker_clock() -> str:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        return "unavailable (no credentials in the environment)"
    try:
        request = urllib.request.Request(
            "https://paper-api.alpaca.markets/v2/clock",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        return f"{data['timestamp']} is_open={data['is_open']}"
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def _as_dict(value):
    """A pydantic model or a literal-eval string, as a plain dict."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    result = _literal(value)
    return result if isinstance(result, dict) else {}


def _literal(value):
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value
    return value


def main() -> int:
    out: list[str] = []
    add = out.append

    add("# provenance for one run of autopoiesis")
    add(f"generated: {datetime.now(tz=timezone.utc):%Y-%m-%dT%H:%M:%SZ}")

    add("\n## code")
    add(f"commit: {_run('git', 'rev-parse', 'HEAD')}")
    dirty = _run("git", "diff", "--quiet")
    add(f"dirty:  {'no' if dirty == '0' or not dirty else 'yes'}")

    add("\n## environment")
    add(f"python: {sys.version.split()[0]}")
    add("packages:")
    try:
        import importlib.metadata as meta

        for name in ("alpaca-trade-api", "pydantic", "requests", "pytest", "ruff"):
            try:
                add(f"  {name}=={meta.version(name)}")
            except Exception:
                pass
    except Exception:
        pass

    add("\n## configuration in effect")
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from autopoiesis.config import AgentConfig

        config = AgentConfig.from_env()
        for label, value in (
            ("mode", config.mode),
            ("alpaca_base_url", config.alpaca_base_url),
            ("model", config.model),
            ("symbols", config.symbols),
            ("max_position_value", config.max_position_value),
            ("max_daily_loss", config.max_daily_loss),
            ("max_trades_per_day", config.max_trades_per_day),
            ("min_confidence", config.min_confidence),
            ("daemon_interval_s", config.daemon_interval_seconds),
        ):
            add(f"  {label:<19} {value}")
    except Exception as exc:
        add(f"  (unavailable: {type(exc).__name__})")

    add("\n## determinism")
    add("  seed:     none - the decision path has no seeded randomness. The only")
    add("            stochastic element is the model, which samples at a")
    add("            temperature and is not seeded anywhere in this codebase.")
    add("  dataset:  the live broker. It has no version number, so it is versioned")
    add("            by the point in time below.")
    add(f"  broker at: {_broker_clock()}")

    add("\n## output paths")
    for label, path in (
        ("journal", str(_journal_path())),
        ("strategies", "runtime/autopoiesis/strategies/"),
        ("reflection", "runtime/autopoiesis/reflection.json"),
        ("doctor history", "runtime/autopoiesis/doctor-history.jsonl"),
        ("this record", "runtime/autopoiesis/reproduce.txt"),
    ):
        add(f"  {label:<15} {path}")

    add("\n## metrics at the time of this record")
    try:
        # Through JsonlJournal, so rotated generations are included. Reading the live file
        # alone reported "journal unavailable: IndexError" after a rotation - `cycles[-1]`
        # on a file that held only events. A provenance record that silently omits its own
        # metrics is worse than one that reports none: it looks complete.
        sys.path.insert(0, str(ROOT / "src"))
        from autopoiesis.journal import JsonlJournal
        journal = JsonlJournal(Path(_journal_path()))
        cycles = journal.read_all()
        if not cycles:
            add("  (no cycle records yet)")
            cycles = None
        last = cycles[-1] if cycles else None
        if last is not None:
            # read_all returns CycleRecord models, not dicts, so the old subscript access
            # raised TypeError once the journal was read through JsonlJournal.
            snapshot = _as_dict(last.snapshot)
            decision = _as_dict(last.decision)
            account = snapshot.get("account") or {}
            add(f"  cycles recorded:    {len(cycles)}")
            add(f"  snapshot source:    {snapshot.get('source')}")
            add(f"  last action:        {decision.get('action')} {decision.get('symbol')} qty {decision.get('quantity')}")
            add(f"  account equity:     {round(float(account.get('equity', 0)), 2)}")
            add(f"  portfolio value:    {round(float(account.get('portfolio_value', 0)), 2)}")
            states = collections.Counter()
            for path in glob.glob(str(RUNTIME / "strategies" / "*.json")):
                states[json.loads(open(path).read()).get("lifecycle")] += 1
            add(f"  strategy lifecycle: {dict(states)}")
    except Exception as exc:
        add(f"  (journal unavailable: {type(exc).__name__})")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
