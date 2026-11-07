"""G4 v3: a core sleeve plus long/flat ETF satellites, replayed through the real Guardian.

Research only. Registered design: ``configs/experiment_portfolio_v3.spec``. Every arm and
control uses the same sizing rule (`TargetEngine`) so that what differs between arms is
*when* a satellite is long, never how much. Each symbol is its own sub-book run by
`experiment.run_arm` (production Guardian, next-open fills, explicit costs); the
portfolio is the sum of the sub-books, and the account limits are checked on that sum.

Signals see only closes they have already been shown. The walk-forward selector
(`WalkForwardSignal`) fits on bars that end `embargo` bars before the refit, so the last
labels it scores never overlap the period it is about to trade.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from autopoiesis.models import DataSnapshot, TradeDecision

#: A signal maps the closes seen so far (oldest first) to "should the book be long now".
Signal = Callable[[Sequence[float]], bool]


# ----------------------------------------------------------------------------- signals
def sma(values: Sequence[float], n: int) -> float:
    return sum(values[-n:]) / n


@dataclass
class SmaBand:
    """Long above SMA(fast)*(1+band), flat below SMA(fast)*(1-band), unchanged between."""

    fast: int
    band: float = 0.0
    _state: bool = field(default=False, init=False, repr=False)

    def __call__(self, closes: Sequence[float]) -> bool:
        if len(closes) < self.fast:
            return False
        level = sma(closes, self.fast)
        if closes[-1] > level * (1 + self.band):
            self._state = True
        elif closes[-1] < level * (1 - self.band):
            self._state = False
        return self._state

    def fresh(self) -> SmaBand:
        return SmaBand(self.fast, self.band)

    @property
    def name(self) -> str:
        return f"sma{self.fast}_band{self.band:g}"


@dataclass
class SmaCross:
    fast: int
    slow: int

    def __call__(self, closes: Sequence[float]) -> bool:
        if len(closes) < self.slow:
            return False
        return sma(closes, self.fast) > sma(closes, self.slow)

    def fresh(self) -> SmaCross:
        return SmaCross(self.fast, self.slow)

    @property
    def name(self) -> str:
        return f"cross{self.fast}_{self.slow}"


@dataclass
class Momentum:
    lookback: int

    def __call__(self, closes: Sequence[float]) -> bool:
        if len(closes) <= self.lookback:
            return False
        return closes[-1] > closes[-1 - self.lookback]

    def fresh(self) -> Momentum:
        return Momentum(self.lookback)

    @property
    def name(self) -> str:
        return f"mom{self.lookback}"


def search_grid() -> list:
    """The 17 registered configurations of arm C."""
    grid: list = [SmaBand(f, b) for f in (10, 20, 50, 100, 200) for b in (0.0, 0.01)]
    grid += [SmaCross(10, 50), SmaCross(20, 100), SmaCross(50, 200)]
    grid += [Momentum(n) for n in (20, 60, 126, 252)]
    return grid


def signal_path(signal_factory: Callable[[], Signal], closes: Sequence[float]) -> list[bool]:
    """The signal's state after each close, computed causally (index i uses closes[:i+1])."""
    signal = signal_factory()
    seen: list[float] = []
    out = []
    for close in closes:
        seen.append(close)
        out.append(bool(signal(seen)))
    return out


def held_returns(path: Sequence[bool], closes: Sequence[float], cost: float) -> list[float]:
    """Close-to-close returns of holding when the state says long, net of `cost` per switch.

    Return i is earned from close i to close i+1 by the state decided at close i.
    """
    out = []
    prev = False
    for i in range(len(closes) - 1):
        state = path[i]
        r = (closes[i + 1] / closes[i] - 1.0) if state else 0.0
        if state != prev:
            r -= cost
        out.append(r)
        prev = state
    return out


def _sharpe(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    m = sum(returns) / len(returns)
    v = sum((x - m) ** 2 for x in returns) / (len(returns) - 1)
    return m / math.sqrt(v) if v > 0 else 0.0


@dataclass
class WalkForwardSignal:
    """Arm C's selector: refit every `step` bars on the last `train` bars, minus an embargo.

    At a refit on close index t, each grid config's state path is computed on
    closes[: t - embargo + 1] and scored on the held returns of its last `train` labels; the
    best in-sample Sharpe wins and trades until the next refit. Before the first refit the
    book is flat. `choices` records (index, config name) for the report.
    """

    grid: list
    train: int = 504
    step: int = 126
    embargo: int = 5
    cost: float = 0.0003
    _active: object | None = field(default=None, init=False, repr=False)
    _next_fit: int = field(default=0, init=False, repr=False)
    choices: list[tuple[int, str]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._next_fit = self.train + self.embargo

    def __call__(self, closes: Sequence[float]) -> bool:
        t = len(closes) - 1
        if t >= self._next_fit:
            self._fit(closes, t)
            self._next_fit = t + self.step
        if self._active is None:
            return False
        return bool(self._active(closes))

    def _fit(self, closes: Sequence[float], t: int) -> None:
        visible = list(closes[: t - self.embargo + 1])
        best, best_score = None, -math.inf
        for config in self.grid:
            path = signal_path(config.fresh, visible)
            score = _sharpe(held_returns(path, visible, self.cost)[-self.train:])
            if score > best_score:
                best, best_score = config, score
        # A stateful config (SmaBand) must be warmed on everything visible so its state at t
        # is the one it would have reached trading live, not a cold start.
        active = best.fresh()
        signal_path(lambda: active, closes[:t])
        self._active = active
        self.choices.append((t, best.name))


@dataclass
class MarkovTiming:
    """Control: random long/flat timing with a set long fraction and mean long-run length.

    A two-state chain whose stationary long share is `long_fraction` and whose long runs
    average `mean_run` bars. It knows nothing about prices, so any return it earns is what
    being exposed that much, that often, earns by itself.
    """

    long_fraction: float
    mean_run: float
    seed: int = 0
    _state: bool = field(default=False, init=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self.long_fraction = min(max(self.long_fraction, 0.0), 1.0)
        self.mean_run = max(self.mean_run, 1.0)

    def __call__(self, closes: Sequence[float]) -> bool:
        f = self.long_fraction
        if f <= 0.0:
            return False
        if f >= 1.0:
            return True
        p_exit = 1.0 / self.mean_run
        # Stationarity: f * p_exit = (1 - f) * p_enter.
        p_enter = min(1.0, f * p_exit / (1.0 - f))
        u = self._rng.random()
        self._state = (u >= p_exit) if self._state else (u < p_enter)
        return self._state


@dataclass
class Reversed:
    inner: Signal

    def __call__(self, closes: Sequence[float]) -> bool:
        return not self.inner(closes)


@dataclass
class Shuffled:
    """Drift control: the rule run on a seeded shuffle of the closes seen so far."""

    inner_factory: Callable[[], Signal]
    seed: int = 0

    def __call__(self, closes: Sequence[float]) -> bool:
        history = list(closes)
        random.Random(self.seed * 1_000_003 + len(history)).shuffle(history)
        # The last element must stay the real close the decision is made at.
        history.append(closes[-1])
        return bool(self.inner_factory()(history))


# ----------------------------------------------------------------------------- engine
@dataclass
class TargetEngine:
    """Long/flat sizing shared by every satellite arm and control.

    Long: hold floor(0.98 * pool / price) shares. Flat: hold none. An order is sent only
    when the gap to the target is at least max(1, 10% of the target), so a moving price
    does not create a trade every bar.
    """

    signal: Signal
    pool: float
    strategy_id: str
    rebalance_band: float = 0.10
    closes: list[float] = field(default_factory=list, init=False, repr=False)
    states: list[bool] = field(default_factory=list, init=False, repr=False)

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        self.closes.append(snapshot.last_price)
        long = bool(self.signal(self.closes))
        self.states.append(long)
        held = sum(
            p.quantity for p in snapshot.positions if p.symbol.upper() == snapshot.symbol.upper()
        )
        # Sized from the sub-book's own equity, capped at the pool: sizing from the pool alone
        # left a book that had lost money unable to afford its target, and the Guardian refused
        # every later buy (smoke run 2026-11-07: 890 "insufficient buying power" on XLE, 2 fills).
        account = getattr(snapshot, "account", None)
        capital = min(self.pool, account.equity) if account is not None else self.pool
        target = int(0.98 * capital // snapshot.last_price) if long else 0
        gap = target - held
        if abs(gap) < max(1, self.rebalance_band * target) or gap == 0:
            return self._decision(snapshot, "HOLD", 0, "at target")
        if gap > 0:
            return self._decision(snapshot, "BUY", int(gap), f"long: {held} -> {target}")
        return self._decision(snapshot, "SELL", int(-gap), f"{'long' if long else 'flat'}: {held} -> {target}")

    def _decision(self, snapshot: DataSnapshot, action: str, qty: int, why: str) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol, action=action, quantity=qty,
            confidence=0.6 if action != "HOLD" else 0.0, rationale=why, strategy_id=self.strategy_id,
        )


@dataclass
class CashEngine:
    """A satellite that holds cash (arm A's satellites)."""

    strategy_id: str = "cash"

    def __call__(self, snapshot: DataSnapshot) -> TradeDecision:
        return TradeDecision(
            symbol=snapshot.symbol, action="HOLD", quantity=0, confidence=0.0,
            rationale="arm A: satellite holds cash", strategy_id=self.strategy_id,
        )


# ----------------------------------------------------------------------------- portfolio
def sum_curves(curves: Sequence[Sequence[float]]) -> list[float]:
    n = min(len(c) for c in curves)
    return [sum(c[i] for c in curves) for i in range(n)]


def long_runs(path: Sequence[bool]) -> tuple[float, float]:
    """(share of bars long, mean length of a long run) for a state path."""
    if not path:
        return 0.0, 1.0
    runs, current = [], 0
    for state in path:
        if state:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    share = sum(path) / len(path)
    return share, (sum(runs) / len(runs) if runs else 1.0)


def held_shares_by_bar(outcome, bars: Sequence) -> list[float]:
    """Shares held at each bar's close, rebuilt from the sub-book's fills (fills land at the next open)."""
    by_ts: dict[str, float] = {}
    for fill in outcome.fills:
        signed = fill["quantity"] if fill["action"] in {"BUY", "COVER"} else -fill["quantity"]
        by_ts[fill["fill_timestamp"]] = by_ts.get(fill["fill_timestamp"], 0.0) + signed
    held, out = 0.0, []
    for bar in bars:
        held += by_ts.get(bar.timestamp.isoformat(), 0.0)
        out.append(held)
    return out


def deployed_fraction(outcome, bars: Sequence, pool: float) -> float:
    """Average share of `pool` held in the symbol, at closes."""
    shares = held_shares_by_bar(outcome, bars)
    values = [q * bar.close / pool for q, bar in zip(shares, bars, strict=False)]
    return sum(values) / len(values) if values else 0.0


def portfolio_invariants(outcomes: Sequence, *, max_gross: float, max_daily_loss: float) -> dict:
    """Account limits checked on the sum of sub-books (each sub-book saw only its own)."""
    equity = sum_curves([o.equity_curve for o in outcomes])
    worst_day = 0.0
    for i in range(1, len(equity)):
        worst_day = min(worst_day, equity[i] - equity[i - 1])
    gross = sum(o.max_gross_exposure for o in outcomes)
    min_cash = min(o.min_cash for o in outcomes)
    return {
        "sum_of_peak_gross_exposure": round(gross, 2),
        "gross_limit": max_gross,
        "gross_within_limit": gross <= max_gross,
        "min_sub_book_cash": round(min_cash, 2),
        "cash_never_negative": min_cash >= 0,
        "worst_day_pnl": round(worst_day, 2),
        "daily_loss_limit": max_daily_loss,
        "worst_day_within_limit": -worst_day <= max_daily_loss,
    }
