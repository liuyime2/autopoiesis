#!/usr/bin/env python
"""Verify that each of the 15 recorded defects is actually fixed.

Run:  PYTHONPATH=src python tools/audit_defects.py
Exit: 0 when every check passes, 1 otherwise.

Each check is a property of the source, not a claim in a document. The
originals and their reproduction evidence are in
docs/history/plans/2026-09-28-quantgroup-recovery-and-refactor.md.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "autopoiesis"

results: list[tuple[str, str, bool]] = []


def check(defect: str, description: str, condition: bool) -> None:
    results.append((defect, description, bool(condition)))


def function_body(text: str, name: str) -> str:
    """Slice out one function, with its docstring removed.

    A naive text split also captures the rest of the file, and the docstrings in
    this codebase deliberately quote the old buggy code - so the comments that
    explain a fix would otherwise read as the bug still being present.
    """
    match = re.search(rf"def {re.escape(name)}\(.*?(?=\n    def |\nclass |\Z)", text, re.S)
    if not match:
        return ""
    body = match.group(0)
    return re.sub(r'("""|\'\'\').*?\1', "", body, flags=re.S)


def main() -> int:
    py = {p.name: p.read_text(encoding="utf-8") for p in SRC.glob("*.py") if p.is_file()}
    py = {k: v for k, v in py.items() if k != "__init__.py"}
    # The five shell wrappers and auto_reviewer.py were deleted: each duplicated CLI
    # flags or doctor's checks, and nothing scheduled them. `minictrl` is the only ops
    # script that remains, and the D8 properties are asserted over whatever is here.
    ops = {name: (ROOT / name).read_text(encoding="utf-8") for name in (
        "minictrl",
    ) if (ROOT / name).exists()}

    dg, g, ev, se = py["data_gateway.py"], py["guardian.py"], py["evaluator.py"], py["strategy_engine.py"]
    h, sc, lp = py["health.py"], py["scheduler.py"], py["loop.py"]
    llm, cu, cli = py["llm_decision.py"], py["curriculum.py"], py["cli.py"]
    jn, aio = py["journal.py"], py["atomicio.py"]
    fr, pe = py["fill_reconciler.py"], py["policy_engine.py"]
    sa, md = py["strategy_admission.py"], py["models.py"]

    # D1 - broker data is real or loudly absent
    check("D1", "gateway calls list_positions/list_orders", "list_positions()" in dg and "list_orders(" in dg)
    check("D1", "pre-3.2.0 method names are gone", "get_all_positions" not in dg and "get_orders(" not in dg)
    check("D1", "no degradation to an empty result", "except Exception: return ()" not in dg)
    check("D1", "BrokerDataUnavailable is raised", "raise BrokerDataUnavailable" in dg)
    check("D1", "lowercase broker side is normalised", ".upper()" in dg)

    # D2 - it can sell, and exposure is bounded
    check("D2", "aggregate exposure cap implemented", "max_total_exposure" in g)
    check("D2", "cli passes max_total_exposure", "max_total_exposure=config.max_total_exposure" in cli)
    check("D2", "unmeasured day-start equity fails closed", "day_start_equity_known" in g and "day_start_equity_known" in md)

    # D3/D4 - the HOLD latch
    check("D3", "score is gated by an exploration term",
          re.search(r"exploration\s*=\s*.*trade_attempts.*?/\s*cycles", ev, re.S) is not None)
    check("D3", "inaction is floored", re.search(r"INACTION_FLOOR\s*=", ev) is not None)
    check("D3", "system rejections are not charged to the strategy", "SYSTEM_REJECTION_REASONS" in ev)
    check("D3", "PnL reserves headroom rather than saturating",
          re.search(r"return score \* \(1\.0 - PNL_BONUS\)", ev) is not None)
    guard = function_body(se, "_is_degenerate_no_exploration")
    check("D4", "degenerate guard is not kind-gated", 'kind != "FIXED_SIZE"' not in guard)
    # The recorded defect was an early `return False` that let any non-probation
    # strategy escape the guard, so a promoted strategy that stopped acting was
    # never caught. This check used to forbid the literal "PROBATION" anywhere in
    # the guard, which also forbore naming the lifecycle to choose how many cycles
    # a strategy must be given before it can be called degenerate - and a candidate
    # on probation was then judged at min_active_cycles instead of the budget it was
    # guaranteed, which is how 24 of 24 admissions ended PAUSED without ever
    # reaching the screen's evidence gate. Forbid the escape, not the word.
    # Behaviour is pinned by the lifecycle tests, not by this text.
    check("D4", "degenerate guard does not exempt non-probation strategies",
          'lifecycle != "PROBATION"' not in guard)
    check("D4", "exploration floor exists", "_exploration_floor_engaged" in se)
    check("D4", "a probe is never a short", '== "BUY"' in se)

    # D5 - health is liveness
    check("D5", "pid liveness is probed", "os.kill" in h)
    check("D5", "staleness is measured", "def is_stale" in h)
    check("D5", "--status exits non-zero when not live", "is_live() else 1" in cli)

    # D6 - the market clock fails closed
    check("D6", "clock errors are caught", "except Exception as exc" in sc)
    check("D6", "the reason is retained", "last_error" in sc)
    check("D6", "broker outage is journaled", "CYCLE_FAILED" in lp and "CYCLE_FAILED" in md)
    check("D6", "no snapshot is fabricated for a failed cycle", "_journal_cycle_failure" in lp)

    # D7 - the LLM decides, and says what it is
    check("D7", "HybridDecisionEngine exists", "class HybridDecisionEngine" in llm)
    check("D7", "the daemon uses it", "decision_engine=decision_engine" in cli)
    check("D7", "provenance is recorded per decision", "decision_source" in md)
    check("D7", "the fallback is a distinct source", "fallback_policy_engine" in llm)
    check("D7", "provenance is stamped by the caller, not trusted", 'update={"decision_source": "llm"}' in llm)
    check("D7", "curriculum fallbacks declare themselves", 'source="fallback"' in cu)
    check("D7", "the daemon journals fallbacks as skipped", '"SKIPPED"' in py["daemon.py"])
    check("D7", "no example price in the prompt", '"reference_price":100.0' not in cli)
    check("D7", "no hard-coded created_at in the prompt", "2026-06-10T00:00:00Z" not in cli)
    check("D7", "reference_price is checked against the market", "_reference_price_rejection_reason" in sa)

    # D8 - no automatic weakening of hard risk limits
    limit_write = re.compile(r"max_(?:position_value|daily_loss|total_exposure|trades_per_day)\"?\s*[:=]\s*[\"']?[0-9]")
    lifecycle_write = re.compile(r"^[^#\n]*[\"']lifecycle[\"']\s*[:=]\s*[\"'](?:PROBATION|ACTIVE|PAUSED|RETIRED|BASELINE)", re.M)
    enable_write = re.compile(r"^[^#\n]*[\"']enabled[\"']\s*[:=]\s*(?:True|true|1)", re.M)
    for name, text in ops.items():
        check("D8", f"{name} assigns no risk limit", not limit_write.search(text))
        check("D8", f"{name} writes no lifecycle", not lifecycle_write.search(text))
        check("D8", f"{name} force-enables no strategy", not enable_write.search(text))
    # auto-fix.sh was deleted: it duplicated `doctor`'s strategy-library and pidfile
    # checks as inline Python inside a shell wrapper, and its only unique artefact was a
    # log file whose contents were a banner and a footer. The property it was guarded
    # for - an ops script must not weaken a risk limit, rewrite a lifecycle, or
    # force-enable a strategy - is still enforced over whatever ops scripts remain, and
    # the read-only drift report it performed now lives in `doctor` as
    # "refused by admission but still selectable".

    # D9 - real interpreter, real exit codes
    shells = {n: t for n, t in ops.items() if n.endswith(".sh") or n == "minictrl"}
    for name, text in ops.items():
        check("D9", f"{name} uses no bare `conda run`", not re.search(r"(?<![/\w-])conda run", text))
    for name, text in shells.items():
        check("D9", f"{name} enables pipefail", "pipefail" in text)

    # D10 - supervision
    unit = (ROOT / "tools" / "autopoiesis.service.in").read_text(encoding="utf-8")
    check("D10", "systemd unit exists", "[Service]" in unit)
    check("D10", "restarts are bounded", "StartLimitBurst=" in unit and "StartLimitIntervalSec=" in unit)
    check("D10", "no credential in the unit", "ALPACA_API_KEY=" not in unit)
    check("D10", "doctor command exists", '"--doctor"' in cli)
    check("D10", "doctor judges liveness", "liveness()" in py["doctor.py"] and 'live["live"]' in py["doctor.py"])

    # D11/D12 - durable state
    check("D11", "journal rotates", "_rotate_if_needed" in jn)
    check("D11", "journal fsyncs", "os.fsync" in jn)
    check("D11", "unparseable lines are counted", "last_dropped_lines" in jn)
    check("D11", "there is a targeted cycle scan", "count_submitted_on" in jn)
    check("D12", "atomic io exists", "def write_text_atomic" in aio and "os.replace" in aio)
    check("D12", "no bare write_text outside atomicio",
          all("write_text(" not in v for k, v in py.items() if k != "atomicio.py"))
    check("D12", "file locking is used", "file_lock" in se and "fcntl" in aio)
    check("D12", "strategy parse failures are recorded", "self.rejected" in se)

    # D13/D14 - the loop is closed
    check("D13", "fills are re-polled from the broker", "get_order_by_client_order_id" in fr)
    check("D13", "a broker outage yields no fill", "except Exception" in fr)
    check("D13", "the evaluator takes confirmed fills", "fills: Mapping[str, float]" in ev)
    check("D13", "the daemon supplies them", "_confirmed_fills" in py["daemon.py"])
    check("D13", "a fill is not reported as PnL", "PNL_EVIDENCE_MISSING" in ev)
    check("D14", "the knowledge library is read", "relevant_lessons" in pe and "knowledge_library.list(" in pe)
    decide = function_body(pe, "decide_snapshot")
    check("D14", "a broken lesson cannot stop a cycle",
          "except Exception" in decide and decide.index("except Exception") < decide.index("lesson.summary"))

    # D15 - the repository itself
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True, cwd=ROOT).stdout.strip()
    check("D15", "repo is under version control", inside == "true")
    check("D15", "archived projects are out of the tree", not (ROOT / "history_version").exists())
    check("D15", "a plan document exists",
          (ROOT / "docs/history/plans/2026-09-28-quantgroup-recovery-and-refactor.md").exists())

    width = max(len(d) for d, _, _ in results)
    for defect, description, passed in results:
        print(f"{defect:<4} {description:<{width}}  {'PASS' if passed else '**FAIL**'}")
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks pass across 15 defects")
    if failed:
        print("failing:")
        for defect, description, _ in failed:
            print(f"  {defect}  {description}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
