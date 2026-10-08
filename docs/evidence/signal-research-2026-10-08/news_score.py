"""Score sampled headlines with a decision model or a letter-logit LLM. One fixed question, no tuning.
Usage: news_score.py MODEL news.json out.json PORT N [letters]"""
import json, math, random, sys, time, requests
model, src, dst, port, N = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
letters_mode = len(sys.argv) > 6 and sys.argv[6] == "letters"
items = [x for x in json.load(open(src)) if "2023-01-01" <= x["t"][:10] <= "2026-09-25"]
items.sort(key=lambda x: x["id"]); random.Random(7).shuffle(items); items = items[:N]
Q = {"impact": {"type": "choice", "instructions": "How does this news affect the company's stock price over the next trading day?",
                "criteria": {"positive": "good news for the company's stock", "neutral": "no clear effect, or not about the company itself", "negative": "bad news for the company's stock"}}}
out = []; t0 = time.time(); bad = 0
for k, x in enumerate(items):
    state = {"company": x["sym"], "headline": x["headline"], "summary": x["summary"]}
    try:
        if letters_mode:
            text = ("You are an equity analyst. How does this news affect the stock price of %s over the next trading day?\nA) positive: good news for the stock\nB) neutral: no clear effect, or not about the company itself\nC) negative: bad news for the stock\n\nHeadline: %s\nSummary: %s\n\nAnswer with exactly one letter: A, B or C." % (x["sym"], x["headline"], x["summary"]))
            j = requests.post(f"http://127.0.0.1:{port}/api/chat", json={"model": model, "messages": [{"role": "user", "content": text}], "stream": False, "think": False, "logprobs": True, "top_logprobs": 20, "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1}}, timeout=600).json()
            m = {"A": 0.0, "B": 0.0, "C": 0.0}
            for tk in j["logprobs"][0]["top_logprobs"]:
                key = tk["token"].strip().strip("*()").upper()
                if key in m: m[key] += math.exp(tk["logprob"])
            s = sum(m.values())
            if s < 0.5: bad += 1; continue
            p = {"positive": m["A"] / s, "neutral": m["B"] / s, "negative": m["C"] / s}
        else:
            j = requests.post(f"http://127.0.0.1:{port}/v1/systemone", json={"model": model, "state": state, "questions": Q}, timeout=600).json()
            p = j["answers"]["impact"]["probabilities"]
    except Exception:
        bad += 1; continue
    out.append(dict(id=x["id"], sym=x["sym"], t=x["t"], pos=p["positive"], neu=p["neutral"], neg=p["negative"]))
    if k % 250 == 0: print(model, k, len(items), f"{time.time()-t0:.0f}s", flush=True)
json.dump(out, open(dst, "w"))
print(f"{model}: {len(out)}/{len(items)} scored, {bad} failed, {(time.time()-t0)/max(len(items),1):.2f}s/item")
