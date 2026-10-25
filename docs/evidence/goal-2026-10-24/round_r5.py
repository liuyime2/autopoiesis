"""Round R5: did showing the curriculum the signal ledger (and refusing cash-ETF and TREND_FOLLOW
probes) change what it proposes? Read-only; real strategy files only; nothing is estimated.

Preregistered in ROUND_R5.md BEFORE any treatment-period strategy existed. Baseline window and
treatment window are cut on the strategy's own `created_at`:

  baseline   2026-10-15T00:00Z .. 2026-10-24T23:55Z   (old rules; 44 strategies at registration)
  treatment  from 2026-10-25T01:57Z                    (build b4868493: refusals + ledger summary)

Usage: python round_r5.py <strategy_dir> [--now ISO]   (prints one JSON object)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASELINE = (datetime(2026, 10, 15, tzinfo=timezone.utc), datetime(2026, 10, 24, 23, 55, tzinfo=timezone.utc))
TREATMENT_FROM = datetime(2026, 10, 25, 1, 57, tzinfo=timezone.utc)
CASH = {"BIL", "SHV", "SGOV", "USFR", "TFLO", "BOXX", "ICSH", "MINT", "JPST", "GBIL"}
MATURE_DAYS = 7  # a strategy is judged only once it has had this long to be retired
DEAD = {"RETIRED", "PAUSED"}


def load(strategy_dir: Path) -> list[dict]:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(strategy_dir.glob("*.json"))]
    for row in rows:
        row["_created"] = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    return rows


def summarise(rows: list[dict], now: datetime) -> dict:
    mature = [r for r in rows if now - r["_created"] >= timedelta(days=MATURE_DAYS)]
    return {
        "n": len(rows),
        "kinds": dict(Counter(r["kind"] for r in rows)),
        "cash_etf": sum(1 for r in rows if CASH & set(r["symbols"])),
        "cites_ledger": sum(1 for r in rows if "ledger" in (r.get("rationale") or "").lower()),
        "mature_n": len(mature),
        "mature_dead_share": round(sum(r["lifecycle"] in DEAD for r in mature) / len(mature), 3) if mature else None,
    }


def main() -> None:
    args = sys.argv[1:]
    now = datetime.fromisoformat(args[args.index("--now") + 1]) if "--now" in args else datetime.now(timezone.utc)
    rows = load(Path(args[0]))
    base = [r for r in rows if BASELINE[0] <= r["_created"] <= BASELINE[1]]
    treat = [r for r in rows if r["_created"] >= TREATMENT_FROM]
    out = {"as_of": now.isoformat(), "baseline": summarise(base, now), "treatment": summarise(treat, now)}
    t = out["treatment"]
    out["verdict"] = (
        "NOT_YET_JUDGEABLE: fewer than 15 treatment strategies" if t["n"] < 15
        else "NOT_YET_JUDGEABLE: fewer than 10 treatment strategies are 7 days old" if t["mature_n"] < 10
        else "IMPROVED" if (t["cash_etf"] == 0 and t["cites_ledger"] / t["n"] >= 0.5
                            and t["mature_dead_share"] < 0.8)
        else "NOT_IMPROVED"
    )
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
