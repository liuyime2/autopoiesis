"""READ-ONLY: replay the deployed screen on the live journal's real rows, as-is and with correct paired trades counted."""
import collections, glob, json, sys, types, hashlib
from pathlib import Path
from autopoiesis import offline_validation as ov
from autopoiesis.models import StrategySpec
RT = Path(__import__("os").environ.get("AUTOPOIESIS_RUNTIME", "runtime/autopoiesis"))

# Variant: identical source, except a correct paired trade counts as a correct outcome (still not as an executed fill).
src = Path(ov.__file__).read_text()
old = '''        elif record.verdict == GOOD_TRADE and not getattr(record, "paired", False):
            result.good_trades += 1
'''
new = '''        elif record.verdict == GOOD_TRADE:
            if record.paired:
                result.paired_good = getattr(result, "paired_good", 0) + 1
            else:
                result.good_trades += 1
'''
oldp = "        return self.good_holds + self.good_trades\n"
newp = "        return self.good_holds + self.good_trades + getattr(self, 'paired_good', 0)\n"
assert src.count(old) == 1 and src.count(oldp) == 1
fix = types.ModuleType("ov_fix"); sys.modules["ov_fix"] = fix
exec(compile(src.replace(old, new).replace(oldp, newp), "ov_fix", "exec"), fix.__dict__)

files = sorted(glob.glob(str(RT / "journal-archive/r*.jsonl"))) + [str(RT / f"journal.jsonl{s}") for s in (".3", ".2", ".1", "")]
events = []
for f in files:
    with open(f) as fh:
        for line in fh:
            if "COUNTERFACTUAL_EVALUATED" not in line: continue
            d = json.loads(line)
            if d.get("event_type") == "COUNTERFACTUAL_EVALUATED":
                events.append((d.get("timestamp") or d.get("created_at") or "", types.SimpleNamespace(payload=d["payload"])))
events.sort(key=lambda x: x[0]); ev = [e for _, e in events]
latest = {}
for e in ev:
    for r in e.payload["rows"]: latest[r["cycle_id"]] = r
paired = [r for r in latest.values() if r.get("rule_action") and r.get("rule_verdict")]
print("events", len(ev), "| cycles", len(latest), "| paired", len(paired))
print("paired rule verdicts", dict(collections.Counter(r["rule_verdict"] for r in paired)))
print("unpaired verdicts", dict(collections.Counter(r.get("verdict") for r in latest.values() if not (r.get("rule_action") and r.get("rule_verdict")))))

tab = collections.Counter(); changed = []; n_pg = 0; by_day = {}
for p in sorted((RT / "strategies").glob("*.json")):
    spec = StrategySpec.model_validate_json(p.read_text())
    sym = spec.symbols[0]
    if sym not in by_day: by_day[sym] = ov.day_direction(ev, symbol=sym)
    recs = ov.collect_decisions(ev, spec.strategy_id, strategy=spec)
    if not recs: continue
    a = ov.validate(recs, strategy_id=spec.strategy_id, day_up=by_day[sym])
    frecs = [fix.DecisionRecord(**r.__dict__) for r in recs]
    b = fix.validate(frecs, strategy_id=spec.strategy_id, day_up=by_day[sym])
    pg = sum(1 for r in recs if r.paired and r.verdict == ov.GOOD_TRADE and a.scored); n_pg += pg > 0
    tab[(a.verdict, b.verdict)] += 1
    if a.verdict != b.verdict:
        changed.append((spec.strategy_id, spec.lifecycle, sym, a.scored, pg, a.verdict, a.day_margin, b.verdict, b.day_margin))
print("strategies with records", sum(tab.values()), "| with >=1 correct paired trade", n_pg)
for k, v in sorted(tab.items()): print(v, "deployed=%s -> counted=%s" % k)
for c in changed: print(*c)
