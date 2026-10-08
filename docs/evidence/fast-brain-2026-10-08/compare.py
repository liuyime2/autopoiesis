"""Compare fast brains on one benchmark, with day-block bootstrap intervals and ensembles."""
import json, math, random, re, statistics as st, sys
from collections import defaultdict
C = 0.10
def spearman(x, y):
    def rk(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]: j += 1
            for k in range(i, j + 1): r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    a, b = rk(x), rk(y); ma, mb = st.mean(a), st.mean(b)
    den = math.sqrt(sum((p - ma) ** 2 for p in a) * sum((q - mb) ** 2 for q in b))
    return sum((p - ma) * (q - mb) for p, q in zip(a, b)) / den if den else 0.0
def lab(f): return "BUY" if f > C else ("SELL" if f < -C else "HOLD")
def act(r): return max((("BUY", r["buy"]), ("HOLD", r["hold"]), ("SELL", r["sell"])), key=lambda t: t[1])[0]
def metrics(rows):
    y = [lab(r["f1"]) for r in rows]; a = [act(r) for r in rows]
    brier = st.mean(sum((p - (k == yy)) ** 2 for k, p in (("BUY", r["buy"]), ("HOLD", r["hold"]), ("SELL", r["sell"]))) for r, yy in zip(rows, y))
    cap = sum(r["f1"] if x == "BUY" else (-r["f1"] if x == "SELL" else 0.0) for r, x in zip(rows, a))
    f4 = [(r["buy"] - r["sell"], r["f4"]) for r in rows if r.get("f4") is not None]
    return dict(acc=sum(p == q for p, q in zip(a, y)) / len(rows), brier=brier, cap=cap, rho4=spearman([x for x, _ in f4], [y_ for _, y_ in f4]) if len(f4) > 20 else float("nan"),
                rho=spearman([r["buy"] - r["sell"] for r in rows], [r["f1"] for r in rows]),
                mix="/".join(str(a.count(k)) for k in ("BUY", "HOLD", "SELL")))
def boot(rows, key, n=400, seed=7):
    days = defaultdict(list)
    for r in rows: days[r["ts"][:10]].append(r)
    keys = list(days); rnd = random.Random(seed); vals = []
    for _ in range(n):
        s = [r for d in (rnd.choice(keys) for _ in keys) for r in days[d]]
        vals.append(metrics(s)[key])
    vals.sort(); return vals[int(.05 * n)], vals[int(.95 * n)]
def load(p): return {r["ts"]: r for r in json.load(open(p))}
def line(name, rows):
    m = metrics(rows); lo, hi = boot(rows, "rho")
    print(f"{name:34} n={len(rows):3} acc={m['acc']:.3f} brier={m['brier']:.3f} mix={m['mix']:>9} captured={m['cap']:+6.2f}% rho1h={m['rho']:+.3f} [90% CI {lo:+.2f},{hi:+.2f}] rho4h={m['rho4']:+.3f}")
    return m
if __name__ == "__main__":
    paths = sys.argv[1:]; sets = {p.split("/")[-1][:-5]: load(p) for p in paths}
    common = set.intersection(*[set(s) for s in sets.values()]) if sets else set()
    keys = sorted(common)
    print(f"common examples: {len(keys)}")
    per = {}
    for name, s in sorted(sets.items()):
        rows = [s[k] for k in keys]; per[name] = rows; line(name, rows)
    base = per[next(iter(per))]
    print("-- baselines")
    for nm, f in (("always HOLD", lambda r: "HOLD"), ("follow past-1h return", lambda r: (lambda m: "BUY" if m > C else "SELL" if m < -C else "HOLD")(float(re.search(r'"return_1h_pct": (-?[\d.]+)', r["prompt"]).group(1)) if re.search(r'"return_1h_pct": (-?[\d.]+)', r["prompt"]) else 0.0))):
        a = [f(r) for r in base]; y = [lab(r["f1"]) for r in base]
        cap = sum(r["f1"] if x == "BUY" else (-r["f1"] if x == "SELL" else 0.0) for r, x in zip(base, a))
        print(f"{nm:34} n={len(base):3} acc={sum(p == q for p, q in zip(a, y)) / len(base):.3f} captured={cap:+6.2f}%")
    print("-- ensembles (mean of probabilities)")
    names = sorted(per)
    def ens(ns):
        return [dict(base[i], buy=st.mean(per[n][i]["buy"] for n in ns), hold=st.mean(per[n][i]["hold"] for n in ns), sell=st.mean(per[n][i]["sell"] for n in ns)) for i in range(len(base))]
    line("ALL models", ens(names))
    best = sorted(names, key=lambda n: -metrics(per[n])["rho"])[:3]
    line("top-3 by rho: " + ",".join(b.replace("mo_", "")[:10] for b in best), ens(best))
    big = [n for n in names if re.search(r"(27|30|32|35|70|72)b", n)]
    small = [n for n in names if n not in big]
    if big and small: line(f"big({len(big)}) only", ens(big)); line(f"small({len(small)}) only", ens(small))
    print("-- combination rules (act only when the rule fires; 1h return captured, acts = number of trades)")
    def r1h(r):
        m = re.search(r'"return_1h_pct": (-?[\d.]+)', r["prompt"]); return float(m.group(1)) if m else 0.0
    def score_rule(name, fn):
        acts = [fn(i) for i in range(len(base))]; y = [lab(r["f1"]) for r in base]
        cap = sum(r["f1"] if a == "BUY" else (-r["f1"] if a == "SELL" else 0.0) for r, a in zip(base, acts))
        n = sum(a != "HOLD" for a in acts); right = sum(a == b and a != "HOLD" for a, b in zip(acts, y))
        print(f"{name:34} acts={n:3} right-when-acting={(right / n if n else float('nan')):.3f} (chance-ish 0.33) captured={cap:+6.2f}%")
    for k in (3, 5, 7):
        if len(names) >= k:
            def vote(i, k=k):
                v = [act(per[n][i]) for n in names]; b, s = v.count("BUY"), v.count("SELL")
                return "BUY" if b >= k and b > s else ("SELL" if s >= k and s > b else "HOLD")
            score_rule(f">= {k} of {len(names)} models agree on a direction", vote)
    def with_momentum(i):
        v = [act(per[n][i]) for n in names]; b, s = v.count("BUY"), v.count("SELL"); m = r1h(base[i])
        d = "BUY" if b > s else ("SELL" if s > b else "HOLD")
        return d if (d == "BUY" and m > 0) or (d == "SELL" and m < 0) else "HOLD"
    score_rule("majority AND past-1h momentum agree", with_momentum)
    def most_confident(i):
        n = max(names, key=lambda n: max(per[n][i]["buy"], per[n][i]["hold"], per[n][i]["sell"]))
        return act(per[n][i])
    score_rule("the single most confident model", most_confident)
    print("-- pairwise signal correlation (Spearman of P(buy)-P(sell))")
    for i, a in enumerate(names):
        print(f"{a.replace('mo_','')[:18]:18}", " ".join(f"{spearman([r['buy']-r['sell'] for r in per[a]],[r['buy']-r['sell'] for r in per[b]]):+.2f}" for b in names))
