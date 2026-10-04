"""Was the model's confidence worth anything?

The objective requires model predictions to be aligned against actual outcomes. The
system records a `confidence` on every decision and never once compares it to what
happened. Confidence is used as a gate - `min_confidence` decides whether an order may
be sent - and never as a claim to be tested. So a model that is confidently wrong is
indistinguishable from one that is right, which is the one distinction that matters
for whether the decision engine is worth running at all.

That gate has a real but thin record, and the shape of it is worth stating here rather than
leaving to a reader who assumes either a working filter or dead code. Over the 1179 cycles on
record it has fired **once**, and that firing was correct: it blocked a baseline SELL at
confidence 0.30 that the counterfactual ledger scores as a `FALSE_TRADE`, 22 points below the
base rate of the day it appeared on. It has never refused one of the LLM's own decisions,
because the lowest confidence the model states on a trade is exactly `min_confidence` — so its
inertness is a fact about this model's behaviour distribution, not about the gate. Every
setting above 0.5 blocks sets that underperform their own days. So this module's job is to
keep reporting what the model's confidences are worth on the trades it can see, which on the
live record is nothing, and not to imply the gate is carrying weight it is not. See
`docs/superpowers/plans/2026-10-03-min-confidence-what-it-is-worth.md`.

The pairing is already available and needs no new data: every decision carries a
confidence, and the counterfactual ledger already scores every decision against the
first real broker quote at or after the horizon. This joins the two.

One control is not optional, though. Correctness is mostly a property of the *trading day*,
not of the decision: on the live record the per-day base rate of scored decisions runs
96.9% / 26.6% / 85.1% / 12.3% over four consecutive days, because on a down day a HOLD is
the right answer and on an up day the same HOLD is a MISSED_ALPHA. A bucket compared against
the overall base rate is therefore comparing days, and the first version of this report did
exactly that and published "confidence is inverted" - a description of the market's
direction. Every figure below is stated both raw and against the days it came from.

What it deliberately does not do is produce a score and call it a result. On the live
journal **all 67 LLM decisions are PENDING** - they all fall inside a single session
on 2026-09-28, and the 24-hour horizon needs a quote from the following day, which
does not exist because the market has been closed. The honest answer today is that the
model's value is *unmeasured*. A module that smoothed that into a number would be
worse than nothing, because it would look like an answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from min_agent import coerce
from min_agent.counterfactual import (
    CORRECT,
    FALSE_TRADE,
    GOOD_HOLD,
    GOOD_TRADE,
    INFORMATIVE,
    MISSED_ALPHA,
    NEUTRAL,
)

# Re-exported rather than redeclared. These used to be a third hand-written copy of the
# same verdict set, and this copy had drifted: it had no GOOD_TRADE at all, so winning
# trades were dropped from calibration entirely and then scored incorrect. See
# `counterfactual` for the full account.
__all__ = [
    "CORRECT",
    "FALSE_TRADE",
    "GOOD_HOLD",
    "GOOD_TRADE",
    "INFORMATIVE",
    "MISSED_ALPHA",
    "NEUTRAL",
]

#: Below this many informative decisions, no accuracy number is printed. Six
#: decisions split across three buckets says nothing about anything.
MIN_SCORED_DECISIONS = 30

#: Buckets are 0.2 wide so five of them span the legal range, which keeps a
#: confidence of 0.95 and 1.0 in the same bucket instead of leaving a bucket with one
#: member in it that then reads as a 100% accurate stratum.
BUCKET_WIDTH = 0.2

#: The top bucket must beat the base rate by this much to count as separating.
#: Without it, a 96.8% base rate and a 97.1% bucket produce "SIGNAL" on a 0.3 point
#: difference, which is rounding noise dressed as a finding.
MIN_MATERIAL_MARGIN = 0.05

#: A Brier score at or above this is worse than always claiming 0.5, which scores
#: exactly 0.25. Beyond it the stated confidence is not merely uninformative, it is
#: actively misleading, and the verdict must say so whatever the accuracy says.
BRIER_UNINFORMATIVE = 0.25


@dataclass(frozen=True)
class CalibrationRow:
    cycle_id: str
    confidence: float
    action: str
    verdict: str
    decision_source: str
    #: The trading day the decision was taken on, taken from the record's own snapshot.
    #: `None` only when a caller builds rows by hand without one.
    #:
    #: This exists because correctness is mostly a property of the *day*: on the live
    #: record the per-day base rate of scored decisions runs 96.9% / 26.6% / 85.1% /
    #: 12.3% across four consecutive days, because on a down day a HOLD is right and on
    #: an up day it is MISSED_ALPHA. Comparing buckets across days therefore compares
    #: days, and the report was doing exactly that - see `_day_expectation`.
    day: date | None = None

    @property
    def informative(self) -> bool:
        return self.verdict in INFORMATIVE

    @property
    def correct(self) -> bool:
        """A decision counts as correct when it was a good hold or a winning trade.

        A MISSED_ALPHA is a hold that should have been a trade, and a FALSE_TRADE is a
        trade that lost. Both are decisions the system would have made better
        differently, so both are incorrect. Scoring a missed rally as a "correct
        caution" would flatter any model that never trades, which is the single easiest
        way to manufacture a good calibration curve.

        GOOD_TRADE joins GOOD_HOLD because it is the same kind of statement told from the
        other side: the decision was right. Excluding it does not protect that curve, it
        inverts it - the model is most confident when it trades, so refusing to credit
        winning trades made the most confident bucket read as the least accurate one and
        produced a Brier score that said more about this function than about the model.
        """
        return self.verdict in CORRECT


@dataclass
class Bucket:
    low: float
    high: float
    n: int = 0
    correct: int = 0
    mean_confidence: float = 0.0
    #: What this bucket's accuracy would be if confidence carried no information at
    #: all - the mean base rate of the days its decisions were taken on. The margin
    #: between the two is the only comparison here that is about the model.
    day_expected_accuracy: float | None = None
    _conf_sum: float = field(default=0.0, repr=False)

    @property
    def accuracy(self) -> float | None:
        return None if self.n == 0 else self.correct / self.n

    @property
    def day_margin(self) -> float | None:
        """Accuracy above what the bucket's own days predict, or `None` without days."""
        if self.accuracy is None or self.day_expected_accuracy is None:
            return None
        return self.accuracy - self.day_expected_accuracy

    def to_payload(self) -> dict:
        return {
            "range": f"{self.low:.1f}-{self.high:.1f}",
            "n": self.n,
            "correct": self.correct,
            "accuracy": None if self.accuracy is None else round(self.accuracy, 4),
            "mean_confidence": round(self.mean_confidence, 4),
            "day_expected_accuracy": (
                None if self.day_expected_accuracy is None
                else round(self.day_expected_accuracy, 4)
            ),
            "day_margin": None if self.day_margin is None else round(self.day_margin, 4),
        }


@dataclass
class CalibrationReport:
    source: str
    total_decisions: int = 0
    scored: int = 0
    pending: int = 0
    neutral: int = 0
    correct: int = 0
    brier: float | None = None
    #: The same score with each decision compared against the base rate of its own day.
    #: The gap between the two is the share of the apparent mis-calibration that is the
    #: market's direction rather than the model's. On the live record: 0.396 raw,
    #: 0.271 adjusted, so roughly a third of it was the day.
    day_brier: float | None = None
    base_rate: float | None = None
    #: Trading days the scored decisions fall on, and their base rates. Empty when the
    #: caller built rows without one, and then the day-adjusted figures stay `None`
    #: rather than silently reproducing the cross-day ones.
    days: int = 0
    day_base_rates: dict[str, float] = field(default_factory=dict)
    buckets: list[Bucket] = field(default_factory=list)
    passed_gate_n: int = 0
    passed_gate_accuracy: float | None = None
    #: `Bucket.to_payload()`; kept as the serialised form because it is what
    #: the report publishes and what a reader outside this module sees.
    top_bucket: dict[str, object] | None = None
    top_bucket_accuracy: float | None = None
    #: The most confident populated bucket's margin over its own days. The comparison the
    #: verdict is about; `top_bucket_accuracy` alone says more about the market.
    top_bucket_day_margin: float | None = None
    #: And the worst one, because a confidence that is merely uninformative and a
    #: confidence that is actively misleading call for different responses.
    worst_bucket_day_margin: float | None = None
    verdict: str = ""
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "source": self.source,
            "total_decisions": self.total_decisions,
            "scored": self.scored,
            "pending": self.pending,
            "neutral": self.neutral,
            "correct": self.correct,
            "brier": None if self.brier is None else round(self.brier, 6),
            "day_brier": None if self.day_brier is None else round(self.day_brier, 6),
            "days": self.days,
            "day_base_rates": {
                key: round(value, 6) for key, value in sorted(self.day_base_rates.items())
            },
            "base_rate": None if self.base_rate is None else round(self.base_rate, 6),
            "buckets": [b.to_payload() for b in self.buckets],
            "passed_gate_n": self.passed_gate_n,
            "passed_gate_accuracy": (
                None if self.passed_gate_accuracy is None
                else round(self.passed_gate_accuracy, 6)
            ),
            "verdict": self.verdict,
            "notes": self.notes,
        }


def build_rows(
    records: Sequence[object],
    verdicts_by_cycle: dict[str, str],
    *,
    source: str = "llm",
) -> list[CalibrationRow]:
    """Pair each decision with the verdict that was later scored against it."""
    rows: list[CalibrationRow] = []
    for record in records:
        decision = getattr(record, "decision", None)
        if decision is None or decision.decision_source != source:
            continue
        cycle_id = getattr(record, "cycle_id", None)
        if not cycle_id:
            continue
        # The day comes from the record's own market-data timestamp, not from when the
        # verdict was computed: it is the day the decision was taken on, which is the day
        # whose base rate it has to be compared against.
        snapshot = getattr(record, "snapshot", None)
        stamp = getattr(snapshot, "timestamp", None)
        rows.append(
            CalibrationRow(
                cycle_id=cycle_id,
                confidence=float(decision.confidence),
                action=decision.action,
                verdict=verdicts_by_cycle.get(cycle_id, "UNSCORED"),
                decision_source=decision.decision_source,
                day=stamp.date() if stamp is not None else None,
            )
        )
    return rows


def calibrate(
    rows: Iterable[CalibrationRow],
    *,
    source: str = "llm",
    min_confidence: float = 0.5,
) -> CalibrationReport:
    """Score the model's confidence against what actually happened."""
    items = list(rows)
    report = CalibrationReport(source=source, total_decisions=len(items))
    if not items:
        report.verdict = (
            f"NO_DATA: no decisions from {source!r} are on record, so there is "
            "nothing to calibrate"
        )
        return report

    report.pending = sum(
        1 for r in items if r.verdict in {"PENDING", "GAP", "UNSCORED"}
    )
    report.neutral = sum(1 for r in items if r.verdict == NEUTRAL)
    scored = [r for r in items if r.informative]
    report.scored = len(scored)
    report.correct = sum(1 for r in scored if r.correct)

    if report.scored < MIN_SCORED_DECISIONS:
        report.verdict = (
            f"INSUFFICIENT: {report.scored} of {report.total_decisions} decisions "
            f"have an outcome yet, against the {MIN_SCORED_DECISIONS} needed to say "
            "anything about calibration"
        )
        if report.pending:
            report.notes.append(
                f"{report.pending} decision(s) are still waiting for a quote at or "
                "after their horizon; the honest reading is that the model's value is "
                "unmeasured, not that it is good"
            )
        return report

    report.base_rate = report.correct / report.scored
    # Brier score: mean squared error of the stated confidence against the realised
    # outcome. 0 is perfect, 0.25 is what always claiming 0.5 scores.
    report.brier = sum(
        (r.confidence - (1.0 if r.correct else 0.0)) ** 2 for r in scored
    ) / report.scored

    # The control. Correctness is mostly the day's, not the decision's: the same HOLD is
    # right on a down day and a MISSED_ALPHA on an up one. So each day's own base rate is
    # the expectation, and a bucket's accuracy is read against the days it appears on
    # rather than against the whole record.
    #
    # Needs more than one day to mean anything. With a single day the adjusted figures
    # would reproduce the raw ones and the control would be theatre, so they are left
    # `None` and the verdict says the comparison is unavailable instead of implying one.
    day_totals: dict[str, list[int]] = {}
    for r in scored:
        if r.day is None:
            continue
        entry = day_totals.setdefault(r.day.isoformat(), [0, 0])
        entry[0] += 1
        entry[1] += 1 if r.correct else 0
    expectations: dict[str, float] = {}
    if len(day_totals) > 1:
        report.days = len(day_totals)
        report.day_base_rates = {
            key: correct / total for key, (total, correct) in day_totals.items()
        }
        expectations = {
            r.cycle_id: report.day_base_rates[r.day.isoformat()]
            for r in scored
            if r.day is not None
        }
        report.day_brier = sum(
            (r.confidence - expectations[r.cycle_id]) ** 2
            for r in scored
            if r.cycle_id in expectations
        ) / report.scored
    elif day_totals:
        report.notes.append(
            "scored decisions fall on a single trading day, so correctness cannot be "
            "separated from the market's direction for that day; the day-adjusted "
            "figures are unavailable rather than equal to the raw ones"
        )

    n_buckets = max(1, round(1.0 / BUCKET_WIDTH))
    for i in range(n_buckets):
        low = i * BUCKET_WIDTH
        bucket = Bucket(low=low, high=low + BUCKET_WIDTH)
        expected_sum = 0.0
        expected_n = 0
        for r in scored:
            index = min(int(r.confidence / BUCKET_WIDTH), n_buckets - 1)
            if index == i:
                bucket.n += 1
                bucket.correct += 1 if r.correct else 0
                bucket._conf_sum += r.confidence
                if r.cycle_id in expectations:
                    expected_sum += expectations[r.cycle_id]
                    expected_n += 1
        if bucket.n:
            bucket.mean_confidence = bucket._conf_sum / bucket.n
        # Counted, not truthiness-tested: a day on which nothing was right has an
        # expectation of exactly 0.0, and `if expected_sum:` would discard that bucket's
        # margin - which is precisely the bucket that matters most on a bad day.
        if expected_n:
            bucket.day_expected_accuracy = expected_sum / expected_n
        report.buckets.append(bucket)

    # The decision that actually matters: of the decisions that were confident enough
    # to pass the gate and therefore reached the Guardian, how many were right? A
    # model whose confident decisions are no better than its unconfident ones is
    # adding a threshold, not information.
    passed = [r for r in scored if r.confidence >= min_confidence]
    report.passed_gate_n = len(passed)
    if passed:
        report.passed_gate_accuracy = sum(1 for r in passed if r.correct) / len(passed)

    # The top bucket is the sharper comparison. The configured gate is 0.5, so a 0.6
    # and a 0.9 both clear it and "gate accuracy" ends up measuring almost every
    # decision - which is how the first version of this test could not distinguish a
    # model that separates good from bad from one that does not. Whether the *most
    # confident* bucket beats the base rate is the question "does confidence identify
    # the good decisions" actually asks - and since that question was being answered
    # against a base rate the market set, it is now asked against the bucket's own days.
    populated = [b for b in report.buckets if b.n >= 10]
    if populated:
        top = max(populated, key=lambda b: b.mean_confidence)
        report.top_bucket = top.to_payload()
        report.top_bucket_accuracy = top.accuracy
        report.top_bucket_day_margin = top.day_margin
        judged = [b.day_margin for b in populated if b.day_margin is not None]
        if judged:
            report.worst_bucket_day_margin = min(judged)

    if report.passed_gate_n < 10:
        report.verdict = (
            f"INSUFFICIENT_AT_GATE: only {report.passed_gate_n} scored decision(s) "
            f"cleared confidence {min_confidence}, too few to judge whether "
            "confidence means anything"
        )
    elif report.brier is not None and report.brier >= BRIER_UNINFORMATIVE:
        # Checked before the accuracy comparison on purpose. A model can be right
        # almost always and still be badly calibrated, and when the base rate is
        # extreme the accuracy comparison cannot discriminate at all: the first
        # non-INSUFFICIENT verdict this system produced was "SIGNAL" on a 0.3 point
        # margin over a 96.8% base rate, with a Brier of 0.589 - which is 2.4x worse
        # than always claiming 0.5. The accuracy said nothing; the Brier said the
        # stated confidence is worse than useless.
        #
        # What it must not say is that the most confident bucket is less accurate than
        # the least. That comparison is against a base rate the market set: on the live
        # record the per-day rate runs 96.9% / 26.6% / 85.1% / 12.3%, and the 0.0-0.2
        # bucket that looked 90.6% accurate was simply the Tuesday. So the bucket is read
        # against its own days, and the day-adjusted Brier is what the verdict quotes.
        if report.day_brier is not None:
            day_clause = (
                f" Brier {report.day_brier:.3f} against the {report.days} day(s) those "
                "decisions were taken on, so "
                f"{(report.brier - report.day_brier):.3f} of it is the market's "
                "direction rather than the model's"
            )
        else:
            day_clause = (
                " The decisions fall on a single day, so how much of that is the "
                "market's direction cannot be separated here"
            )
        margin = None
        if report.top_bucket_accuracy is not None and report.base_rate is not None:
            margin = report.top_bucket_accuracy - report.base_rate
        worst_clause = ""
        if report.worst_bucket_day_margin is not None:
            worst_clause = (
                f". The worst bucket against its own days is "
                f"{report.worst_bucket_day_margin:+.1%}"
            )
        report.verdict = (
            f"MIS-CALIBRATED: Brier {report.brier:.3f} against the "
            f"{BRIER_UNINFORMATIVE:.2f} a constant 0.5 claim scores, so the stated "
            f"confidence adds no usable information.{day_clause}. Measured against the "
            f"whole record rather than the days, accuracy is {report.base_rate:.1%} and "
            f"the most confident bucket is {report.top_bucket_accuracy:.1%} (margin "
            f"{margin:+.1%} if positive){worst_clause}"
        )
    elif (
        report.top_bucket_accuracy is not None
        and report.base_rate is not None
        and report.top_bucket_accuracy - report.base_rate < MIN_MATERIAL_MARGIN
    ):
        report.verdict = (
            f"NO_SIGNAL: the most confident bucket was right "
            f"{report.top_bucket_accuracy:.1%} against a {report.base_rate:.1%} base "
            f"rate, a margin below the {MIN_MATERIAL_MARGIN:.0%} needed to be "
            "distinguishable from noise"
        )
    elif (
        report.top_bucket_accuracy is not None
        and report.base_rate is not None
        and report.top_bucket_accuracy <= report.base_rate
    ):
        top_range = coerce.field_str(report.top_bucket.get("range"), "top_bucket.range") if report.top_bucket else "?"
        report.verdict = (
            f"NO_SIGNAL: the most confident bucket ({top_range}) was right "
            f"{report.top_bucket_accuracy:.1%} of the time against a "
            f"{report.base_rate:.1%} base rate, so the stated confidence is not "
            "identifying the good decisions and the gate is not earning its place"
        )
    else:
        report.verdict = (
            f"SIGNAL: the most confident bucket "
            f"({coerce.field_str(report.top_bucket.get('range'), 'top_bucket.range') if report.top_bucket else '?'}) was right "
            f"{report.top_bucket_accuracy:.1%} against a {report.base_rate:.1%} base "
            f"rate, Brier {report.brier:.3f} (0.25 is a constant 0.5 claim). Still a "
            "hypothesis, not a result."
        )
    return report
