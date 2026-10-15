"""Synthetic boundary fixtures; these are not observations of real trading."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from synthetic_evidence import synthetic_clean_record, synthetic_row_metadata, synthetic_spec

from autopoiesis import calibration, offline_validation
from autopoiesis.evaluator import DeterministicEvaluator
from autopoiesis.learning_evidence import capture, eligibility
from autopoiesis.llm_decision import NO_STRATEGY_MARKER
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    ReflectionRecord,
    TradeDecision,
)
from autopoiesis.reflection_memory import ReflectionMemory

STAMP = datetime(2026, 6, 1, 14, tzinfo=timezone.utc)


def synthetic_legacy_record(*, decision_symbol="SPY"):
    return CycleRecord(
        cycle_id="synthetic-legacy", strategy_id="synthetic-strategy",
        snapshot=DataSnapshot(
            symbol="SPY", timestamp=STAMP, market_open=True, last_price=100,
            source="alpaca", account=AccountSnapshot(
                equity=1000, cash=1000, buying_power=1000, portfolio_value=1000, daily_loss=0,
            ),
        ),
        decision=TradeDecision(
            symbol=decision_symbol, action="HOLD", quantity=0, confidence=.8,
            rationale="synthetic fixture", strategy_id="synthetic-strategy", decision_source="llm",
        ),
        guardian=GuardianResult(approved=True, reason="synthetic"),
        execution=ExecutionResult(status="SKIPPED", message="synthetic", order_id=None, filled_quantity=0),
    )


def test_legacy_and_mismatched_cycles_do_not_calibrate():
    records = [synthetic_legacy_record(), synthetic_legacy_record(decision_symbol="TLT")]
    assert calibration.build_rows(records, {"synthetic-legacy": "GOOD_HOLD"}) == []


def test_legacy_counterfactual_rows_do_not_screen_or_supply_day_baseline():
    events = [{"payload": {"rows": [{
        "cycle_id": "synthetic-legacy", "strategy_id": "synthetic-strategy",
        "action": "HOLD", "verdict": "GOOD_HOLD", "net_return_pct": -2,
        "decided_at": STAMP.isoformat(),
    }]}}]
    assert offline_validation.collect_decisions(events, "synthetic-strategy") == []
    assert offline_validation.day_direction(events) == {}


def test_unknown_cycles_preserve_raw_counts_but_have_no_strategy_score():
    report = DeterministicEvaluator().evaluate([synthetic_legacy_record()])
    assert report.window_cycles == 1
    assert report.skipped_orders == 1
    assert report.strategy_metrics == {}


def test_legacy_reflection_scores_cannot_feed_selector():
    record = ReflectionRecord(
        generated_at=STAMP, window_cycles=50, submitted_orders=10, rejected_orders=0,
        error_count=0, summary="synthetic legacy cache", strategy_scores={"synthetic-strategy": 1},
    )
    assert ReflectionMemory("unused-synthetic-path").strategy_results(record) == []


def test_clean_capture_is_immutable_and_rejects_id_symbol_universe_and_digest_tampering():
    clean = synthetic_clean_record(synthetic_legacy_record())
    assert eligibility(clean) == "ELIGIBLE"
    assert len(calibration.build_rows([clean], {clean.cycle_id: "GOOD_HOLD"})) == 1
    assert eligibility(clean.model_copy(update={"strategy_id": "another"})) == "STRATEGY_ID_MISMATCH"
    assert eligibility(clean.model_copy(update={"decision": clean.decision.model_copy(update={"symbol": "TLT"})})) == "SYMBOL_MISMATCH"
    outsider = capture(synthetic_spec(clean.strategy_id, "TLT"))
    assert eligibility(clean.model_copy(update={"decision": clean.decision.model_copy(update={"strategy_provenance": outsider})})) == "OUTSIDE_RECORDED_UNIVERSE"
    tampered = outsider.model_copy(update={"behavior_digest": "0" * 64})
    assert eligibility(clean.model_copy(update={"decision": clean.decision.model_copy(update={"strategy_provenance": tampered})})) == "UNKNOWN_PROVENANCE"


def test_model_cannot_forge_strategy_id_or_metadata_and_selection_reads_once(tmp_path):
    from test_llm_provenance import ScriptedLLM, _engine, _snapshot

    model = ScriptedLLM(TradeDecision(
        symbol="SPY", action="BUY", quantity=1, confidence=.6,
        rationale="synthetic forged model id", strategy_id="forged",
        strategy_provenance=capture(synthetic_spec("forged", "TLT")),
    ))
    engine, policy = _engine(tmp_path, model)
    original = policy.strategy_library.list
    # Exercise the cache-reading branch too: it must use the same library read.
    selected = original()[0]
    source = synthetic_legacy_record().model_copy(update={
        "strategy_id": "s1", "decision": synthetic_legacy_record().decision.model_copy(update={
            "strategy_id": "s1", "strategy_provenance": capture(selected),
        }),
    })
    policy.reflection_memory.save(policy.reflection_memory.reflect([source]))
    calls = []

    def one_selection():
        calls.append(1)
        assert len(calls) == 1, "selected metadata must come from the same spec, not a second reload"
        return original()

    policy.strategy_library.list = one_selection
    decision = engine.decide_snapshot(_snapshot())
    assert decision.strategy_id == "s1"
    assert decision.strategy_provenance.strategy_id == "s1"
    assert decision.strategy_provenance.universe == ("SPY",)
    assert len(calls) == 1


@pytest.mark.parametrize("boundary", [
    # `uncovered_symbol` was in this list until 2026-10-14, when the plan authorised the
    # model to decide a symbol no strategy covers. It has its own test below. It is
    # deliberately NOT removed from the file: the six boundaries left here are failures -
    # an empty library, a selector that threw, and four ways for provenance to be absent or
    # wrong - and the model must not be asked about any of them.
    "empty_library", "selector_failure", "missing_provenance",
    "wrong_selected_symbol", "wrong_provenance_id", "invalid_provenance_digest",
])
def test_synthetic_unattributed_selection_abstains_before_model(tmp_path, monkeypatch, boundary):
    """Synthetic failures must never let an actionable model trade without attribution."""
    from test_llm_provenance import ScriptedLLM, _engine, _snapshot

    from autopoiesis import llm_decision

    symbol = "XLB" if boundary in {"uncovered_symbol", "wrong_selected_symbol"} else "SPY"
    model = ScriptedLLM(TradeDecision(
        symbol=symbol, action="BUY", quantity=1, confidence=.9,
        rationale="synthetic actionable model response",
    ))
    engine, policy = _engine(tmp_path, model, strategies=() if boundary == "empty_library" else ("s1",))
    if boundary == "selector_failure":
        def failed_selection(*_args, **_kwargs):
            raise RuntimeError("synthetic selector failure")

        monkeypatch.setattr(policy.selector, "select", failed_selection)
    elif boundary == "wrong_selected_symbol":
        selected = policy.strategy_library.list()[0]
        monkeypatch.setattr(policy.selector, "select", lambda *_args, **_kwargs: selected)
    elif boundary == "missing_provenance":
        monkeypatch.setattr(llm_decision, "capture", lambda _spec: None)
    elif boundary == "wrong_provenance_id":
        monkeypatch.setattr(llm_decision, "capture", lambda _spec: capture(synthetic_spec("other")))
    elif boundary == "invalid_provenance_digest":
        monkeypatch.setattr(llm_decision, "capture", lambda spec: capture(spec).model_copy(
            update={"behavior_digest": "0" * 64},
        ))
    fallback_calls = []

    def would_reselect(snapshot):
        fallback_calls.append(snapshot)
        return model.response

    monkeypatch.setattr(policy, "decide_snapshot", would_reselect)
    decision = engine.decide_snapshot(_snapshot(symbol=symbol))

    assert (decision.symbol, decision.action, decision.quantity) == (symbol, "HOLD", 0)
    assert "selection" in decision.rationale
    assert decision.hold_reason == "no_signal"
    assert decision.strategy_id is None and decision.strategy_provenance is None
    assert model.calls == []
    assert fallback_calls == []


def test_an_uncovered_symbol_is_asked_but_cannot_trade_unattributed(tmp_path, monkeypatch):
    """The one boundary the plan changes, asserted on the property that mattered.

    This case used to assert `model.calls == []`. The property the old test protected is
    the docstring of the test above: "synthetic failures must never let an actionable model
    trade without attribution". That property still holds, by a different mechanism - the
    decision carries no `strategy_id`, so it is attributed to no strategy, and HOLD is the
    baseline, so the model's unexplained BUY is refused. What changed is only that the
    model is now asked at all, which is the defect the plan names: about 316 cycles a day
    went by without the model ever being consulted.
    """
    from test_llm_provenance import ScriptedLLM, _engine, _snapshot

    model = ScriptedLLM(TradeDecision(
        symbol="XLB", action="BUY", quantity=1, confidence=.9,
        rationale="synthetic actionable model response",
    ))
    engine, _policy = _engine(tmp_path, model, strategies=("s1",))
    decision = engine.decide_snapshot(_snapshot(symbol="XLB"))

    assert len(model.calls) == 1, "the model must be asked about an uncovered symbol"
    assert (decision.symbol, decision.action, decision.quantity) == ("XLB", "HOLD", 0), (
        "an unexplained BUY for an uncovered symbol must not be taken"
    )
    assert decision.strategy_id is None, "no strategy may be credited with it"
    assert decision.strategy_provenance is None
    assert decision.decision_source == "llm", "the model answered"
    assert NO_STRATEGY_MARKER in decision.rationale, decision.rationale


def test_synthetic_no_selection_fallback_does_not_reselect_trade(tmp_path, monkeypatch):
    """A synthetic policy that would reselect BUY must not run in the fallback."""
    from test_llm_provenance import ScriptedLLM, _engine, _snapshot

    engine, policy = _engine(tmp_path, ScriptedLLM(), strategies=())
    calls = []

    def would_reselect(snapshot):
        calls.append(snapshot)
        return TradeDecision(
            symbol=snapshot.symbol, action="BUY", quantity=1, confidence=.9,
            rationale="synthetic second-selection trade", strategy_id="second-selection",
        )

    monkeypatch.setattr(policy, "decide_snapshot", would_reselect)
    decision = engine._fallback(_snapshot(), "synthetic timeout")
    assert (decision.action, decision.quantity) == ("HOLD", 0)
    assert "selection" in decision.rationale
    assert decision.strategy_id is None and decision.strategy_provenance is None
    assert calls == []


@pytest.mark.parametrize("model_fails", [False, True])
def test_synthetic_covered_model_and_fallback_preserve_frozen_provenance(tmp_path, monkeypatch, model_fails):
    """Synthetic covered trades keep the original capture even if the library changes."""
    from test_llm_provenance import ScriptedLLM, _engine, _snapshot

    model = ScriptedLLM(
        TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=.6, rationale="synthetic"),
        error=TimeoutError("synthetic") if model_fails else None,
    )
    engine, policy = _engine(tmp_path, model)
    frozen = capture(policy.strategy_library.list()[0])
    original_decide = model.decide

    def mutate_library(context):
        policy.strategy_library.save(synthetic_spec("s1", "TLT"))
        return original_decide(context)

    monkeypatch.setattr(model, "decide", mutate_library)
    decision = engine.decide_snapshot(_snapshot())
    # The action differs by design when the model fails: the fallback may not open
    # exposure, so the rule's BUY is recorded and refused. The provenance assertion below
    # is identical in both cases and is what this test is about - the frozen capture must
    # survive the library changing underneath it either way.
    expected_action = "HOLD" if model_fails else "BUY"
    assert (decision.symbol, decision.action, decision.strategy_id) == (
        "SPY", expected_action, "s1",
    )
    if model_fails:
        assert decision.rule_action == "BUY", "the refused intent must be recorded"
    assert decision.strategy_provenance == frozen
    assert decision.decision_source == ("fallback_policy_engine" if model_fails else "llm")
    assert len(model.calls) == 1


@pytest.mark.parametrize("model_fails", [False, True])
def test_synthetic_risk_fitted_rule_keeps_frozen_provenance(tmp_path, model_fails):
    """Synthetic position limits change the rule action without losing its attribution."""
    from test_llm_provenance import ScriptedLLM, _engine, _snapshot

    from autopoiesis.models import PositionSnapshot

    model = ScriptedLLM(
        TradeDecision(symbol="SPY", action="BUY", quantity=1, confidence=.6, rationale="synthetic"),
        error=TimeoutError("synthetic") if model_fails else None,
    )
    engine, policy = _engine(tmp_path, model)
    engine.risk_limits = {"max_position_value": 1000, "allowlist": ["SPY"]}
    frozen = capture(policy.strategy_library.list()[0])
    held = PositionSnapshot(symbol="SPY", quantity=2, market_value=1400)
    decision = engine.decide_snapshot(_snapshot(price=700, positions=[held]))
    assert (decision.action, decision.quantity, decision.strategy_id) == ("HOLD", 0, "s1")
    assert decision.strategy_provenance == frozen
    assert decision.decision_source == ("fallback_policy_engine" if model_fails else "policy_engine")


def test_contaminated_decision_still_supplies_counterfactual_horizon_quote():
    from autopoiesis import counterfactual

    clean = synthetic_clean_record(synthetic_legacy_record())
    future = synthetic_legacy_record(decision_symbol="TLT").model_copy(update={
        "cycle_id": "synthetic-future",
        "snapshot": clean.snapshot.model_copy(update={"timestamp": STAMP + timedelta(hours=25), "last_price": 95}),
    })
    report = counterfactual.evaluate([clean, future])
    assert report.rows[0].verdict == "GOOD_HOLD"
    assert report.rows[0].price_at_horizon == 95
    assert report.rows[0].eligibility == "ELIGIBLE"
    assert report.rows[1].eligibility == "UNKNOWN_PROVENANCE"


def test_latest_unknown_or_changed_strategy_row_revokes_prior_clean_evidence():
    clean = {**synthetic_row_metadata("s1"), "cycle_id": "c", "action": "HOLD", "verdict": "GOOD_HOLD", "net_return_pct": -2, "decided_at": STAMP.isoformat()}
    contaminated = {**clean, "strategy_id": "forged"}
    events = [{"payload": {"rows": [clean]}}, {"payload": {"rows": [contaminated]}}]
    assert offline_validation.collect_decisions(events, "s1") == []
    assert offline_validation.day_direction(events) == {}


def test_reflection_current_behavior_mismatch_preserves_spent_service_budget():
    clean = synthetic_clean_record(synthetic_legacy_record())
    memory = ReflectionMemory("unused-synthetic-path")
    reflection = memory.reflect([clean], cumulative_cycles={clean.strategy_id: 100}, cumulative_trade_attempts={clean.strategy_id: 20})
    current = synthetic_spec(clean.strategy_id, "TLT")
    [result] = memory.strategy_results(reflection, [current])
    assert result.cycles == 0 and result.realized_pnl is None and result.score == 0
    assert result.cumulative_cycles == 100 and result.cumulative_trade_attempts == 20


def test_unknown_seeded_fill_labels_preserve_accounting_without_learning_pnl():
    from test_evaluator import make_record

    from autopoiesis.models import BrokerEvidenceBatch, BrokerFillActivity

    closed = make_record(cycle_id="sell", action="SELL", quantity=1, strategy_id="closer")
    seeded = BrokerFillActivity(
        activity_id="synthetic-seed", client_order_id="synthetic-unknown-origin", strategy_id="opener",
        symbol="SPY", side="BUY", quantity=1, price=90, transaction_time=STAMP,
        source="alpaca_journal_confirmed_fill",
    )
    evidence = BrokerEvidenceBatch(
        generated_at=STAMP + timedelta(hours=2), window_start=STAMP, window_end=STAMP + timedelta(hours=2),
        activities=(BrokerFillActivity(
            activity_id="synthetic-sell", order_id="oid-sell", client_order_id="cid-sell",
            symbol="SPY", side="SELL", quantity=1, price=100, transaction_time=STAMP + timedelta(hours=1),
        ),),
    )
    report = DeterministicEvaluator().evaluate([closed], evidence=evidence, seeded_fills=[seeded], full_records=[closed])
    # The amount is the subject of this test; which strategy holds it is not. Realized PnL
    # is booked to the strategy that sold - see `test_realized_pnl_attribution.py`.
    assert sum(report.pnl.strategy_realized_pnl.values()) == 10
    assert "opener" not in report.strategy_metrics
    assert report.pnl.closed_lot_count == 1 and report.pnl.unmatched_sell_quantity == {}


@pytest.mark.parametrize("conflict", ["unknown_retry", "eligible_other_strategy", "partial_retry"])
def test_order_alias_conflicts_preserve_accounting_but_withhold_learning(conflict):
    from test_evaluator import make_record

    from autopoiesis.models import BrokerEvidenceBatch, BrokerFillActivity

    buy = make_record(cycle_id="buy", action="BUY", quantity=1, strategy_id="opener")
    sell = make_record(cycle_id="sell", action="SELL", quantity=1, strategy_id="closer")
    retry = buy.model_copy(update={"cycle_id": "synthetic-conflicting-retry",
        "execution": buy.execution.model_copy(update={"order_id": None})})
    if conflict in {"unknown_retry", "partial_retry"}:
        retry = retry.model_copy(update={"decision": retry.decision.model_copy(update={"strategy_provenance": None})})
    else:
        retry = synthetic_clean_record(retry.model_copy(update={"strategy_id": "other"}))
    evidence = BrokerEvidenceBatch(
        generated_at=STAMP + timedelta(hours=2), window_start=STAMP, window_end=STAMP + timedelta(hours=2),
        activities=tuple(BrokerFillActivity(
            activity_id=f"synthetic-{action}", order_id=f"oid-{action}",
            client_order_id=None if conflict == "partial_retry" and action == "buy" else f"cid-{action}",
            symbol="SPY", side=action.upper(), quantity=1, price=price,
            transaction_time=STAMP + timedelta(hours=index),
        ) for index, (action, price) in enumerate((("buy", 90), ("sell", 110)))),
    )
    records = [buy, retry, sell]
    if conflict == "partial_retry":
        records.insert(2, buy.model_copy(update={"cycle_id": "synthetic-broker-only-retry",
            "execution": buy.execution.model_copy(update={"client_order_id": None})}))
    report = DeterministicEvaluator().evaluate(records, evidence=evidence, full_records=records)
    assert sum(report.pnl.strategy_realized_pnl.values()) == 20
    assert report.strategy_metrics["opener"].realized_pnl is None


def test_historical_accepted_or_forged_journal_lessons_are_withheld(tmp_path):
    from autopoiesis.knowledge_admission import KnowledgeAdmission
    from autopoiesis.knowledge_library import KnowledgeLibrary
    from autopoiesis.models import KnowledgeArtifact
    from autopoiesis.policy_engine import PolicyEngine
    from autopoiesis.strategy_engine import StrategyLibrary

    library = KnowledgeLibrary(tmp_path / "knowledge")
    artifact = KnowledgeArtifact(
        artifact_id="synthetic-legacy-lesson", artifact_type="LESSON", answer="Synthetic claim",
        summary="Synthetic claim", source_kind="journal", source_refs=("unresolved",),
        created_at=STAMP, rationale="synthetic", status="ACCEPTED", eligibility_version=1,
        verified_source_digest="forged-model-marker",
    )
    library.save(artifact)
    library.verify_journal_sources([synthetic_legacy_record()])
    policy = PolicyEngine(strategy_library=StrategyLibrary(tmp_path / "strategies"), knowledge_library=library)
    assert policy.relevant_lessons("synthetic-strategy") == []
    assert not KnowledgeAdmission(knowledge_library=library).admit(artifact.model_copy(update={"artifact_id": "synthetic-new"})).accepted


@pytest.mark.parametrize("old_unknown", [False, True])
@pytest.mark.parametrize("fee", [0.0, 0.5])
def test_current_behavior_can_earn_learning_pnl_after_old_behavior(old_unknown, fee):
    from test_evaluator import make_record

    from autopoiesis.models import BrokerEvidenceBatch, BrokerFillActivity

    records = [make_record(cycle_id=cid, action=action, quantity=1, strategy_id="synthetic-s1", last_price=price)
        for cid, action, price in (("old-buy", "BUY", 100), ("old-sell", "SELL", 110),
                                  ("new-buy", "BUY", 100), ("new-sell", "SELL", 120))]
    provenance = capture(synthetic_spec("synthetic-s1").model_copy(update={"max_position_value": 2000}))
    records = [r if i < 2 else r.model_copy(update={"decision": r.decision.model_copy(
        update={"strategy_provenance": provenance})}) for i, r in enumerate(records)]
    if old_unknown:
        records[:2] = [r.model_copy(update={"decision": r.decision.model_copy(
            update={"strategy_provenance": None})}) for r in records[:2]]
    activities = tuple(BrokerFillActivity(
        activity_id=f"synthetic-{r.cycle_id}", order_id=r.execution.order_id,
        client_order_id=r.execution.client_order_id, strategy_id="synthetic-s1", symbol="SPY",
        side=r.decision.action, quantity=1, price=r.snapshot.last_price, fees=fee * (6 if i < 2 else 1),
        transaction_time=STAMP + timedelta(hours=i),
    ) for i, r in enumerate(records))
    evidence = BrokerEvidenceBatch(generated_at=STAMP + timedelta(hours=5), window_start=STAMP,
        window_end=STAMP + timedelta(hours=5), activities=activities)
    report = DeterministicEvaluator().evaluate(records[-2:], evidence=evidence, full_records=records)
    assert sum(report.pnl.strategy_realized_pnl.values()) == 30 - 14 * fee
    assert report.strategy_metrics["synthetic-s1"].cycles == 2
    assert report.strategy_metrics["synthetic-s1"].realized_pnl == 20 - 2 * fee
    assert report.strategy_metrics["synthetic-s1"].fees == 2 * fee


@pytest.mark.parametrize("tainted", [False, True])
@pytest.mark.parametrize("fill_source", ["activities", "orders"])
def test_confirmed_unknown_submission_resolves_alias_without_erasing_taint(tainted, fill_source):
    from test_evaluator import make_record

    from autopoiesis.models import BrokerEvidenceBatch, BrokerFillActivity, BrokerOrderSnapshot

    buy = make_record(cycle_id="buy", action="BUY", quantity=1, strategy_id="opener", status="SUBMISSION_UNKNOWN")
    sell = make_record(cycle_id="sell", action="SELL", quantity=1, strategy_id="closer")
    records = [buy, sell]
    if tainted:
        records.insert(1, buy.model_copy(update={"cycle_id": "synthetic-unknown-retry",
            "decision": buy.decision.model_copy(update={"strategy_provenance": None})}))
    evidence = BrokerEvidenceBatch(generated_at=STAMP + timedelta(hours=3), window_start=STAMP,
        window_end=STAMP + timedelta(hours=3), activities=tuple(BrokerFillActivity(
            activity_id=f"synthetic-{action}", order_id=f"oid-{action}", client_order_id=f"cid-{action}",
            symbol="SPY", side=action.upper(), quantity=1, price=price,
            transaction_time=STAMP + timedelta(hours=i),
        ) for i, (action, price) in enumerate((("buy", 90), ("sell", 110)))))
    if fill_source == "orders":
        evidence = evidence.model_copy(update={"activities": (), "orders": tuple(BrokerOrderSnapshot(
            order_id=a.order_id, client_order_id=a.client_order_id, symbol=a.symbol, side=a.side,
            quantity=a.quantity, filled_quantity=a.quantity, filled_avg_price=a.price,
            status="filled", submitted_at=a.transaction_time, filled_at=a.transaction_time,
        ) for a in evidence.activities)})
    report = DeterministicEvaluator().evaluate(records, evidence=evidence, full_records=records)
    assert sum(report.pnl.strategy_realized_pnl.values()) == 20
    # On the strategy that actually sold. `opener` supplied the cost basis and has a bucket
    # of its own, but no realized PnL: a strategy cannot hold profit it did not realise.
    assert report.strategy_metrics["closer"].realized_pnl == (None if tainted else 20)
    assert report.strategy_metrics["opener"].realized_pnl is None


def test_latest_inconclusive_screen_supersedes_old_pass_and_legacy_pass_is_rejected(tmp_path):
    from test_daemon import FakeLoop, config

    from autopoiesis.daemon import AgentDaemon
    from autopoiesis.journal import JsonlJournal
    from autopoiesis.strategy_engine import StrategyLibrary

    cfg = config(tmp_path)
    library = StrategyLibrary(cfg.strategy_dir)
    spec = synthetic_spec("s1")
    library.save(spec)
    daemon = AgentDaemon(config=cfg, loop=FakeLoop(), journal=JsonlJournal(cfg.journal_path), strategy_library=library)
    legacy = SimpleNamespace(strategy_id="s1", event_id="legacy", payload={"verdict": "PASS_SCREENED", "scored": 100})
    assert daemon._offline_evidence_by_strategy(_validation=[legacy]) == {}
    verified = {"eligibility_version": 1, "strategy_provenance": capture(spec).model_dump(mode="json"), "source_cycle_ids": ["synthetic-c"]}
    passed = SimpleNamespace(strategy_id="s1", event_id="old", payload={**verified, "verdict": "PASS_SCREENED", "scored": 100})
    newer = SimpleNamespace(strategy_id="s1", event_id="new", payload={**verified, "verdict": "INCONCLUSIVE", "scored": 0})
    latest = daemon._offline_evidence_by_strategy(_validation=[passed, newer])
    assert latest["s1"].verdict == "INCONCLUSIVE"


def test_behavior_update_earns_new_learning_without_old_scores_or_budget_reset():
    old = synthetic_clean_record(synthetic_legacy_record())
    spec = synthetic_spec(old.strategy_id).model_copy(update={"max_position_value": 2000})
    new = old.model_copy(update={
        "cycle_id": "synthetic-new-behavior",
        "decision": old.decision.model_copy(update={"strategy_provenance": capture(spec), "action": "BUY", "quantity": 1}),
    })
    memory = ReflectionMemory("unused-synthetic-path")
    reflection = memory.reflect([old, new], full_records=[old, new])
    [result] = memory.strategy_results(reflection, [spec])
    assert result.cycles == 1 and result.action_counts == {"BUY": 1}
    assert result.cumulative_cycles == 2
    assert reflection.strategy_metrics[old.strategy_id]["source_cycle_ids"] == ("synthetic-new-behavior",) or reflection.strategy_metrics[old.strategy_id]["source_cycle_ids"] == ["synthetic-new-behavior"]


def test_changing_actual_cited_data_invalidates_previously_admitted_lesson(tmp_path):
    from autopoiesis.knowledge_admission import KnowledgeAdmission
    from autopoiesis.knowledge_library import KnowledgeLibrary
    from autopoiesis.learning_evidence import source_digest
    from autopoiesis.models import KnowledgeArtifact

    clean = synthetic_clean_record(synthetic_legacy_record())
    library = KnowledgeLibrary(tmp_path / "knowledge")
    library.verify_journal_sources([clean])
    artifact = KnowledgeArtifact(
        artifact_id="synthetic-verified-lesson", artifact_type="LESSON", answer="Synthetic claim",
        summary="Synthetic claim", source_kind="journal", source_refs=(clean.cycle_id,),
        created_at=STAMP, rationale="synthetic",
    )
    assert KnowledgeAdmission(knowledge_library=library).admit(artifact).accepted
    accepted = library.load(artifact.artifact_id)
    assert library.lesson_verified(accepted)
    changed = clean.model_copy(update={"decision": clean.decision.model_copy(update={"confidence": .1})})
    assert source_digest(artifact.source_refs, {clean.cycle_id: clean}) != source_digest(artifact.source_refs, {clean.cycle_id: changed})
    library.verify_journal_sources([changed])
    assert not library.lesson_verified(accepted)


def test_fresh_verified_lesson_can_supersede_withheld_legacy_content(tmp_path):
    from autopoiesis.knowledge_admission import KnowledgeAdmission
    from autopoiesis.knowledge_library import KnowledgeLibrary
    from autopoiesis.models import KnowledgeArtifact

    library = KnowledgeLibrary(tmp_path / "knowledge")
    legacy = KnowledgeArtifact(artifact_id="synthetic-old", artifact_type="LESSON",
        answer="Synthetic identical statement", summary="Synthetic identical statement", source_kind="journal",
        source_refs=("unknown-old-cycle",), created_at=STAMP, rationale="synthetic", status="ACCEPTED")
    library.save(legacy)
    clean = synthetic_clean_record(synthetic_legacy_record())
    library.verify_journal_sources([clean])
    assert not library.lesson_verified(legacy)
    fresh = legacy.model_copy(update={"artifact_id": "synthetic-fresh", "status": "PROPOSED",
        "source_refs": (clean.cycle_id,)})
    result = KnowledgeAdmission(knowledge_library=library).admit(fresh)
    assert result.accepted, result.reason
    assert library.lesson_verified(library.load(fresh.artifact_id))
    assert library.load(legacy.artifact_id).status == "RETIRED"
    assert library.load(legacy.artifact_id).verified_source_digest is None


def test_equal_price_quote_at_horizon_is_observed_zero_return_not_pending():
    from autopoiesis import counterfactual

    first = synthetic_clean_record(synthetic_legacy_record())
    quote = first.model_copy(update={
        "cycle_id": "synthetic-flat-horizon",
        "snapshot": first.snapshot.model_copy(update={"timestamp": STAMP + timedelta(hours=24)}),
    })
    report = counterfactual.evaluate([first, quote])
    row = report.rows[0]
    assert row.price_at_horizon == 100
    assert row.gross_return_pct == 0 and row.net_return_pct == -.05
    assert row.verdict not in {"PENDING", "GAP"}


def test_counterfactual_same_verdict_refreshes_unknown_metadata_and_then_deduplicates(tmp_path):
    from dataclasses import asdict

    from test_daemon import _scored_cycle, _screening_daemon

    from autopoiesis import counterfactual
    from autopoiesis.models import JournalEvent

    daemon, journal = _screening_daemon(tmp_path)
    records = list(_scored_cycle("synthetic-c", price=100, horizon_price=95))
    for record in records:
        journal.append(record)
    rows = [asdict(row) for row in counterfactual.evaluate(records).rows]
    for row in rows:
        row["decided_at"] = row["decided_at"].isoformat()
        for field in ("eligibility_version", "eligibility", "strategy_provenance", "decision_symbol", "decision_strategy_id", "symbol"):
            row.pop(field)
    journal.append_event(JournalEvent(
        event_id="synthetic-legacy-cache", event_type="COUNTERFACTUAL_EVALUATED",
        timestamp=STAMP, status="SUCCESS", payload={"rows": rows},
    ))
    daemon._record_counterfactuals(_events=journal.read_events("COUNTERFACTUAL_EVALUATED"))
    refreshed = journal.read_events("COUNTERFACTUAL_EVALUATED")
    assert len(refreshed) == 2
    assert refreshed[-1].payload["rows"][0]["eligibility"] == "ELIGIBLE"
    assert refreshed[-1].payload["rows"][0]["symbol"] == "SPY"
    daemon._record_counterfactuals(_events=refreshed)
    assert len(journal.read_events("COUNTERFACTUAL_EVALUATED")) == 2


def test_reflection_reuses_one_full_journal_read_for_origins_and_raw_budget(tmp_path, monkeypatch):
    from test_daemon import FakeLoop, config

    from autopoiesis.daemon import AgentDaemon
    from autopoiesis.journal import JsonlJournal

    cfg = config(tmp_path)
    journal = JsonlJournal(cfg.journal_path)
    journal.append(synthetic_legacy_record())
    memory = ReflectionMemory(tmp_path / "reflection.json")
    daemon = AgentDaemon(config=cfg, loop=FakeLoop(), journal=journal, reflection_memory=memory)
    original = journal.read_all
    calls = []

    def read_once():
        calls.append(1)
        assert len(calls) == 1
        return original()

    monkeypatch.setattr(journal, "read_all", read_once)
    daemon._reflect()
    [result] = memory.strategy_results(memory.load())
    assert result.cumulative_cycles == 1 and result.cycles == 0
    assert len(calls) == 1


def test_curriculum_withholds_legacy_strategy_metrics():
    from autopoiesis.curriculum import StructuredCurriculumAgent

    spec = synthetic_spec()
    cached = ReflectionRecord(
        generated_at=STAMP, window_cycles=50, submitted_orders=50, rejected_orders=0,
        error_count=0, summary="synthetic unknown cache", strategy_scores={spec.strategy_id: 1},
        strategy_metrics={spec.strategy_id: {"cycles": 50, "submitted_orders": 50}},
    )
    [row] = StructuredCurriculumAgent()._compact_strategies([spec], cached)
    assert row["score"] is None and row["cycles"] == 0 and row["submitted_orders"] == 0


def test_real_journal_learning_audit_is_explicitly_opt_in():
    """Read-only real audit; skipped in ordinary credential-free fixture tests."""
    import hashlib
    import json
    import os
    from collections import Counter

    import pytest

    from autopoiesis.journal import JsonlJournal

    path = os.environ.get("AUTOPOIESIS_LEARNING_AUDIT_JOURNAL")
    if not path:
        pytest.skip("set AUTOPOIESIS_LEARNING_AUDIT_JOURNAL for the explicit read-only real audit")
    journal = JsonlJournal(path, lock=False)  # Do not create a lock or manifest in real runtime.
    paths = journal.history_paths()
    before = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    records = journal.read_all()
    fingerprint = hashlib.sha256()
    for p in paths:
        with p.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                fingerprint.update(block)
    after = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in journal.history_paths()]
    assert before == after, "journal changed during read-only measurement; rerun on a stable snapshot"
    reasons = Counter(eligibility(record) for record in records)
    report = DeterministicEvaluator().evaluate(records, full_records=records)
    assert report.window_cycles == len(records)
    assert len(calibration.build_rows(records, {})) == sum(
        eligibility(record) == "ELIGIBLE" and record.decision.decision_source == "llm" for record in records
    )
    print(json.dumps({
        "history_files": len(paths), "history_bytes": sum(size for _, size, _ in before),
        "history_sha256": fingerprint.hexdigest(), "parsed_cycles": len(records),
        "attributed_cycles": sum(bool(r.strategy_id or r.decision.strategy_id) for r in records),
        "decision_snapshot_symbol_mismatches": sum(r.decision.symbol != r.snapshot.symbol for r in records),
        "eligibility": dict(reasons), "learning_strategy_buckets": len(report.strategy_metrics),
        "raw_strategy_service_cycles": sum(report.raw_cumulative_cycles.values()),
    }, sort_keys=True))
