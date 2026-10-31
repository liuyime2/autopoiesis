"""Which strategies that PASS_SCREENED today would keep the pass under MIN_SCREEN_DAYS and MIN_TRADE_EVIDENCE?

Read-only. Takes each strategy's latest OFFLINE_VALIDATION_COMPLETED record from the real journal,
maps its source cycles to the days they were made on, and applies the two new requirements to the
counts the record already carries. Nothing is recomputed from scratch and nothing is written.

Usage: python screen_rescore.py <runtime/autopoiesis dir> <out.json>
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

MIN_DAYS, MIN_TRADES = 10, 5   # the values in autopoiesis.offline_validation


def main() -> None:
    root = Path(sys.argv[1])
    files = [f for f in glob.glob(str(root / "journal.jsonl*")) + glob.glob(str(root / "journal-archive/*")) if not f.endswith(".lock")]
    latest: dict[str, dict] = {}
    for f in files:
        for line in open(f, errors="replace"):
            if '"OFFLINE_VALIDATION_COMPLETED"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            sid = d.get("strategy_id")
            if sid and (sid not in latest or d["timestamp"] > latest[sid]["timestamp"]):
                latest[sid] = d
    passing = {s: d for s, d in latest.items() if (d.get("payload") or {}).get("verdict") == "PASS_SCREENED"}
    wanted = {c for d in passing.values() for c in d["payload"].get("source_cycle_ids", [])}
    day_of: dict[str, str] = {}
    for f in files:
        for line in open(f, errors="replace"):
            if '"execution":' not in line or '"event_id":' in line:
                continue
            i = line.find('"cycle_id":"')
            cid = line[i + 12:i + 48]
            if cid in wanted:
                j = line.find('"timestamp":"')
                day_of[cid] = line[j + 13:j + 23]
    rows = []
    for sid, d in sorted(passing.items()):
        p = d["payload"]
        days = {day_of[c] for c in p.get("source_cycle_ids", []) if c in day_of}
        trades = (p.get("good_trades") or 0) + (p.get("false_trades") or 0)
        keeps = len(days) >= MIN_DAYS and trades >= MIN_TRADES
        rows.append({"strategy_id": sid, "scored": p.get("scored"), "trades": trades, "days": len(days),
                     "keeps_pass": keeps, "why_not": [] if keeps else
                     [r for r, bad in (("fewer than 10 days", len(days) < MIN_DAYS), ("fewer than 5 scored trades", trades < MIN_TRADES)) if bad]})
    summary = {"passing_now": len(rows), "keep_the_pass": sum(r["keeps_pass"] for r in rows), "rows": rows}
    Path(sys.argv[2]).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "rows"})
    for r in rows:
        print(r["strategy_id"], r["scored"], "scored,", r["trades"], "trades,", r["days"], "days ->", "KEEPS" if r["keeps_pass"] else "LOSES: " + ", ".join(r["why_not"]))


if __name__ == "__main__":
    main()
