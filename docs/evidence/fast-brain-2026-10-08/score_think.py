import json, bisect, sys
from datetime import datetime, timedelta
from autopoiesis.journal import JsonlJournal
rows=json.load(open(sys.argv[1]))
j=JsonlJournal("runtime/autopoiesis/journal.jsonl")
series=sorted((r.snapshot.timestamp, r.snapshot.last_price) for r in j.read_all() if r.snapshot.symbol=="SPY")
ts=[t for t,_ in series]
def fwd(t, hours):
    i=bisect.bisect_left(ts, t); p0=series[min(i,len(series)-1)][1]
    k=bisect.bisect_left(ts, t+timedelta(hours=hours))
    return None if k>=len(series) else (series[k][1]/p0-1)*100
act=lambda d: d["action"] if d else None
def stat(sel):
    n=0; s=0.0
    for r in rows:
        f=fwd(datetime.fromisoformat(r["ts"]),1.0)
        if f is None: continue
        a=act(sel(r)); n+=1; s += f if a=="BUY" else (-f if a=="SELL" else 0.0)
    return n, s
for label, sel in (("think run 1", lambda r:r["a1"]), ("think run 2", lambda r:r["a2"]), ("nothink", lambda r:r["b"])):
    n,s=stat(sel); print(f"{label:12} {n} scenarios with a 1h outcome | 1h forward return captured by its actions: {s:+.3f}%")
bh=[x for x in (fwd(datetime.fromisoformat(r['ts']),1.0) for r in rows) if x is not None]
print(f"always BUY (the rule): {sum(bh):+.3f}% | up-hours {sum(x>0 for x in bh)}, down-hours {sum(x<0 for x in bh)}, flat {sum(x==0 for x in bh)}")
agree=lambda a,b: sum(act(r[a])==act(r[b]) for r in rows)
print("action agreement  think-vs-think:", agree("a1","a2"),"/",len(rows)," think-vs-nothink:", agree("a1","b"),"/",len(rows))
print("mean latency  think %.1fs   nothink %.1fs" % (sum(r['ta'] for r in rows)/len(rows), sum(r['tb'] for r in rows)/len(rows)))
