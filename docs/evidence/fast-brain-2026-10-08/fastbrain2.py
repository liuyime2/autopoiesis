"""Fast brain: a frozen local LLM reads the answer-letter logits for a market-only prompt.
One forward pass, no generation. Usage: fastbrain2.py MODEL dataset.json out.json PORT
The template is chosen by model family; models that always think (old qwen3 tags) are refused
rather than scored on a prompt they will not follow."""
import json, math, sys, time, requests
model, src, dst, port = sys.argv[1:5]
rows = json.load(open(src))
def prompt_text(raw):
    c = json.loads(raw.split("\nContext: ", 1)[1])
    keep = {k: c.get(k) for k in ("symbol", "timestamp", "last_price", "market_open", "market")}
    return ("You are a market analyst. From the recent price behaviour below, say which way the price is most likely to move over the next hour.\n"
            "A) UP (more than +0.10%)\nB) FLAT (within 0.10%)\nC) DOWN (more than -0.10%)\n\nData: "
            + json.dumps(keep, sort_keys=True) + "\n\nAnswer with exactly one letter: A, B or C.")
def letters(top):
    m = {"A": 0.0, "B": 0.0, "C": 0.0}
    for t in top:
        k = t["token"].strip().strip("*()").upper()
        if k in m: m[k] += math.exp(t["logprob"])
    return m
def ask(text):
    if "deepseek-r1" in model:
        body = {"model": model, "prompt": "<｜User｜>" + text + "<｜Assistant｜><think>\n\n</think>\n\n", "raw": True, "stream": False,
                "logprobs": True, "top_logprobs": 20, "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1}}
        return requests.post(f"http://127.0.0.1:{port}/api/generate", json=body, timeout=600).json()["logprobs"][0]["top_logprobs"]
    if model.startswith("hf.co/"):  # community decision-style checkpoints: plain text, no chat template
        body = {"model": model, "prompt": text.split("\n\nAnswer with exactly")[0] + "\n\nAnswer with exactly one letter: A, B or C.\nAnswer:", "raw": True, "stream": False,
                "logprobs": True, "top_logprobs": 20, "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1}}
        return requests.post(f"http://127.0.0.1:{port}/api/generate", json=body, timeout=600).json()["logprobs"][0]["top_logprobs"]
    body = {"model": model, "messages": [{"role": "user", "content": text}], "stream": False, "think": False,
            "logprobs": True, "top_logprobs": 20, "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1}}
    j = requests.post(f"http://127.0.0.1:{port}/api/chat", json=body, timeout=600).json()
    if "error" in j and "think" in j["error"].lower():
        body.pop("think"); j = requests.post(f"http://127.0.0.1:{port}/api/chat", json=body, timeout=600).json()
    return j["logprobs"][0]["top_logprobs"]
out = []; bad = 0; t0 = time.time(); masses = []
for i, r in enumerate(rows):
    try:
        m = letters(ask(prompt_text(r["prompt"])))
    except Exception:
        bad += 1; continue
    s = sum(m.values()); masses.append(s)
    if s < 0.5: bad += 1; continue
    out.append(dict(ts=r["ts"], buy=m["A"] / s, hold=m["B"] / s, sell=m["C"] / s, mass=s, f1=r["f1"], f4=r["f4"], rule=r["rule"], prompt=r["prompt"]))
    if i == 20 and bad > 15:  # refuse a model that does not answer in letters
        break
json.dump(out, open(dst, "w"))
print(f"{model}: {len(out)}/{len(rows)} usable, {bad} unusable, mean letter mass {sum(masses)/max(len(masses),1):.2f}, {(time.time()-t0)/max(i+1,1):.2f}s/example")
