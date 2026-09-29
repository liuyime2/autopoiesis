"""The stage that decides whether a candidate deserved to trade at all.

The objective's loop names `hypothesis -> candidate -> offline validation ->
shadow/probation -> live validation`. The middle stage did not exist: a candidate
went straight from admission into PROBATION and then traded real (paper) orders,
with the Guardian as the only gate.

The obvious way to build it - replay the strategy's own rule against historical
prices - would have been theatre, and it is worth recording why. In this system a
strategy is *not an executable rule*. ``parameters["action"]`` is never checked on
the execution path, and TREND_FOLLOW's ``reference_price``/``threshold_pct`` are
read only by validation and admission, never at trade time: the selected strategy
is handed to the model as context and the model decides. So a rule-replay would
have graded a rule the live system never runs, and would have produced a confident
number about the wrong object.

What is real is the decision the strategy actually produced. When a strategy is
selected, the system acts, and every action has since been scored by
`counterfactual.py` against the first real broker quote at or after the horizon.
That is a genuine offline screen over genuine recorded behaviour: it cannot be
fitted, there is no rule to overfit, and every number traces to a journalled row.

Three properties keep it honest:

* **It can only reject.** Promotion to ACTIVE still requires prospective live
  cycles through probation. This stage never promotes anything, so a favourable
  backtest - or a favourable screen - cannot short-circuit live evidence.

* **It cannot manufacture evidence.** Verdicts come from journalled rows, and a
  candidate with too few scored decisions returns
  ``INCONCLUSIVE_INSUFFICIENT_EVIDENCE`` rather than a pass. With the live journal
  most candidates are exactly that, which is the truthful answer and is the reason
  this is a screen and not a gate that decides.

* **It is after cost**, because the verdicts it reads were computed after cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

#: A candidate needs this many *informative* decisions before its record can say
#: anything at all. Below it, silence is the honest answer.
DEFAULT_MIN_SCORED_DECISIONS = 10


#: A strategy whose recorded decisions were right less often than this is
#: rejected. The bar is deliberately low: this filters the clearly bad, it is not
#: a claim about which strategy is best.
DEFAULT_MIN_GOOD_HOLD_RATIO = 0.5

PASS_SCREENED = "PASS_SCREENED"
REJECT_POOR_DECISIONS = "REJECT_POOR_DECISIONS"
INCONCLUSIVE = "INCONCLUSIVE_INSUFFICIENT_EVIDENCE"

GOOD_HOLD = "GOOD_HOLD"
MISSED_ALPHA = "MISSED_ALPHA"
FALSE_TRADE = "FALSE_TRADE"
NEUTRAL = "NEUTRAL"

#: Verdicts that mean something about whether the decision was right. NEUTRAL is
#: deliberately absent: a move that did not clear the cost is silence, not a
#: failure.
INFORMATIVE = frozenset({GOOD_HOLD, MISSED_ALPHA, FALSE_TRADE})


@dataclass(frozen=True)
class DecisionRecord:
    """One recorded decision and the verdict that was scored against it later."""

    cycle_id: str
    action: str
    verdict: str
    net_return_pct: float | None


@dataclass
class OfflineValidationResult:
    strategy_id: str
    verdict: str
    reason: str
    decisions: int = 0
    scored: int = 0
    good_holds: int = 0
    missed_alpha: int = 0
    false_trades: int = 0
    neutral: int = 0
    good_hold_ratio: float | None = None
    mean_net_pct: float | None = None
    cycles: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return self.verdict == REJECT_POOR_DECISIONS

    def to_payload(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "decisions": self.decisions,
            "scored": self.scored,
            "good_holds": self.good_holds,
            "missed_alpha": self.missed_alpha,
            "false_trades": self.false_trades,
            "neutral": self.neutral,
            "good_hold_ratio": self.good_hold_ratio,
            "mean_net_pct": self.mean_net_pct,
        }


def _field(obj, name, default=None):
    """Read a field from a JournalEvent model or a plain dict.

    `JsonlJournal.read_events` returns `JournalEvent` objects, not dicts. The
    first cut called `.get` on them, so every real call raised AttributeError and
    the daemon swallowed it into a FAILED event - a screen that looked wired and
    was never reached.
    """
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def collect_decisions(
    counterfactual_events: Iterable[object],
    strategy_id: str,
) -> list[DecisionRecord]:
    """Pull this strategy's decisions and verdicts out of the journalled rows.

    The latest event wins per cycle, so a re-run supersedes its own earlier
    result rather than being double-counted. The journal is the only source; this
    recomputes nothing, so the screen cannot disagree with what was recorded.
    """
    latest: dict[str, DecisionRecord] = {}
    for event in counterfactual_events:
        for row in (_field(event, "payload", None) or {}).get("rows", []):
            if row.get("strategy_id") != strategy_id:
                continue
            cycle_id = row.get("cycle_id")
            if not cycle_id:
                continue
            latest[cycle_id] = DecisionRecord(
                cycle_id=cycle_id,
                action=row.get("action", ""),
                verdict=row.get("verdict", ""),
                net_return_pct=row.get("net_return_pct"),
            )
    return [latest[key] for key in sorted(latest)]


def validate(
    decisions: Iterable[DecisionRecord],
    *,
    strategy_id: str,
    min_scored: int = DEFAULT_MIN_SCORED_DECISIONS,
    min_good_hold_ratio: float = DEFAULT_MIN_GOOD_HOLD_RATIO,
) -> OfflineValidationResult:
    """Screen a candidate on the decisions it has actually produced.

    Scored means the counterfactual reached a verdict: a hold before a fall, a hold
    before a rally, or a filled order that lost. PENDING and GAP rows are excluded
    because they say nothing about whether the decision was right.
    """
    records = list(decisions)
    result = OfflineValidationResult(
        strategy_id=strategy_id, verdict=INCONCLUSIVE, reason="", decisions=len(records)
    )
    if not records:
        result.reason = (
            "no recorded decisions for this strategy, so there is nothing to "
            "validate; this is a first-cycle candidate"
        )
        return result

    neutral = [d for d in records if d.verdict == NEUTRAL]
    result.neutral = len(neutral)
    # NEUTRAL is excluded from the ratio on purpose. It means the move did not
    # clear the cost, so the decision taught us nothing either way - it is
    # silence, not evidence of a bad decision. Counting it as a failure rejected
    # tiny-fixed-size-001 on 15 neutral decisions, and that strategy is the
    # largest PnL contributor on record (+362.66).
    scored = [d for d in records if d.verdict in INFORMATIVE]
    result.scored = len(scored)
    result.cycles = [d.cycle_id for d in records]
    for record in scored:
        if record.verdict == GOOD_HOLD:
            result.good_holds += 1
        elif record.verdict == MISSED_ALPHA:
            result.missed_alpha += 1
        elif record.verdict == FALSE_TRADE:
            result.false_trades += 1

    nets = [d.net_return_pct for d in scored if d.net_return_pct is not None]
    if nets:
        result.mean_net_pct = round(sum(nets) / len(nets), 6)

    if result.scored < min_scored:
        extra = (
            f"; a further {result.neutral} moved less than cost and so say nothing"
            if result.neutral
            else ""
        )
        result.reason = (
            f"only {result.scored} informative decision(s) out of "
            f"{result.decisions}, below the {min_scored} needed to say anything; "
            f"not a pass and not a rejection{extra}"
        )
        return result

    # Holds are the only rows that measure whether *not* trading was right, and a
    # false trade is a filled order that lost. The ratio is over everything scored
    # so a strategy cannot buy a good ratio by trading constantly and luckily.
    result.good_hold_ratio = round(result.good_holds / result.scored, 6)
    if result.false_trades:
        result.verdict = REJECT_POOR_DECISIONS
        result.reason = (
            f"{result.false_trades} of {result.scored} scored decisions were "
            "filled trades that lost money net of cost"
        )
        return result
    if result.good_hold_ratio < min_good_hold_ratio:
        result.verdict = REJECT_POOR_DECISIONS
        result.reason = (
            f"only {result.good_holds} of {result.scored} scored decisions were "
            f"correct holds (ratio {result.good_hold_ratio:.3f} < "
            f"{min_good_hold_ratio:.2f})"
        )
        return result

    result.verdict = PASS_SCREENED
    result.reason = (
        f"{result.good_holds} of {result.scored} scored decisions were correct "
        f"holds and none were losing trades; a screen, not a promotion"
    )
    return result
