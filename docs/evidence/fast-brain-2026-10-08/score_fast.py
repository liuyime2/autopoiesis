import json, math, re, sys, statistics as st
from collections import defaultdict
C = 0.10  # % : a move smaller than this is "flat" (round-trip cost is 0.05%)
def spearman(x, y):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v)
        for pos, i in enumerate(o): r[i] = pos
        return r
    a, b = rk(x), rk(y); ma, mb = st.mean(a), st.mean(b)
    num = sum((p - ma) * (q - mb) for p, q in zip(a, b)); den = math.sqrt(sum((p - ma) ** 2 for p in a) * sum((q - mb) ** 2 for q in b))
    return num / den if den else float("nan")
def label(f): return "BUY" if f > C else ("SELL" if f < -C else "HOLD")
def report(name, rows):
    n = len(rows); y = [label(r["f1"]) for r in rows]
    act = [max((("BUY", r["buy"]), ("HOLD", r["hold"]), ("SELL", r["sell"])), key=lambda t: t[1])[0] for r in rows]
    acc = sum(a == b for a, b in zip(act, y)) / n
    brier = st.mean(sum((p - (1.0 if k == yy else 0.0)) ** 2 for k, p in (("BUY", r["buy"]), ("HOLD", r["hold"]), ("SELL", r["sell"]))) for r, yy in zip(rows, y))
    cap = sum(r["f1"] if a == "BUY" else (-r["f1"] if a == "SELL" else 0.0) for r, a in zip(rows, act))
    score = [r["buy"] - r["sell"] for r in rows]
    print(f"{name:28} n={n} acc={acc:.3f} brier={brier:.3f} | action mix B/H/S={act.count('BUY')}/{act.count('HOLD')}/{act.count('SELL')} | 1h captured {cap:+.2f}% | spearman(P(buy)-P(sell), f1)={spearman(score,[r['f1'] for r in rows]):+.3f} f4={spearman(score,[r['f4'] for r in rows if r['f4'] is not None] if all(r['f4'] is not None for r in rows) else [r['f1'] for r in rows]):+.3f}")
    return act
def baselines(rows):
    n = len(rows); y = [label(r["f1"]) for r in rows]
    def r1h(r):
        m = re.search(r'"return_1h_pct": (-?[\d.]+)', r["prompt"]); return float(m.group(1)) if m else None
    for name, f in (("always HOLD", lambda r: "HOLD"), ("always BUY", lambda r: "BUY"),
                    ("follow past-1h return", lambda r: ("BUY" if (r1h(r) or 0) > C else "SELL" if (r1h(r) or 0) < -C else "HOLD")),
                    ("the strategy rule", lambda r: r["rule"] if r["rule"] in ("BUY", "SELL", "HOLD") else "HOLD")):
        a = [f(r) for r in rows]; cap = sum(r["f1"] if x == "BUY" else (-r["f1"] if x == "SELL" else 0.0) for r, x in zip(rows, a))
        print(f"{name:28} n={n} acc={sum(p == q for p, q in zip(a, y)) / n:.3f} | action mix B/H/S={a.count('BUY')}/{a.count('HOLD')}/{a.count('SELL')} | 1h captured {cap:+.2f}%")
rows0 = None
for path in sys.argv[1:]:
    rows = json.load(open(path)); rows0 = rows0 or rows
    report(path.split("/")[-1].replace(".json", ""), rows)
baselines(rows0)
d = defaultdict(list)
for r in rows0: d[r["ts"][:10]].append(r["f1"])
print("days:", len(d), "| label mix (|f1|>0.10%): up", sum(label(r['f1'])=='BUY' for r in rows0), "flat", sum(label(r['f1'])=='HOLD' for r in rows0), "down", sum(label(r['f1'])=='SELL' for r in rows0))
