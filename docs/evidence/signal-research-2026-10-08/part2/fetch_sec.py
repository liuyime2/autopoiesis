"""Earnings-release dates and point-in-time annual fundamentals from the SEC's public APIs.

For each ticker: every 8-K carrying Item 2.02 (results of operations) with its acceptance time, and the
XBRL facts we need, each with the date it was FILED - so a value is only used from the day it was public.
Polite: identified User-Agent, about 6 requests a second, one retry.
Usage: fetch_sec.py OUT.pkl CIK_MAP.json
CIK_MAP.json maps ticker to CIK (the S&P 500 constituents table on Wikipedia carries both); www.sec.gov's own
ticker file refuses an anonymous client, and only data.sec.gov is used here."""
import json, sys, time
import requests
UA = {"User-Agent": "autopoiesis-research paper-trading study (open-source, non-commercial)", "Accept-Encoding": "gzip, deflate"}
TAGS = {"Assets": "us-gaap", "GrossProfit": "us-gaap", "NetIncomeLoss": "us-gaap", "StockholdersEquity": "us-gaap",
        "EntityCommonStockSharesOutstanding": "dei", "WeightedAverageNumberOfDilutedSharesOutstanding": "us-gaap"}


def get(url):
    for attempt in range(2):
        time.sleep(0.17)
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200: return r.json()
        time.sleep(2)
    return None


cik_of = json.load(open(sys.argv[2]))
out = {}
for t, cik in cik_of.items():
    sub = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    events = []
    pages = [sub["filings"]["recent"]] if sub else []
    for f in (sub or {}).get("filings", {}).get("files", []):
        extra = get(f"https://data.sec.gov/submissions/{f['name']}")
        if extra: pages.append(extra)
    for p in pages:
        for form, items, acc in zip(p["form"], p["items"], p["acceptanceDateTime"]):
            if form == "8-K" and "2.02" in (items or ""): events.append(acc)
    facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    rows = {}
    for tag, tax in TAGS.items():
        units = ((facts or {}).get("facts", {}).get(tax, {}).get(tag, {}) or {}).get("units", {})
        vals = units.get("USD") or units.get("shares") or []
        rows[tag] = [(v["end"], v.get("start"), v["val"], v["filed"], v.get("form"), v.get("fp")) for v in vals if v.get("form") in ("10-K", "10-Q")]
    out[t] = dict(cik=cik, earnings=sorted(set(events)), facts=rows)
    print(t, cik, len(events), "earnings 8-Ks;", {k: len(v) for k, v in rows.items()}, flush=True)
import pickle
pickle.dump(out, open(sys.argv[1], "wb")); print("saved", len(out))
