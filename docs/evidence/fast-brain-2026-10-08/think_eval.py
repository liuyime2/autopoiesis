"""Paired evaluation: does turning the model's thinking off change its decisions?

Same real contexts (historical SPY snapshots from the journal, built by the production
engine), three calls each: thinking twice (the model's own run-to-run noise) and not thinking
once. Read-only: nothing is submitted, nothing is journalled."""
import json, sys, time, requests
from unittest import mock
import min_agent.cli as cli
from min_agent.config import AgentConfig
from min_agent.journal import JsonlJournal

c = AgentConfig.from_env(); journal = JsonlJournal(c.journal_path)
cap = {}
class Stub:
    def __init__(self, **kw): cap.update(kw)
    def run(self, max_cycles=None): return 0
with mock.patch.object(cli, "AgentDaemon", Stub): cli._run_daemon(c)
eng = cap["loop"].decision_engine
payloads = []
real = eng.llm.transport
eng.llm.transport = lambda url, p, t: (payloads.append(p), {"response": json.dumps({"symbol": "SPY", "action": "HOLD", "quantity": 0, "confidence": 0.5, "rationale": "x", "hold_reason": "other", "override_reason": None})})[1]
recs = [r for r in journal.read_all() if r.snapshot.symbol == "SPY" and r.snapshot.timestamp.strftime("%Y-%m-%d") == "2026-10-07" and r.snapshot.market_open]
step = max(1, len(recs) // 12)
picked = recs[::step][:12]
def call(p, think):
    q = dict(p); q["think"] = think
    t0 = time.time(); r = requests.post("http://127.0.0.1:11434/api/generate", json=q, timeout=900).json()
    try: d = json.loads(r["response"])
    except Exception: d = None
    return d, time.time() - t0, r.get("eval_count", 0)
rows = []
for i, rec in enumerate(picked):
    payloads.clear(); eng.decide_snapshot(rec.snapshot)
    if not payloads: continue
    p = payloads[0]
    a1, ta1, _ = call(p, True); a2, ta2, _ = call(p, True); b, tb, nb = call(p, False)
    rows.append(dict(ts=str(rec.snapshot.timestamp), a1=a1, a2=a2, b=b, ta=(ta1 + ta2) / 2, tb=tb))
    f = lambda d: "INVALID" if d is None else f"{d['action']}{d.get('quantity')}@{d.get('confidence')} ov={'Y' if d.get('override_reason') else 'n'}"
    print(f"{i:2} {rec.snapshot.timestamp:%H:%M} think {f(a1)} / {f(a2)} | nothink {f(b)} | {ta1:.0f}s vs {tb:.0f}s", flush=True)
json.dump(rows, open(sys.argv[1], "w"))
