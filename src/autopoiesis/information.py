"""Read-only information layer for the trading loop.

The account holder's instruction (2026-11-04): combine more and more complex
information - news, quantitative levels, earnings, everything obtainable that can
matter - into the judgment. This module is the *read* side of that: it turns a
cached per-symbol JSON file (written by `tools/refresh_information.py`) into a
bounded text block that the decision engine can attach to a cycle's context.

Design constraints, all enforced here:

- It never raises. A missing or corrupt cache, a stale field, a malformed
  number - all of them degrade to "no information", never to an exception. A
  diagnostic that can crash the trading loop is worse than no diagnostic.
- It drops fields older than their budget. Stale information is worse than
  none, because the model cannot tell the difference.
- It caps the block at ~700 characters. The context already carries news,
  instrument and risk; the information block is an addition, not a replacement.
- It derives `earnings_in_days`, `earnings_imminent`, `ex_dividend_in_days`
  and `iv_over_realized` so the prompt can state them directly rather than
  making the model compute them.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

#: Per-field freshness budgets, in days. A field older than its budget is dropped.
FRESHNESS_BUDGETS: dict[str, int] = {
    "earnings_date": 30,
    "earnings_consensus_eps": 30,
    "valuation": 7,
    "growth": 7,
    "margins": 7,
    "analyst_target": 7,
    "analyst_recommendation": 7,
    "dividends": 7,
    "news": 2,
    "options_iv": 2,
}

#: Hard cap on the rendered block.
BLOCK_CAP = 700

#: Earnings within this many days count as "imminent".
EARNINGS_IMMINENT_DAYS = 5


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            except ValueError:
                return None
    return None


def _age_days(as_of: datetime | None, field_as_of: datetime | None) -> int | None:
    if as_of is None or field_as_of is None:
        return None
    try:
        return (as_of - field_as_of).days
    except (TypeError, ValueError):
        return None


class InformationProvider:
    """Read-only provider over a directory of per-symbol JSON caches."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)

    def _load(self, symbol: str) -> dict[str, Any] | None:
        path = self.cache_dir / f"{symbol.upper()}.json"
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def block(self, symbol: str, *, cutoff: datetime | None = None) -> str | None:
        """Render a bounded information block for `symbol`, or None when empty.

        `cutoff` enables point-in-time use: a field (or the whole cache) whose
        `available_at` is after `cutoff` is dropped, so historical replay never
        sees information that was not yet public.
        """
        try:
            data = self._load(symbol)
        except Exception:
            return None
        if not data:
            return None

        as_of_raw = data.get("as_of")
        try:
            as_of = datetime.fromisoformat(str(as_of_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            as_of = datetime.now(timezone.utc)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)

        # The reference time is the decision's, never the cache's own. Age used to be
        # `as_of - field_as_of`, both written at refresh time, so a month-old file read as
        # zero days old and nothing was ever dropped; the earnings countdown had the same
        # flaw (measured 2026-11-07 on the code of 41886bb).
        reference = cutoff if cutoff is not None else datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        # Point-in-time: if the whole cache was not yet available, drop it.
        if cutoff is not None:
            avail_raw = data.get("available_at")
            if avail_raw is not None:
                try:
                    avail = datetime.fromisoformat(str(avail_raw).replace("Z", "+00:00"))
                    if avail.tzinfo is None:
                        avail = avail.replace(tzinfo=timezone.utc)
                    if avail > cutoff:
                        return None
                except (ValueError, TypeError):
                    pass

        # Freshness filter: drop fields older than their budget.
        fresh: dict[str, Any] = {}
        for key, budget in FRESHNESS_BUDGETS.items():
            if key not in data:
                continue
            field_as_of = None
            fa = data.get(f"{key}_as_of")
            if fa is not None:
                try:
                    field_as_of = datetime.fromisoformat(str(fa).replace("Z", "+00:00"))
                    if field_as_of.tzinfo is None:
                        field_as_of = field_as_of.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    field_as_of = as_of
            else:
                field_as_of = as_of
            # Point-in-time: drop the field if it was not yet public.
            if cutoff is not None:
                favail = data.get(f"{key}_available_at", data.get("available_at"))
                if favail is not None:
                    try:
                        fav = datetime.fromisoformat(str(favail).replace("Z", "+00:00"))
                        if fav.tzinfo is None:
                            fav = fav.replace(tzinfo=timezone.utc)
                        if fav > cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
            # A field observed after the decision time did not exist at that time.
            if cutoff is not None and field_as_of is not None and field_as_of > reference:
                continue
            age = _age_days(reference, field_as_of)
            if age is not None and age > budget:
                continue
            fresh[key] = data[key]

        # Derive convenience fields.
        earnings_date = _parse_date(fresh.get("earnings_date"))
        today = reference.date()
        if earnings_date is not None:
            fresh["earnings_in_days"] = (earnings_date - today).days
            fresh["earnings_imminent"] = 0 <= (earnings_date - today).days <= EARNINGS_IMMINENT_DAYS

        ex_div = _parse_date(fresh.get("ex_dividend_date"))
        if ex_div is not None:
            fresh["ex_dividend_in_days"] = (ex_div - today).days

        iv = fresh.get("options_iv")
        rv = fresh.get("realized_vol")
        if isinstance(iv, (int, float)) and isinstance(rv, (int, float)) and rv:
            fresh["iv_over_realized"] = round(float(iv) / float(rv), 2)

        # Render.
        lines: list[str] = []
        if "earnings_date" in fresh:
            d = fresh["earnings_date"]
            extra = ""
            if fresh.get("earnings_consensus_eps") is not None:
                extra = f" (consensus EPS {fresh['earnings_consensus_eps']})"
            imminent = " [IMMINENT]" if fresh.get("earnings_imminent") else ""
            lines.append(f"earnings: {d}{extra}{imminent}")
        if "earnings_in_days" in fresh and "earnings_date" not in fresh:
            lines.append(f"earnings in {fresh['earnings_in_days']}d")

        val = fresh.get("valuation")
        if isinstance(val, dict):
            parts = []
            for k in ("pe", "forward_pe", "peg", "price_to_book"):
                if val.get(k) is not None:
                    parts.append(f"{k}={val[k]}")
            if parts:
                lines.append("valuation: " + ", ".join(parts))

        growth = fresh.get("growth")
        if isinstance(growth, dict):
            parts = []
            for k in ("revenue_growth", "earnings_growth"):
                if growth.get(k) is not None:
                    parts.append(f"{k}={growth[k]}")
            if parts:
                lines.append("growth: " + ", ".join(parts))

        margins = fresh.get("margins")
        if isinstance(margins, dict):
            parts = []
            for k in ("gross", "operating", "net"):
                if margins.get(k) is not None:
                    parts.append(f"{k}={margins[k]}")
            if parts:
                lines.append("margins: " + ", ".join(parts))

        at = fresh.get("analyst_target")
        ar = fresh.get("analyst_recommendation")
        if at is not None or ar is not None:
            parts = []
            if at is not None:
                parts.append(f"target={at}")
            if ar is not None:
                parts.append(f"rec={ar}")
            lines.append("analyst: " + ", ".join(parts))

        divs = fresh.get("dividends")
        if isinstance(divs, list) and divs:
            last = divs[-1]
            if isinstance(last, dict):
                ex = last.get("ex_date") or last.get("ex_dividend_date")
                amt = last.get("amount")
                bits = []
                if ex is not None:
                    bits.append(f"ex={ex}")
                if amt is not None:
                    bits.append(f"amt={amt}")
                if "ex_dividend_in_days" in fresh:
                    bits.append(f"in {fresh['ex_dividend_in_days']}d")
                if bits:
                    lines.append("dividend: " + ", ".join(bits))

        news = fresh.get("news")
        if isinstance(news, list) and news:
            headlines = [str(n.get("headline", "")).strip() for n in news if isinstance(n, dict)]
            headlines = [h for h in headlines if h][:3]
            if headlines:
                lines.append("news: " + " | ".join(headlines))

        if "options_iv" in fresh:
            iv_str = f"iv={fresh['options_iv']}"
            if "iv_over_realized" in fresh:
                iv_str += f" (iv/realized={fresh['iv_over_realized']})"
            lines.append("options: " + iv_str)

        block = "\n".join(lines).strip()
        if not block:
            return None
        if len(block) > BLOCK_CAP:
            block = block[:BLOCK_CAP].rsplit("\n", 1)[0]
        return block
