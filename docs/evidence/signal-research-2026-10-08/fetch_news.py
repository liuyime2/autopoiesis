"""Sample Benzinga headlines (via Alpaca) for large caps, 2023-01..2026-09: random (stock, month) pairs, fixed seed."""
import json, os, random, sys, time
import alpaca_trade_api as t
api = t.REST(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], os.environ["ALPACA_BASE_URL"])
stocks = open(sys.argv[1]).read().split()
months = [(y, m) for y in (2023, 2024, 2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 9)]
pairs = [(s, y, m) for s in stocks for (y, m) in months]
random.Random(42).shuffle(pairs)
out = {}; t0 = time.time()
for k, (s, y, m) in enumerate(pairs[: int(sys.argv[3])]):
    start = f"{y}-{m:02d}-01"; end = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"
    try:
        items = api.get_news(symbol=s, start=start, end=end, limit=50)
    except Exception:
        time.sleep(1); continue
    for x in items:
        if len(x.symbols) <= 3 and s in x.symbols and x.id not in out:
            out[x.id] = dict(id=x.id, sym=s, t=str(x.created_at), headline=x.headline, summary=(x.summary or "")[:500], nsym=len(x.symbols))
    if k % 100 == 0: print(k, len(out), f"{time.time() - t0:.0f}s", flush=True)
json.dump(list(out.values()), open(sys.argv[2], "w"))
print("saved", len(out))
