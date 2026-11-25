"""The v6 forward basket: hold the targets file's names equal weight inside a fixed pool.

Why it exists. v6 (docs/research/specs/v6-mom-top10.spec) holds the tenth of the top-500 US listed
equities with the strongest 12-1 momentum. Its first look was not PROVEN, and a one-year look
cannot prove a strategy of its information ratio (docs/evidence/xsection_v5/power.md). On
2026-11-24 the account holder authorised trading it on paper anyway, as evidence (real fills,
cost, slippage) rather than as a claim: docs/research/specs/v6-forward-paper.md.

Like the core sleeve it is an exception to the screen rule. A strategy that has not passed the
screen may not add, but the screen scores 24-hour direction and cannot grade a weekly basket. The
exception is narrow:
- it is off unless the env file names a pool;
- the pool can never exceed `BASKET_POOL_CAP`;
- every decision goes through Guardian, the executor and the journal like any other.

Nothing here sends an order. The selection is not computed here: production may not import the
research package. `tools/basket_targets.py` writes `targets-<session>.json` from the registered
selection code, and this module only validates and follows it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from autopoiesis.learning_evidence import capture_behavior
from autopoiesis.models import DataSnapshot, StrategyProvenance, TradeDecision

NEW_YORK = ZoneInfo("America/New_York")
BASKET_STRATEGY_ID = "v6-mom-top10"
#: The ceiling the account holder authorised on 2026-11-24. Raising it is a code change they make.
BASKET_POOL_CAP = 30_000.0
#: Basket symbols are looked at in the first rounds of each New York day only. Each round costs
#: one cycle a symbol out of `max_daily_cycles`, and fifty held names in every round would cut the
#: other strategies' cadence by about three times.
BASKET_ROUNDS_PER_DAY = 3
MAX_TARGETS = 60
#: A targets file older than this is stale: the weekly job has stopped. The basket then holds.
MAX_TARGET_AGE_DAYS = 10
#: Earlier files whose names the basket still manages, so a name dropped while (for example) the
#: daily-loss stop refused trading is still sold on a later day.
LOOKBACK_FILES = 4
#: Share of a name's budget its position may drift before it is traded.
BAND = 0.25
CONFIDENCE = 0.8
TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
FILE = re.compile(r"^targets-(\d{4}-\d{2}-\d{2})\.json$")


def body_sha256(body: Mapping[str, Any]) -> str:
    """Canonical JSON of every field but `sha256`; the writer stores it, the reader recomputes it."""
    return hashlib.sha256(json.dumps({k: v for k, v in body.items() if k != "sha256"},
                                     sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Targets:
    effective: date
    symbols: tuple[str, ...]
    path: str


@dataclass
class TargetState:
    current: Targets | None = None
    earlier: list[Targets] = field(default_factory=list)
    problem: str | None = None


def _read(path: Path, today: date) -> Targets:
    body = json.loads(path.read_text(encoding="utf-8"))
    effective = date.fromisoformat(str(body.get("effective")))
    symbols = body.get("symbols")
    if body.get("strategy_id") != BASKET_STRATEGY_ID:
        raise ValueError(f"strategy_id is {body.get('strategy_id')!r}")
    if body.get("sha256") != body_sha256(body):
        raise ValueError("sha256 does not match the body: the file was edited")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_TARGETS:
        raise ValueError(f"needs 1..{MAX_TARGETS} symbols")
    if len(set(symbols)) != len(symbols) or not all(isinstance(s, str) and TICKER.match(s) for s in symbols):
        raise ValueError("symbols must be unique, well-formed tickers")
    if effective > today:
        raise ValueError("not effective yet")
    return Targets(effective=effective, symbols=tuple(symbols), path=str(path))


def load_targets(directory: Path, today: date, excluded: Iterable[str] = ()) -> TargetState:
    """The newest targets effective by `today`, the earlier ones still managed, and any problem.

    A newest file that is invalid or stale leaves `current` None (the basket then holds); it does
    not fall back to an older file, which would trade last week's names as if they were this week's.
    """
    state = TargetState()
    dated = sorted(((date.fromisoformat(m.group(1)), p) for p in directory.glob("targets-*.json")
                    if (m := FILE.match(p.name))), reverse=True)
    dated = [(d, p) for d, p in dated if d <= today]
    if not dated:
        state.problem = f"no targets file effective by {today} in {directory}"
        return state
    skip = {s.upper() for s in excluded}
    for i, (_, path) in enumerate(dated[: LOOKBACK_FILES + 1]):
        try:
            t = _read(path, today)
        except (OSError, ValueError, TypeError) as exc:
            if i == 0:
                state.problem = f"{path.name}: {exc}"
            continue
        t = Targets(t.effective, tuple(s for s in t.symbols if s not in skip), t.path)
        if i == 0:
            if (today - t.effective).days > MAX_TARGET_AGE_DAYS:
                state.problem = f"{path.name} is {(today - t.effective).days} days old: stale"
                state.earlier.append(t)
            elif not t.symbols:
                state.problem = f"{path.name}: every symbol is excluded"
            else:
                state.current = t
        else:
            state.earlier.append(t)
    return state


def round_symbols(symbols: list[str], basket: BasketSleeve, round_index: int) -> list[str]:
    """`symbols` for round `round_index` of the New York day, adjusted for the basket.

    In the first `BASKET_ROUNDS_PER_DAY` rounds the current targets the round does not already hold
    are appended, so a new name is bought. Afterwards every owned name is left out: nothing else may
    trade it, and a rebalance does not need more passes than that (the executor refuses a second
    order while one is unresolved).
    """
    basket.refresh()
    if round_index < BASKET_ROUNDS_PER_DAY:
        current = basket.state.current.symbols if basket.state.current else ()
        seen = {s.upper() for s in symbols}
        return [*symbols, *(s for s in current if s not in seen)]
    return [s for s in symbols if s.upper() not in basket.owned]


class BasketSleeve:
    strategy_id = BASKET_STRATEGY_ID

    def __init__(
        self,
        *,
        pool: float,
        targets_dir: Path,
        excluded: Iterable[str] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(NEW_YORK),
        band: float = BAND,
    ):
        if not 0 < pool <= BASKET_POOL_CAP:
            raise ValueError(f"basket pool must be in (0, {BASKET_POOL_CAP:.0f}], got {pool}")
        self.pool = pool
        self.targets_dir = Path(targets_dir)
        self.excluded = frozenset(s.upper() for s in excluded)
        self.clock = clock
        self.band = band
        self._loaded_on: date | None = None
        self.state = TargetState()
        self.owned: frozenset[str] = frozenset()
        self.provenance: StrategyProvenance | None = None

    def refresh(self) -> None:
        """Reload the targets once per New York day (a new week's file takes effect on its day)."""
        today = self.clock().astimezone(NEW_YORK).date()
        if self._loaded_on == today:
            return
        self._loaded_on = today
        self.state = load_targets(self.targets_dir, today, self.excluded)
        names = set(self.state.current.symbols if self.state.current else ())
        for t in self.state.earlier:
            names.update(t.symbols)
        self.owned = frozenset(names)
        current = self.state.current
        #: The journal records this as the behaviour behind every basket decision. The verifier and
        #: attribution require the decision's symbol to be in the recorded universe.
        self.provenance = capture_behavior(
            BASKET_STRATEGY_ID, "FORWARD_BASKET", tuple(sorted(names)),
            {"pool": self.pool, "band": self.band, "targets": current.path if current else None,
             "effective": current.effective.isoformat() if current else None}, self.pool,
        ) if names else None

    def owns(self, symbol: str) -> bool:
        self.refresh()
        return symbol.upper() in self.owned

    def plan(self, snapshot: DataSnapshot) -> TradeDecision | None:
        """A trade toward the target for this snapshot's symbol, or None when there is nothing to do.

        The target is for the whole position in the symbol, whoever bought it (as for the core
        sleeve): `round(pool / names / price)` shares for a current name, zero for a name an earlier
        file held. No current file (missing, invalid, stale) means no trade at all.
        """
        self.refresh()
        symbol = snapshot.symbol.upper()
        current = self.state.current
        if current is None or symbol not in self.owned or not snapshot.market_open or snapshot.last_price <= 0:
            return None
        if any(order.symbol.upper() == symbol for order in snapshot.open_orders):
            return None
        held = max(0, math.floor(sum(p.quantity for p in snapshot.positions if p.symbol.upper() == symbol)))
        price = snapshot.last_price
        budget = self.pool / len(current.symbols)
        target = round(budget / price) if symbol in current.symbols else 0
        gap = target - held
        if gap == 0 or (target > 0 and abs(gap) * price <= self.band * budget):
            return None
        action: Literal["BUY", "SELL"] = "BUY" if gap > 0 else "SELL"
        why = (f"forward basket {current.effective}: {target} shares (${budget:,.0f} of a ${self.pool:,.0f} pool "
               f"over {len(current.symbols)} names), holding {held}" if target else
               f"forward basket {current.effective}: {symbol} left the targets, selling {held}")
        return TradeDecision(
            symbol=symbol,
            action=action,
            quantity=abs(gap),
            confidence=CONFIDENCE,
            rationale=why,
            strategy_id=BASKET_STRATEGY_ID,
            strategy_provenance=self.provenance,
            decision_source="policy_engine",
        )

    def hold(self, snapshot: DataSnapshot) -> TradeDecision:
        """The forced HOLD for an owned symbol with nothing to do, so no other strategy trades it."""
        problem = f"; {self.state.problem}" if self.state.problem else ""
        return TradeDecision(
            symbol=snapshot.symbol,
            action="HOLD",
            quantity=0,
            confidence=0.0,
            rationale=f"forward basket holds this symbol exclusively; nothing to trade this cycle{problem}",
            strategy_id=BASKET_STRATEGY_ID,
            strategy_provenance=self.provenance,
            decision_source="policy_engine",
        )
