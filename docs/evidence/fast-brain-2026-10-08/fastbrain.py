"""SemIf-style fast brain: a frozen local LLM reads the answer-letter logits for the production
context. One forward pass, no generation. Usage: fastbrain.py MODEL dataset.json out.json"""
import json, math, sys, time, requests
model, src, dst = sys.argv[1:4]
PORT = sys.argv[4] if len(sys.argv) > 4 else "11434"
MARKET_ONLY = len(sys.argv) > 5 and sys.argv[5] == "market-only"
RAW_QWEN3 = len(sys.argv) > 6 and sys.argv[6] == "raw-qwen3"
rows = json.load(open(src))
def user_prompt(prompt):
    ctx = prompt.split("\nContext: ", 1)[1]
    if MARKET_ONLY:
        c = json.loads(ctx)
        keep = {k: c.get(k) for k in ("symbol", "timestamp", "last_price", "market_open", "market")}
        return ("You are a market analyst. From the recent price behaviour below, say which way the price is most likely to move over the next hour.\n"
                "A) UP (more than +0.10%)\nB) FLAT (within 0.10%)\nC) DOWN (more than -0.10%)\n\nData: "
                + json.dumps(keep, sort_keys=True) + "\n\nAnswer with exactly one letter: A, B or C.")
    return ("You are a paper-trading decision engine. Given the context, choose the single best action.\n"
            "A) BUY one share\nB) HOLD\nC) SELL\n\nContext: " + ctx + "\n\nAnswer with exactly one letter: A, B or C.")
out = []; t0 = time.time(); bad = 0
for i, r in enumerate(rows):
    if RAW_QWEN3:
        # older Qwen3 ignores think:false; close an empty think block ourselves so the first token is the answer
        raw = "<|im_start|>user\n" + user_prompt(r["prompt"]) + "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        body = {"model": model, "prompt": raw, "raw": True, "stream": False, "logprobs": True, "top_logprobs": 20,
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1}}
        try:
            lp = requests.post("http://127.0.0.1:" + PORT + "/api/generate", json=body, timeout=300).json()["logprobs"][0]["top_logprobs"]
        except Exception:
            bad += 1; continue
        m = {"A": 0.0, "B": 0.0, "C": 0.0}
        for tk in lp:
            k = tk["token"].strip()
            if k in m: m[k] += math.exp(tk["logprob"])
        s = sum(m.values())
        if s < 0.5: bad += 1; continue
        out.append(dict(ts=r["ts"], buy=m["A"] / s, hold=m["B"] / s, sell=m["C"] / s, mass=s, f1=r["f1"], f4=r["f4"], rule=r["rule"], prompt=r["prompt"]))
        continue
    body = {"model": model, "messages": [{"role": "user", "content": user_prompt(r["prompt"])}], "stream": False,
            "think": False, "logprobs": True, "top_logprobs": 20,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1}}
    try:
        lp = requests.post("http://127.0.0.1:" + PORT + "/api/chat", json=body, timeout=300).json()["logprobs"][0]["top_logprobs"]
    except Exception:
        bad += 1; continue
    m = {"A": 0.0, "B": 0.0, "C": 0.0}
    for t in lp:
        k = t["token"].strip()
        if k in m: m[k] += math.exp(t["logprob"])
    s = sum(m.values())
    if s < 0.5: bad += 1; continue
    out.append(dict(ts=r["ts"], buy=m["A"] / s, hold=m["B"] / s, sell=m["C"] / s, mass=s, f1=r["f1"], f4=r["f4"], rule=r["rule"], prompt=r["prompt"]))
    if i % 50 == 0: print(i, len(rows), f"{time.time()-t0:.0f}s", flush=True)
json.dump(out, open(dst, "w"))
print(f"done {len(out)}/{len(rows)} usable, {bad} unusable, {(time.time()-t0)/max(len(rows),1):.2f}s/example")
