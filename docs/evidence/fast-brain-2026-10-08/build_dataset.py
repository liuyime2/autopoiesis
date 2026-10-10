"""Evaluation set from our own broker record only: for sampled historical SPY cycles, the
production prompt (built by the production engine, without the lessons - they were distilled
from later data) and the realised forward returns. No outside data."""
import bisect, json, sys
from datetime import timedelta
from unittest import mock
import autopoiesis.cli as cli
from autopoiesis.config import AgentConfig
from autopoiesis.journal import JsonlJournal

c = AgentConfig.from_env(); journal = JsonlJournal(c.journal_path)
cap = {}
class Stub:
    def __init__(self, **kw): cap.update(kw)
    def run(self, max_cycles=None): return 0
with mock.patch.object(cli, "AgentDaemon", Stub): cli._run_daemon(c)
eng = cap["loop"].decision_engine
eng.lessons = None
seen = []
eng.llm.transport = lambda url, p, t: (seen.append(p), {"response": json.dumps({"symbol": "SPY", "action": "HOLD", "quantity": 0, "confidence": 0.5, "rationale": "x", "hold_reason": "other", "override_reason": None})})[1]
recs = [r for r in journal.read_all() if r.snapshot.symbol == "SPY" and r.snapshot.market_open]
series = sorted((r.snapshot.timestamp, r.snapshot.last_price) for r in journal.read_all() if r.snapshot.symbol == "SPY")
ts = [t for t, _ in series]
def fwd(t, h):
    i = bisect.bisect_left(ts, t); p0 = series[min(i, len(series) - 1)][1]
    k = bisect.bisect_left(ts, t + timedelta(hours=h))
    return None if k >= len(series) else (series[k][1] / p0 - 1) * 100
out = []
for r in recs[::3]:
    f1, f4 = fwd(r.snapshot.timestamp, 1.0), fwd(r.snapshot.timestamp, 4.0)
    if f1 is None: continue
    seen.clear(); d = eng.decide_snapshot(r.snapshot)
    if not seen: continue
    out.append(dict(ts=str(r.snapshot.timestamp), prompt=seen[0]["prompt"], rule=d.rule_action, f1=f1, f4=f4))
json.dump(out, open(sys.argv[1], "w"))
print(len(out), "examples; up(>0.1%)", sum(o["f1"] > .1 for o in out), "down(<-0.1%)", sum(o["f1"] < -.1 for o in out), "flat", sum(abs(o["f1"]) <= .1 for o in out))
