"""Score Ollama decision models (/v1/systemone) on the same benchmark as the letter-logit brains.
Usage: systemone_bench.py MODEL dataset.json out.json PORT"""
import json, sys, time, requests
model, src, dst, port = sys.argv[1:5]
rows = json.load(open(src)); out = []; t0 = time.time(); bad = 0
extra = []
Q = {
    "direction": {"type": "choice", "instructions": "Which way is the price most likely to move over the next hour?",
                  "criteria": {"up": "more than +0.10%", "flat": "within 0.10%", "down": "more than -0.10%"}},
    "higher": {"type": "noul", "instructions": "Will the price be higher one hour from now?"},
    "move": {"type": "score", "instructions": "How will the price change over the next hour?",
             "criteria": ["falls sharply", "falls", "about flat", "rises", "rises sharply"]},
}
for r in rows:
    c = json.loads(r["prompt"].split("\nContext: ", 1)[1])
    state = {k: c.get(k) for k in ("symbol", "timestamp", "last_price", "market_open", "market")}
    try:
        j = requests.post(f"http://127.0.0.1:{port}/v1/systemone", json={"model": model, "state": state, "questions": Q}, timeout=600).json()["answers"]
    except Exception:
        bad += 1; continue
    p = j["direction"]["probabilities"]
    out.append(dict(ts=r["ts"], buy=p["up"], hold=p["flat"], sell=p["down"], mass=1.0, f1=r["f1"], f4=r["f4"], rule=r["rule"], prompt=r["prompt"]))
    extra.append(dict(ts=r["ts"], higher=j["higher"]["noul"], move=j["move"]["score"], f1=r["f1"], f4=r["f4"]))
json.dump(out, open(dst, "w")); json.dump(extra, open(dst.replace(".json", "_extra.json"), "w"))
print(f"{model}: {len(out)}/{len(rows)} usable, {bad} failed, {(time.time()-t0)/max(len(rows),1):.2f}s/example")
