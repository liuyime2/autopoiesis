from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from typing import cast

from autopoiesis.models import (
    AccountSnapshot,
    DataSnapshot,
    InstrumentProfile,
    NewsItem,
    OpenOrderSnapshot,
    PositionSnapshot,
    Side,
)

#: References an instrument's behaviour is measured against. Four, covering the three things a
#: broad equity index is not: duration bonds, commodities and a second equity index with a
#: different weighting. The choice is stated so it can be argued with.
BEHAVIOUR_REFERENCES = ("SPY", "QQQ", "TLT", "GLD")
#: Days of history behind `beta_spy` and the correlations. Long enough that one earnings gap does
#: not set the flag, short enough that a two-year-old product still has a profile.
BEHAVIOUR_BARS = 120
#: Below this many days an instrument is unseasoned and its flags are suppressed rather than
#: computed from a handful of bars.
MIN_BEHAVIOUR_BARS = 40


class BrokerDataUnavailable(RuntimeError):
    """Broker state could not be read.

    Raised instead of degrading to an empty result. A risk check that reads
    "no positions" while the API is down is unsound: it silently disables the
    SELL guard, the conflicting-open-order guard, and the aggregate exposure
    cap. Degrading is how this gateway hid a wrong client method name for
    thirteen days and 851 cycles.
    """


class AlpacaDataGateway:
    """Reads real broker state.

    Every value here comes from the broker or the call fails loudly. There are
    no synthetic or placeholder values on this path (AGENTS.md 2).
    """

    def __init__(self, *, client):
        self.client = client
        #: Why the last `daily_closes` call returned nothing (None while it has not failed).
        self.last_daily_error: str | None = None
        self.last_universe_error: str | None = None
        #: Why the last `instrument_profile` or `recent_news` call returned nothing. An instrument
        #: that cannot be named is not the same as an ordinary one, so the reason is kept where the
        #: caller can report it.
        self.last_profile_error: str | None = None
        self.last_news_error: str | None = None
        #: Bars fetched in this process, keyed (symbol, days, adjustment), so a round covering ten
        #: symbols asks the broker once per symbol rather than once per symbol per feature.
        self._bars_cache: dict[tuple[str, int, str], list[dict]] = {}
        self._bars_cache_day: date | None = None
        #: Per-day profile and news cache, the same discipline `RiskJudgment` uses.
        self._profile_cache: dict[str, InstrumentProfile] = {}
        self._news_cache: dict[str, tuple[NewsItem, ...]] = {}

    def snapshot(self, symbol: str) -> DataSnapshot:
        symbol = symbol.upper()
        clock = self.client.get_clock()
        account = self.client.get_account()
        last_price = self._latest_price(symbol)

        equity = float(account.equity)
        portfolio_value = float(account.portfolio_value)
        day_start_equity = self._safe_float(account, "last_equity")
        day_start_equity_known = day_start_equity is not None and day_start_equity > 0
        # The `..._known` flag cannot narrow `day_start_equity` for the arithmetic, so
        # the check is repeated here rather than inferred. One source of truth for the
        # flag, one visible guard for the subtraction.
        daily_loss = (
            max(0.0, day_start_equity - equity) if day_start_equity is not None and day_start_equity > 0 else 0.0
        )

        timestamp = getattr(clock, "timestamp", None) or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        return DataSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            market_open=bool(clock.is_open),
            last_price=last_price,
            source="alpaca",
            account=AccountSnapshot(
                equity=equity,
                cash=float(account.cash),
                buying_power=float(account.buying_power),
                portfolio_value=portfolio_value,
                daily_loss=daily_loss,
                day_start_equity_known=day_start_equity_known,
            ),
            day_start_equity=day_start_equity,
            positions=self._fetch_positions(),
            open_orders=self._fetch_open_orders(),
        )

    #: Exchanges whose listings count as a normal US stock. OTC is a different market.
    MAJOR_EXCHANGES = frozenset({"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"})

    def tradable_symbols(self) -> frozenset[str] | None:
        """Active, tradable US equities on a major exchange, cached for the day. `None` on failure
        (the Guardian then refuses rather than guess). Read-only."""
        from datetime import date

        today = date.today()
        cached = getattr(self, "_tradable_cache", None)
        if cached and cached[0] == today:
            return cached[1]
        try:
            assets = self.client.list_assets(status="active", asset_class="us_equity")
            symbols = frozenset(
                str(a.symbol).upper() for a in assets
                if getattr(a, "tradable", False) and str(getattr(a, "exchange", "")).upper() in self.MAJOR_EXCHANGES
            )
        except Exception as exc:
            self.last_universe_error = f"{type(exc).__name__}: {exc}"
            return cached[1] if cached else None  # yesterday's list beats no list
        self._tradable_cache = (today, symbols)
        return symbols

    def held_symbols(self) -> list[str]:
        """Every symbol the account holds a non-zero position in, sorted. Read-only."""
        return sorted({p.symbol for p in self._fetch_positions() if p.quantity != 0})

    def attention_symbols(self, k: int, *, min_price: float = 5.0, exclude: frozenset[str] = frozenset()) -> list[str]:
        """Up to `k` symbols worth a look this round: the broker's most active by dollar volume,
        after quality filters (a major exchange, priced at least `min_price`, not a warrant, unit
        or right). The raw list is mostly penny stocks and SPAC paper. Read-only; `[]` on failure."""
        if k <= 0:
            return []
        try:
            raw = self.client.data_get("/screener/stocks/most-actives", {"by": "volume", "top": 60}, api_version="v1beta1")
            names = [str(x["symbol"]).upper() for x in raw.get("most_actives", [])]
            allowed = self.tradable_symbols()
            movers = self.client.data_get("/screener/stocks/movers", {"top": 40}, api_version="v1beta1")
            names += [str(x["symbol"]).upper() for x in movers.get("gainers", []) + movers.get("losers", [])
                      if float(x.get("price", 0) or 0) >= min_price]
        except Exception as exc:
            self.last_universe_error = f"{type(exc).__name__}: {exc}"
            return []
        candidates: list[str] = []
        for s in names:
            if s in exclude or s in candidates or not (1 <= len(s) <= 5) or not s.isalpha():
                continue
            if len(s) == 5 and s[-1] in "WUR":  # warrants, units, rights
                continue
            if allowed is not None and s not in allowed:
                continue
            candidates.append(s)
        if not candidates:
            return []
        try:  # the most-active list carries no price; one batched call removes the penny stocks
            trades = self.client.get_latest_trades(candidates[:80])
            price = {s: float(getattr(trades.get(s), "price", 0) or getattr(trades.get(s), "p", 0) or 0) for s in candidates[:80]}
        except Exception as exc:
            self.last_universe_error = f"{type(exc).__name__}: {exc}"
            return []
        return [s for s in candidates if price.get(s, 0.0) >= min_price][:k]

    def _bars(self, symbol: str, days: int, *, adjustment: str) -> list[dict]:
        """The broker's bars for one symbol, fetched at most once per symbol per day.

        `daily_closes`, `instrument_profile` and `recent_news`-adjacent behaviour all need the same
        series. Fetching it three times for the same symbol is how a ten-symbol round becomes thirty
        broker calls for no new information, so the first caller pays and the rest reuse. A new day
        invalidates it, because the latest bar moves overnight.
        """
        symbol = symbol.upper()
        today = date.today()
        if self._bars_cache_day != today:
            self._bars_cache.clear()
            self._bars_cache_day = today
        key = (symbol, days, adjustment)
        if key in self._bars_cache:
            return self._bars_cache[key]
        try:
            end = (today - timedelta(days=1)).isoformat()
            start = (today - timedelta(days=int(days * 1.5))).isoformat()
            frame = self.client.get_bars(symbol, "1Day", start, end, adjustment=adjustment).df
            bars = [
                {
                    "t": index.isoformat(),
                    "o": float(row["open"]),
                    "h": float(row["high"]),
                    "l": float(row["low"]),
                    "c": float(row["close"]),
                    "v": float(row["volume"]),
                }
                for index, row in frame.iterrows()
            ]
        except Exception as exc:
            # Recorded on the channel the caller is already watching, so one failure does not
            # silently empty a different feature.
            self.last_daily_error = f"{type(exc).__name__}: {exc}"
            bars = []
        self._bars_cache[key] = bars
        return bars

    def daily_closes(self, symbol: str, days: int = 2800) -> list[float] | None:
        """Split- and dividend-adjusted daily closes, oldest first, ending yesterday. Read-only.

        Adjusted so a split is not a crash: a volatility forecast fed a raw 2:1 split would see a
        50% one-day move. `None` when the broker returns nothing or the call fails.
        """
        bars = self._bars(symbol, days, adjustment="all")
        closes = [b["c"] for b in bars if b["c"] > 0]
        return closes[-days:] if closes else None

    def instrument_profile(self, symbol: str, *, bars: int = BEHAVIOUR_BARS) -> InstrumentProfile | None:
        """What this instrument is, and what its own tape says it does. Read-only.

        The first half is the broker's asset record. `GET /v2/assets/{symbol}` returns the full
        name — "Direxion Daily Semiconductor Bear 3X ETF", "Intel Corporation Common Stock" — which
        is the answer to "what am I being asked about" stated by the broker rather than guessed from
        a ticker. Nothing here blocks a product class; it names it.

        The second half is measured from the instrument's own unadjusted bars against four
        references. This is the part that survives a stale or silent name: a 3x inverse fund has
        `corr_spy` near -1 and `|beta_spy|` far from 1 whether or not its name says so. Every
        `flags` entry carries the number it came from so a reader can disagree.

        Never raises. Returns None when the broker cannot name the symbol at all, and records why
        on `last_profile_error` — an instrument that cannot be named must not be traded on a guess.
        """
        symbol = symbol.upper()
        cached = self._profile_cache.get(symbol)
        if cached is not None or self.last_profile_error:
            return cached
        self.last_profile_error = None
        try:
            asset = self._asset_record(symbol)
        except Exception as exc:
            self.last_profile_error = f"{type(exc).__name__}: {exc}"
            return None
        if asset is None:
            self.last_profile_error = f"broker returned no asset record for {symbol}"
            return None

        raw = self._bars(symbol, bars + 10, adjustment="raw")
        profile_kwargs = self._behaviour(bars, raw)
        profile = InstrumentProfile(symbol=symbol, **asset, **profile_kwargs)
        self._profile_cache[symbol] = profile
        return profile

    def _asset_record(self, symbol: str) -> dict | None:
        """The broker's asset record for one symbol, or None. Fields absent from the record stay
        absent rather than being defaulted into a claim the broker never made."""
        asset = self.client.get_asset(symbol)
        if asset is None:
            return None
        out: dict = {}
        for field, attr in (
            ("name", "name"),
            ("asset_class", "class"),
            ("exchange", "exchange"),
            ("status", "status"),
        ):
            value = getattr(asset, attr, None)
            if value not in (None, ""):
                out[field] = str(value)
        # An instrument the broker cannot name is not tradeable here: a symbol with no name would
        # otherwise reach the decision context with an identity of empty strings, which reads as
        # "nothing to say about this" rather than "we do not know what this is".
        if not out.get("name"):
            return None
        for field in ("tradable", "marginable", "shortable", "easy_to_borrow", "fractionable"):
            value = getattr(asset, field, None)
            if isinstance(value, bool):
                out[field] = value
        return out

    def _behaviour(self, bars: int, raw: list[dict]) -> dict:
        """Measured behaviour from the instrument's own bars: volatility, dollar volume, and
        correlation and beta against `BEHAVIOUR_REFERENCES`.

        Unadjusted bars on purpose. An adjusted close has a dividend drip folded into it, so a bond
        or high-yield ETF would measure as calmer than it is; the measurement is about how the
        instrument moves, not about what it returned.
        """
        if len(raw) < MIN_BEHAVIOUR_BARS:
            return {"bars_used": len(raw), "flags": ()} if raw else {"bars_used": 0, "flags": ()}
        window = raw[-(bars + 1) :]
        closes = [b["c"] for b in window if b["c"] > 0]
        rets = [b / a - 1.0 for a, b in pairwise(closes)]
        if len(rets) < MIN_BEHAVIOUR_BARS - 1:
            return {"bars_used": len(window), "flags": ()}
        dollars = [b["c"] * b["v"] for b in window[1:] if b["c"] > 0 and b["v"] > 0]
        out: dict = {
            "bars_used": len(window),
            "last_price": closes[-1] if closes else None,
            "annualized_vol_pct": round(_vol_pct(rets), 2),
            "avg_dollar_volume": round(sum(dollars) / len(dollars), 2) if dollars else None,
        }
        own = rets
        for ref in BEHAVIOUR_REFERENCES:
            ref_closes = [b["c"] for b in self._bars(ref, bars + 10, adjustment="raw") if b["c"] > 0]
            ref_rets = [b / a - 1.0 for a, b in pairwise(ref_closes)]
            m = min(len(own), len(ref_rets))
            if m < MIN_BEHAVIOUR_BARS:
                continue
            a, b_ = own[-m:], ref_rets[-m:]
            r = _pearson(a, b_)
            if r is None:
                continue
            if ref == "SPY":
                out["beta_spy"] = round(_beta(a, b_), 3)
                out["corr_spy"] = round(r, 3)
                out["vol_ratio_spy"] = round((_vol_pct(a) / _vol_pct(b_)), 2) if _vol_pct(b_) > 0 else None
            else:
                out[f"corr_{ref.lower()}"] = round(r, 3)
        flags = _instrument_flags(out)
        if flags:
            out["flags"] = tuple(flags)
        return out

    def recent_news(self, symbol: str, limit: int = 5) -> tuple[NewsItem, ...]:
        """The broker's most recent headlines for a symbol, newest first. Read-only.

        Present so the agent is not blind to what is published about the instrument it is being
        asked about — which, under a widened universe, is a stock it has never seen before. The
        `decision context` labels what these are: the same session's research measured every
        reader's next-day tradable correlation at within 0.03 of zero, so a headline is context, not
        a signal, and the instruction says so.

        Empty on failure, with the reason on `last_news_error`. Never raises.
        """
        symbol = symbol.upper()
        cached = self._news_cache.get(symbol)
        if cached is not None:
            return cached
        self.last_news_error = None
        try:
            raw = self.client.data_get(
                "/news", {"symbols": symbol, "limit": int(limit)}, api_version="v1beta1"
            )
        except Exception as exc:
            self.last_news_error = f"{type(exc).__name__}: {exc}"
            return ()
        items_raw = (raw or {}).get("news") if isinstance(raw, dict) else None
        if not isinstance(items_raw, list):
            self.last_news_error = f"broker returned no news list for {symbol}"
            return ()
        out: list[NewsItem] = []
        for item in items_raw[:limit]:
            if not isinstance(item, dict):
                continue
            headline = str(item.get("headline") or "").strip()
            if not headline:
                continue
            symbols = item.get("symbols") or [symbol]
            created = None
            if item.get("created_at"):
                try:
                    created = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
                except ValueError:
                    created = None
            out.append(
                NewsItem(
                    symbol=str(symbols[0]).upper(),
                    headline=headline[:200],
                    source=str(item.get("source") or item.get("author") or "alpaca_news")[:60],
                    created_at=created,
                    summary=str(item.get("summary") or "").strip()[:200],
                )
            )
        self._news_cache[symbol] = tuple(out)
        return tuple(out)

    def _latest_price(self, symbol: str) -> float:
        trade = self.client.get_latest_trade(symbol)
        price = getattr(trade, "price", None) or getattr(trade, "p", None)
        if price is None:
            raise BrokerDataUnavailable(f"Alpaca returned no latest trade price for {symbol}")
        return float(price)

    def _safe_float(self, obj, attr: str) -> float | None:
        value = getattr(obj, attr, None)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _fetch_positions(self) -> tuple[PositionSnapshot, ...]:
        try:
            raw_positions = self.client.list_positions()
        except Exception as exc:
            raise BrokerDataUnavailable(f"could not read broker positions: {exc}") from exc
        return tuple(
            PositionSnapshot(
                symbol=str(_required(getattr(pos, "symbol", None), "position", "symbol")),
                quantity=_required_float(getattr(pos, "qty", None), "position", "qty"),
                market_value=_required_float(getattr(pos, "market_value", None), "position", "market_value"),
            )
            for pos in raw_positions
        )

    def _fetch_open_orders(self) -> tuple[OpenOrderSnapshot, ...]:
        try:
            raw_orders = self.client.list_orders(status="open")
        except Exception as exc:
            raise BrokerDataUnavailable(f"could not read broker open orders: {exc}") from exc
        return tuple(
            OpenOrderSnapshot(
                symbol=str(_required(getattr(order, "symbol", None), "open order", "symbol")),
                # Alpaca returns side lowercase; OpenOrderSnapshot is Literal["BUY","SELL"].
                # Without this normalisation every order is dropped as invalid.
                side=cast("Side", str(_required(getattr(order, "side", None), "open order", "side")).upper()),
                quantity=_required_float(getattr(order, "qty", None), "open order", "qty"),
                status=str(_required(getattr(order, "status", None), "open order", "status")),
            )
            for order in raw_orders
        )


def _vol_pct(rets: list[float]) -> float:
    """Annualised volatility in percent from returns."""
    n = len(rets)
    if n < 3:
        return 0.0
    mean = sum(rets) / n
    var = sum((x - mean) ** 2 for x in rets) / (n - 1)
    return (var**0.5) * (252**0.5) * 100.0


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3 or len(y) != n:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (sxx * syy) ** 0.5


def _beta(x: list[float], y: list[float]) -> float:
    """x on y: how much x moves per unit of y. Meaningless without a correlation beside it, which
    is why `_instrument_flags` never reads it on its own."""
    n = len(x)
    if n < 3:
        return 0.0
    my = sum(y) / n
    syy = sum((b - my) ** 2 for b in y)
    if syy <= 0:
        return 0.0
    return sum((a - sum(x) / n) * (b - my) for a, b in zip(x, y, strict=True)) / syy


def _instrument_flags(m: dict) -> list[str]:
    """Derived labels, each carrying the number behind it.

    Everything is suppressed when the window is too short to measure, because a flag computed from
    thirty bars of a new listing is a guess wearing a measurement's clothes.

    Two design choices worth naming. **Correlation to the index alone does not detect leverage**: a
    3x fund whose underlying is not the index (SOXS tracks semiconductors, not the S&P) measures
    near-zero correlation against SPY while moving several times as far, so a correlation-only test
    calls it nothing at all. Measured on this broker: SOXS over 120 days is corr -0.04 with a beta
    of -4.7. So the volatility ratio is the test that catches it. And the flags are a *description*
    of what was measured, never a reason to admit or refuse an instrument - the broker's own `name`
    field is the authoritative answer to what something is, and this exists to catch a name that is
    stale, wrong or silent.
    """
    if m.get("bars_used", 0) < MIN_BEHAVIOUR_BARS:
        return []
    flags: list[str] = []
    vol = m.get("annualized_vol_pct")
    ratio = m.get("vol_ratio_spy")
    if vol is not None and vol >= 60.0:
        flags.append(f"high_volatility: {vol:.0f}% annualised over {m['bars_used']} days")
    if ratio is not None and ratio >= 2.0:
        flags.append(f"moves_more_than_the_index: {ratio:.1f}x the index's daily volatility")
    corr = m.get("corr_spy")
    if corr is not None and corr <= -0.6:
        flags.append(f"moves_against_equities: corr SPY {corr:+.2f}")
    if corr is not None and abs(corr) < 0.4:
        flags.append(f"does_not_track_the_index: corr SPY {corr:+.2f}")
    for ref, key in (("bonds", "corr_tlt"), ("commodities", "corr_gld")):
        r = m.get(key)
        if r is not None and r >= 0.6 and (corr is None or abs(corr) < r):
            flags.append(f"tracks_{ref}_more_than_equities: corr {r:+.2f} vs SPY {(corr or 0.0):+.2f}")
    return flags


def _required(value, context: str, attr: str) -> str:
    if value is None or value == "":
        raise BrokerDataUnavailable(f"broker {context} is missing {attr!r}")
    return value


def _required_float(value, context: str, attr: str) -> float:
    if value is None:
        raise BrokerDataUnavailable(f"broker {context} is missing {attr!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BrokerDataUnavailable(f"broker {context} has non-numeric {attr!r}: {value!r}") from exc
