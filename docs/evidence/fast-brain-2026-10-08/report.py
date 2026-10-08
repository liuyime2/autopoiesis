"""Final table: one row per model, benchmark metrics + size + latency, as markdown."""
import glob, json, os, re, sys, requests
sys.path.insert(0, os.path.dirname(__file__))
from compare import metrics, boot, load
S = os.path.dirname(os.path.abspath(__file__))
tags = {m["name"]: m for m in requests.get("http://127.0.0.1:11439/api/tags", timeout=20).json()["models"]}
def key(name):  # result file stem -> ollama tag (best effort)
    return re.sub(r"[:/.]", "_", name).lower()
byfile = {}
for n, m in tags.items():
    f = re.sub(r"hf.co/[^/]*/", "", n).replace("-GGUF", "").split(":latest")[0]
    byfile[re.sub(r"[:/.]", "_", f).lower()] = (n, m)
lat = {}
for log in glob.glob(S + "/queue_*.log") + glob.glob(S + "/mo*.out"):
    for line in open(log, errors="ignore"):
        m = re.match(r"(\S+): (\d+)/(\d+) usable.*?([\d.]+)s/example", line)
        if m: lat[m.group(1)] = float(m.group(4))
lat.update({"qwen3.8:27b": 1.78, "qwen3:8b": 0.38, "llama3.1:8b": 0.30, "gemma2:9b": 0.78, "qwen2.5:7b": 0.40})
rows = []
for p in sorted(glob.glob(S + "/mo_*.json")):
    data = json.load(open(p))
    if len(data) < 250: continue
    stem = os.path.basename(p)[3:-5]
    name, info = byfile.get(stem, (stem, None))
    ps = (info or {}).get("details", {}).get("parameter_size", "?")
    gb = (info or {}).get("size", 0) / 2**30
    rows.append((name, ps, gb, lat.get(name, float("nan")), data))
common = set.intersection(*[{r["ts"] for r in d} for *_, d in rows])
print(f"{len(common)} common examples\n")
print("| model | params | GB | s/decision | acc | Brier | BUY/HOLD/SELL | 1h captured | rho 1h [90% CI] | rho 4h |")
print("|---|---:|---:|---:|---:|---:|---|---:|---|---:|")
def pnum(s):
    m = re.match(r"([\d.]+)([MB])", s); return float(m.group(1)) * (0.001 if m and m.group(2) == "M" else 1) if m else 0
for name, ps, gb, sec, data in sorted(rows, key=lambda r: (pnum(r[1]), r[0])):
    sub = [r for r in sorted(data, key=lambda r: r["ts"]) if r["ts"] in common]
    m = metrics(sub); lo, hi = boot(sub, "rho", n=300)
    print(f"| {name.replace('hf.co/','')[:44]} | {ps} | {gb:.1f} | {sec:.2f} | {m['acc']:.3f} | {m['brier']:.3f} | {m['mix']} | {m['cap']:+.2f}% | {m['rho']:+.3f} [{lo:+.2f}, {hi:+.2f}] | {m['rho4']:+.3f} |")
