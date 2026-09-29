"""Which of signal, model, strategy, allocation, execution, cost or regime actually
produced the PnL.

The objective asks this directly, and the honest answer on the current record is not
the one the headline number suggests.

**The +564.39 is not the model's money.** All 23 closed lots were opened by the
`baseline` decision source on 2026-06-11, 06-12 and 06-18. The LLM path produced its
first decision on 2026-09-28, two and a half months after the last profitable lot, and
opened none of them. The model's contribution to realized PnL is **0.00**.

That result was reachable from data the system already had and had never joined. Both
numbers - which strategy earned it, and which decision source opened the lot - are
recorded per fill. Nothing connected the two, so `strategy_realized_pnl` was read as
evidence that the decision engine worked, when it is evidence about the baseline.

Two numbers in the same payload also invite a false reconciliation:

* `strategy_realized_pnl` (+564.39) is the sum of the agent's **closed** lots, SPY
  only, opened 2026-06-11 to 2026-06-18.
* `account_realized_or_reported_pnl` (-406.54) is the broker's **cumulative**
  `profit_loss` for the whole account over all time, including BIL and TLT and
  whatever the account held before the agent ran.

They cover different scopes and different periods. Reading the difference of 970.93 as
"the agent lost 970 against its own attribution" is simply wrong, and this module
exists so that nobody has to guess which is which.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

#: The seven causes the objective names. Reported as a table with an explicit
#: "cannot attribute" where the evidence does not support a claim.
CAUSES = (
    "signal", "model", "strategy", "allocation", "execution", "cost", "regime",
)


@dataclass
class CauseRow:
    cause: str
    verdict: str
    evidence: str


@dataclass
class Attribution:
    total_realized_pnl: float = 0.0
    closed_lots: int = 0
    by_source: dict[str, float] = field(default_factory=dict)
    lots_by_source: dict[str, int] = field(default_factory=dict)
    by_strategy: dict[str, float] = field(default_factory=dict)
    account_pnl: float | None = None
    account_pnl_scope: str = ""
    fees: float = 0.0
    fill_ratio: float | None = None
    rejected_orders: int = 0
    causes: list[CauseRow] = field(default_factory=list)

    @property
    def model_pnl(self) -> float:
        return self.by_source.get("llm", 0.0) + self.by_source.get(
            "fallback_policy_engine", 0.0
        )

    @property
    def baseline_pnl(self) -> float:
        return self.by_source.get("baseline", 0.0)

    def headline(self) -> str:
        if not self.closed_lots:
            return "no closed lots, so nothing to attribute"
        if self.model_pnl == 0.0:
            return (
                f"{self.total_realized_pnl:+.2f} across {self.closed_lots} closed "
                f"lot(s), all opened by the '{self._top_source()}' decision source; "
                "the model contributed 0.00"
            )
        return (
            f"{self.total_realized_pnl:+.2f} across {self.closed_lots} closed "
            f"lot(s), of which the model contributed {self.model_pnl:+.2f} and the "
            f"baseline {self.baseline_pnl:+.2f}"
        )

    def _top_source(self) -> str:
        if not self.by_source:
            return "none"
        return max(self.by_source.items(), key=lambda kv: abs(kv[1]))[0]


def attribute(records: list, pnl_payload: dict, window: dict | None = None) -> Attribution:
    """Join closed lots to the decision that opened them.

    The lot records `buy_order_id`; the cycle records carry `execution.order_id`. That
    join is the whole analysis, and it is the join that was never made - which is why
    a number in a payload was read as evidence about the model.
    """
    result = Attribution(
        account_pnl=pnl_payload.get("account_realized_or_reported_pnl"),
        fill_ratio=(window or {}).get("fill_quantity_ratio"),
        rejected_orders=(window or {}).get("rejected_orders", 0) or 0,
    )
    result.account_pnl_scope = (
        "broker cumulative profit_loss for the WHOLE account over all time, "
        "including non-agent positions such as BIL and TLT"
    )

    by_order = {
        record.execution.order_id: record
        for record in records
        if getattr(record, "execution", None) and record.execution.order_id
    }

    lots = pnl_payload.get("closed_lots") or []
    result.closed_lots = len(lots)
    for lot in lots:
        realized = float(lot.get("realized_pnl") or 0.0)
        result.total_realized_pnl += realized
        result.fees += float(lot.get("buy_fees") or 0.0) + float(
            lot.get("sell_fees") or 0.0
        )
        strategy_id = lot.get("strategy_id") or "UNATTRIBUTED"
        result.by_strategy[strategy_id] = (
            result.by_strategy.get(strategy_id, 0.0) + realized
        )
        opener = by_order.get(lot.get("buy_order_id"))
        source = (
            opener.decision.decision_source if opener is not None else "UNLINKED"
        )
        result.by_source[source] = result.by_source.get(source, 0.0) + realized
        result.lots_by_source[source] = result.lots_by_source.get(source, 0) + 1

    result.causes = _classify(result)
    return result


def _classify(a: Attribution) -> list[CauseRow]:
    """One row per cause the objective names, each with a verdict and its evidence.

    `CANNOT ATTRIBUTE` is a legitimate and expected verdict. Claiming a cause because
    the data is suggestive is how a comfortable story replaces an honest one.
    """
    rows: list[CauseRow] = []
    total = a.total_realized_pnl

    if a.closed_lots and a.model_pnl == 0.0:
        rows.append(CauseRow(
            "model", "NOT THE CAUSE",
            f"0.00 of {total:+.2f} came from an llm or fallback decision; every closed "
            f"lot was opened by '{a._top_source()}'",
        ))
        rows.append(CauseRow(
            "signal", "ATTRIBUTABLE",
            f"all {a.closed_lots} closed lots came from the {a._top_source()} "
            "decision source, so the PnL belongs to that rule rather than to "
            "anything the model inferred",
        ))
    elif a.closed_lots:
        rows.append(CauseRow(
            "model", "PARTLY ATTRIBUTABLE",
            f"{a.model_pnl:+.2f} of {total:+.2f} came from model decisions and "
            f"{a.baseline_pnl:+.2f} from the baseline source",
        ))
    else:
        rows.append(CauseRow(
            "model", "CANNOT ATTRIBUTE", "no closed lots on record"
        ))

    if a.by_strategy:
        top = max(a.by_strategy.items(), key=lambda kv: abs(kv[1]))
        rows.append(CauseRow(
            "strategy", "ATTRIBUTABLE",
            f"{len(a.by_strategy)} strategies carry the PnL; the largest is "
            f"{top[0]} at {top[1]:+.2f}",
        ))

    if a.fees == 0.0 and a.closed_lots:
        rows.append(CauseRow(
            "cost", "NOT A FACTOR HERE",
            f"the paper broker charged {a.fees:.2f} in fees across {a.closed_lots} "
            "closed lots, so this figure is gross of real trading costs and "
            "overstates what a live venue would have paid",
        ))

    # Every cause the objective names appears, whether or not there is data for it.
    # A row that is simply absent when the data is missing is a silent gap, and a
    # reader cannot tell "not a factor" from "nobody looked".
    if a.rejected_orders:
        rows.append(CauseRow(
            "allocation", "PARTLY ATTRIBUTABLE",
            f"{a.rejected_orders} order(s) were refused, so allocation constrained "
            "the outcome; the PnL is what got through, not what was wanted",
        ))
    else:
        rows.append(CauseRow(
            "allocation", "CANNOT ATTRIBUTE",
            "no refused orders on record, so allocation did not constrain this result",
        ))

    if a.fill_ratio is not None:
        rows.append(CauseRow(
            "execution", "NOT A FACTOR HERE",
            f"fill ratio {a.fill_ratio:.2f} - submitted quantity was filled in full, "
            "so execution did not cost anything on these lots",
        ))
    else:
        rows.append(CauseRow(
            "execution", "CANNOT ATTRIBUTE",
            "no fill ratio is on record for this window",
        ))

    rows.append(CauseRow(
        "regime", "CANNOT ATTRIBUTE",
        "no regime context is computed or recorded, so no part of the PnL can be "
        "separated into regime versus signal",
    ))

    missing = [c for c in CAUSES if c not in {r.cause for r in rows}]
    for cause in missing:
        rows.append(CauseRow(cause, "CANNOT ATTRIBUTE", "no evidence recorded"))
    return rows


def to_payload(a: Attribution) -> dict:
    return {
        "headline": a.headline(),
        "total_realized_pnl": round(a.total_realized_pnl, 2),
        "closed_lots": a.closed_lots,
        "model_pnl": round(a.model_pnl, 2),
        "baseline_pnl": round(a.baseline_pnl, 2),
        "by_source": {k: round(v, 2) for k, v in sorted(a.by_source.items())},
        "lots_by_source": a.lots_by_source,
        "by_strategy": {k: round(v, 2) for k, v in sorted(a.by_strategy.items())},
        "account_pnl": a.account_pnl,
        "account_pnl_scope": a.account_pnl_scope,
        "do_not_reconcile": (
            "strategy PnL is closed agent lots in one window; account PnL is the "
            "broker's cumulative figure for the whole account. Their difference is "
            "not a loss and must not be read as one."
        ),
        "fees": round(a.fees, 4),
        "causes": [
            {"cause": c.cause, "verdict": c.verdict, "evidence": c.evidence}
            for c in a.causes
        ],
    }
