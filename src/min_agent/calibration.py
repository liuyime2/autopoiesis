"""Was the model's confidence worth anything?

The objective requires model predictions to be aligned against actual outcomes. The
system records a `confidence` on every decision and never once compares it to what
happened. Confidence is used as a gate - `min_confidence` decides whether an order may
be sent - and never as a claim to be tested. So a model that is confidently wrong is
indistinguishable from one that is right, which is the one distinction that matters
for whether the decision engine is worth running at all.

The pairing is already available and needs no new data: every decision carries a
confidence, and the counterfactual ledger already scores every decision against the
first real broker quote at or after the horizon. This joins the two.

What it deliberately does not do is produce a score and call it a result. On the live
journal **all 67 LLM decisions are PENDING** - they all fall inside a single session
on 2026-09-28, and the 24-hour horizon needs a quote from the following day, which
does not exist because the market has been closed. The honest answer today is that the
model's value is *unmeasured*. A module that smoothed that into a number would be
worse than nothing, because it would look like an answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

GOOD_HOLD = "GOOD_HOLD"
MISSED_ALPHA = "MISSED_ALPHA"
FALSE_TRADE = "FALSE_TRADE"
NEUTRAL = "NEUTRAL"

#: Verdicts that mean the decision was right or wrong. NEUTRAL means the move did not
#: clear the cost, so it is silence and is excluded rather than counted as either.
INFORMATIVE = frozenset({GOOD_HOLD, MISSED_ALPHA, FALSE_TRADE})

#: Below this many informative decisions, no accuracy number is printed. Six
#: decisions split across three buckets says nothing about anything.
MIN_SCORED_DECISIONS = 30

#: Buckets are 0.2 wide so five of them span the legal range, which keeps a
#: confidence of 0.95 and 1.0 in the same bucket instead of leaving a bucket with one
#: member in it that then reads as a 100% accurate stratum.
BUCKET_WIDTH = 0.2


@dataclass(frozen=True)
class CalibrationRow:
    cycle_id: str
    confidence: float
    action: str
    verdict: str
    decision_source: str

    @property
    def informative(self) -> bool:
        return self.verdict in INFORMATIVE

    @property
    def correct(self) -> bool:
        """Only a hold that avoided a loss is scored as correct.

        A MISSED_ALPHA is a hold that should have been a trade, and a FALSE_TRADE is a
        trade that lost. Both are decisions the system would have made better
        differently, so both are incorrect. Scoring a missed rally as a "correct
        caution" would flatter any model that never trades, which is the single easiest
        way to manufacture a good calibration curve.
        """
        return self.verdict == GOOD_HOLD


@dataclass
class Bucket:
    low: float
    high: float
    n: int = 0
    correct: int = 0
    mean_confidence: float = 0.0
    _conf_sum: float = field(default=0.0, repr=False)

    @property
    def accuracy(self) -> float | None:
        return None if self.n == 0 else self.correct / self.n

    def to_payload(self) -> dict:
        return {
            "range": f"{self.low:.1f}-{self.high:.1f}",
            "n": self.n,
            "correct": self.correct,
            "accuracy": None if self.accuracy is None else round(self.accuracy, 4),
            "mean_confidence": round(self.mean_confidence, 4),
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
    base_rate: float | None = None
    buckets: list[Bucket] = field(default_factory=list)
    passed_gate_n: int = 0
    passed_gate_accuracy: float | None = None
    top_bucket: dict | None = None
    top_bucket_accuracy: float | None = None
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
        rows.append(
            CalibrationRow(
                cycle_id=cycle_id,
                confidence=float(decision.confidence),
                action=decision.action,
                verdict=verdicts_by_cycle.get(cycle_id, "UNSCORED"),
                decision_source=decision.decision_source,
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

    n_buckets = max(1, round(1.0 / BUCKET_WIDTH))
    for i in range(n_buckets):
        low = i * BUCKET_WIDTH
        bucket = Bucket(low=low, high=low + BUCKET_WIDTH)
        for r in scored:
            index = min(int(r.confidence / BUCKET_WIDTH), n_buckets - 1)
            if index == i:
                bucket.n += 1
                bucket.correct += 1 if r.correct else 0
                bucket._conf_sum += r.confidence
        if bucket.n:
            bucket.mean_confidence = bucket._conf_sum / bucket.n
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
    # the good decisions" actually asks.
    populated = [b for b in report.buckets if b.n >= 10]
    if populated:
        top = max(populated, key=lambda b: b.mean_confidence)
        report.top_bucket = top.to_payload()
        report.top_bucket_accuracy = top.accuracy

    if report.passed_gate_n < 10:
        report.verdict = (
            f"INSUFFICIENT_AT_GATE: only {report.passed_gate_n} scored decision(s) "
            f"cleared confidence {min_confidence}, too few to judge whether "
            "confidence means anything"
        )
    elif (
        report.top_bucket_accuracy is not None
        and report.top_bucket_accuracy <= report.base_rate
    ):
        top = report.top_bucket["range"] if report.top_bucket else "?"
        report.verdict = (
            f"NO_SIGNAL: the most confident bucket ({top}) was right "
            f"{report.top_bucket_accuracy:.1%} of the time against a "
            f"{report.base_rate:.1%} base rate, so the stated confidence is not "
            "identifying the good decisions and the gate is not earning its place"
        )
    else:
        report.verdict = (
            f"SIGNAL: the most confident bucket "
            f"({report.top_bucket['range'] if report.top_bucket else '?'}) was right "
            f"{report.top_bucket_accuracy:.1%} against a {report.base_rate:.1%} base "
            f"rate, Brier {report.brier:.3f} (0.25 is a constant 0.5 claim). Still a "
            "hypothesis, not a result."
        )
    return report
