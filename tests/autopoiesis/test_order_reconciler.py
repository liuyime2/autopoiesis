from datetime import datetime, timezone

from autopoiesis.journal import JsonlJournal
from autopoiesis.models import (
    AccountSnapshot,
    CycleRecord,
    DataSnapshot,
    ExecutionResult,
    GuardianResult,
    JournalEvent,
    TradeDecision,
)
from autopoiesis.order_reconciler import OrderReconciler


def make_record(*, cycle_id, order_id, status="SUBMITTED"):
    snapshot = DataSnapshot(
        symbol="SPY",
        timestamp=datetime(2026, 6, 9, 14, 30, tzinfo=timezone.utc),
        market_open=True,
        last_price=100,
        source="alpaca",
        account=AccountSnapshot(
            equity=100_000, cash=100_000, buying_power=100_000, portfolio_value=100_000, daily_loss=0
        ),
    )
    return CycleRecord(
        cycle_id=cycle_id,
        snapshot=snapshot,
        decision=TradeDecision(symbol="SPY", action="HOLD", quantity=0, confidence=0.2, rationale="wait"),
        guardian=GuardianResult(approved=True, reason="approved"),
        execution=ExecutionResult(
            status=status,
            order_id=order_id,
            client_order_id=f"autopoiesis-{cycle_id}",
            filled_quantity=0,
            submitted_at=datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc),
            message="submitted",
        ),
    )


class FakeAlpacaClient:
    def __init__(self, open_order_ids=None):
        self._open_orders = open_order_ids or []

    def list_orders(self, status: str = "open"):
        return [type("Order", (), {"id": oid})() for oid in self._open_orders]


class FailingAlpacaClient:
    def list_orders(self, status: str = "open"):
        raise RuntimeError("broker unavailable")


def test_reconciler_reports_matched_when_both_empty(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = FakeAlpacaClient(open_order_ids=[])
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is True
    assert report.submitted_order_ids == ()
    assert report.broker_open_order_ids == ()


def test_reconciler_reports_matched_when_all_orders_resolved(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(make_record(cycle_id="c1", order_id="oid-1"))
    journal.append(make_record(cycle_id="c2", order_id="oid-2"))
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-orders-1", reason="test baseline"
    )
    client = FakeAlpacaClient(open_order_ids=[])
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is True
    assert len(report.submitted_order_ids) == 2
    assert report.missing_from_broker == ()
    assert report.extra_broker_orders == ()


def test_reconciler_reports_divergence_when_broker_has_open_order(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.append(make_record(cycle_id="c1", order_id="oid-1"))
    journal.append(make_record(cycle_id="c2", order_id="oid-2"))
    journal.attest_archive_baseline(
        operator_id="test-operator", evidence_reference="test-orders-2", reason="test baseline"
    )
    client = FakeAlpacaClient(open_order_ids=["oid-extra"])
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is False
    assert "broker" in report.message


def test_reconciler_reports_unmatched_when_broker_fetch_fails(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    client = FailingAlpacaClient()
    reconciler = OrderReconciler(client=client, journal=journal)

    report = reconciler.reconcile()

    assert report.matched is False
    assert "fetch failed" in report.message


def test_reconciler_resolves_intent_by_exact_client_order_id_and_projects_known_id(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    client_order_id = "autopoiesis-ambiguous"
    journal.append_event(JournalEvent(
        event_id="intent-1",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        cycle_id="cycle-unknown",
        strategy_id="s1",
        payload={"client_order_id": client_order_id, "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))

    class Broker(FakeAlpacaClient):
        def get_order_by_client_order_id(self, requested):
            assert requested == client_order_id
            return type("Order", (), {
                "id": "broker-accepted",
                "client_order_id": client_order_id,
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
            })()

    reconciler = OrderReconciler(
        client=Broker(open_order_ids=["broker-accepted"]), journal=journal
    )
    report = reconciler.reconcile()

    assert report.matched is True
    assert report.submitted_order_ids == ("broker-accepted",)
    assert report.unresolved_client_order_ids == ()
    resolved = journal.read_events("ORDER_SUBMISSION_RESOLVED")
    assert len(resolved) == 1
    assert resolved[0].payload["client_order_id"] == client_order_id


def test_reconciler_keeps_not_found_intent_unresolved_and_fails_closed(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    journal.append_event(JournalEvent(
        event_id="intent-not-found",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        payload={"client_order_id": "cid-missing", "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))

    class Broker(FakeAlpacaClient):
        def get_order_by_client_order_id(self, _requested):
            return None

    report = OrderReconciler(client=Broker(), journal=journal).reconcile()

    assert report.matched is False
    assert report.unresolved_client_order_ids == ("cid-missing",)
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []


def test_acknowledged_order_without_cycle_is_startup_known(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    journal.append_event(JournalEvent(
        event_id="ack-without-cycle",
        event_type="ORDER_SUBMISSION_ACKNOWLEDGED",
        timestamp=datetime.now(tz=timezone.utc),
        status="SUCCESS",
        payload={"client_order_id": "cid-ack", "order_id": "oid-ack", "symbol": "SPY"},
    ))

    report = OrderReconciler(
        client=FakeAlpacaClient(open_order_ids=["oid-ack"]), journal=journal
    ).reconcile()

    assert report.matched is True
    assert report.submitted_order_ids == ("oid-ack",)


def test_replaced_ambiguous_order_remains_blocked_until_chain_is_supported(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-replaced"
    journal.append_event(JournalEvent(
        event_id="intent-replaced",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        payload={"client_order_id": coid, "symbol": "SPY", "action": "BUY", "quantity": 1},
    ))

    class Broker(FakeAlpacaClient):
        def get_order_by_client_order_id(self, _requested):
            return type("Order", (), {
                "id": "old-order", "client_order_id": coid, "status": "replaced",
                "filled_qty": "0", "replaced_by": "new-order",
            })()

    report = OrderReconciler(client=Broker(), journal=journal).reconcile()

    assert report.matched is False
    assert report.unresolved_client_order_ids == (coid,)
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []


class _Order:
    """A broker order as the walk sees one: id, status, and what it points at."""

    def __init__(self, order_id, status, client_order_id=None, replaced_by=None,
                 symbol="SPY", side="BUY", qty=1, filled_qty=0.0, filled_avg_price=None):
        self.id = order_id
        self.status = status
        self.client_order_id = client_order_id
        self.replaced_by = replaced_by
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.filled_qty = filled_qty
        self.filled_avg_price = filled_avg_price


class _ChainClient:
    """A broker that answers both lookups, so a replacement chain can be walked."""

    def __init__(self, by_coid=None, by_id=None, raise_for_id=()):
        self._by_coid = by_coid or {}
        self._by_id = by_id or {}
        self._raise_for_id = set(raise_for_id)
        self.queried_ids: list[str] = []

    def list_orders(self, status: str = "open"):
        return []

    def get_order_by_client_order_id(self, coid):
        return self._by_coid.get(coid)

    def get_order(self, order_id):
        self.queried_ids.append(order_id)
        if order_id in self._raise_for_id:
            raise ConnectionError("ConnectTimeout to paper-api.alpaca.markets")
        return self._by_id.get(order_id)


def _intent(journal, coid, symbol="SPY", action="BUY", quantity=1):
    journal.append_event(JournalEvent(
        event_id=f"intent-{coid}",
        event_type="ORDER_SUBMISSION_INTENT",
        timestamp=datetime.now(tz=timezone.utc),
        status="ACCEPTED",
        payload={"client_order_id": coid, "symbol": symbol, "action": action,
                 "quantity": quantity},
    ))


def test_a_chain_that_ends_at_a_fill_is_resolved_and_the_fill_is_projected(tmp_path):
    """A verified chain is a known outcome, so the block clears and the fill lands.

    This is the half of the feature the walk alone could not do: walking and recording left the
    block on forever, which is safe and a dead end. An ambiguous submit blocks the whole agent
    until a human intervenes, and a chain verified end to end is exactly the case where that
    intervention is not needed.

    The fill is projected against the *original* client order id, so it stays attributable to the
    submission that placed it even though the broker's order id is the replacement's. Clearing the
    block without projecting the fill would be the "29 unmatched sells" defect: a position in the
    account that no ledger knows about.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-chain"
    _intent(journal, coid)
    client = _ChainClient(
        by_coid={coid: _Order("old-order", "replaced", client_order_id=coid,
                              replaced_by="new-order")},
        by_id={"new-order": _Order("new-order", "filled", filled_qty=1.0,
                                   filled_avg_price=500.0)},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert client.queried_ids == ["new-order"], "the replacement was looked up, not guessed"
    assert report.unresolved_client_order_ids == (), "a verified chain clears the block"
    assert report.matched is True
    # The fill is projected under the original client order id, with the replacement's facts.
    [snapshot] = journal.read_events("ORDER_FILL_SNAPSHOT")
    payload = snapshot.payload
    assert payload["client_order_id"] == coid
    assert payload["order_id"] == "new-order"
    assert payload["filled_quantity"] == 1.0
    assert payload["filled_avg_price"] == 500.0
    assert payload["broker_status"] == "filled"
    # And the shape is the one the evaluator projects, not a second spelling of it.
    from autopoiesis.fill_reconciler import confirmed_fill_events

    projected = confirmed_fill_events(journal.read_events())
    assert [e.payload["client_order_id"] for e in projected] == [coid]
    # The chain is still on the record, with the resolution beside it.
    [resolved] = journal.read_events("ORDER_SUBMISSION_RESOLVED")
    assert [link["order_id"] for link in resolved.payload["replacement_chain"]] == [
        "old-order", "new-order",
    ]
    assert journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED") == []


def test_a_chain_that_ends_canceled_clears_the_block_without_a_fill(tmp_path):
    """"This order did not trade" is an answer, not a gap.

    Knowing the replacement was canceled with nothing filled is a known outcome for the original
    submission, so the block clears - but no fill is projected, because none happened. A page or a
    ledger that inferred one from the fact of the order existing would be inventing a position.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-canceled-chain"
    _intent(journal, coid)
    client = _ChainClient(
        by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
        by_id={"n": _Order("n", "canceled", filled_qty=0.0)},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.unresolved_client_order_ids == ()
    assert journal.read_events("ORDER_FILL_SNAPSHOT") == []
    [resolved] = journal.read_events("ORDER_SUBMISSION_RESOLVED")
    assert "did not trade" in resolved.payload.get("message", resolved.message)
    assert resolved.payload["filled_quantity"] == 0.0


def test_a_chain_ending_partial_keeps_the_block_but_projects_its_progress(tmp_path):
    """A partial fill is not a known outcome yet - more of it can still arrive.

    The cumulative progress is projected so the ledger sees what has filled so far, which is the
    same cumulative-idempotent projection the fill reconciler uses. The block stays on, because
    clearing it while the order can still fill would let the next cycle trade on a position that
    is not what it will end up being.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-partial-chain"
    _intent(journal, coid, quantity=3)
    client = _ChainClient(
        by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
        by_id={"n": _Order("n", "partially_filled", qty=3, filled_qty=1.0,
                           filled_avg_price=500.0)},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.unresolved_client_order_ids == (coid,), "not terminal yet"
    [snapshot] = journal.read_events("ORDER_FILL_SNAPSHOT")
    assert snapshot.payload["filled_quantity"] == 1.0
    assert snapshot.payload["cumulative_filled_quantity"] == 1.0
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []


def test_a_chain_whose_replacement_is_missing_stays_blocked_and_says_why(tmp_path):
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-missing"
    _intent(journal, coid)
    client = _ChainClient(
        by_coid={coid: _Order("old-order", "replaced", client_order_id=coid,
                              replaced_by="gone-order")},
        by_id={},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.unresolved_client_order_ids == (coid,)
    [event] = journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED")
    assert "does not exist at the broker" in event.payload["reason"]
    assert [link["order_id"] for link in event.payload["observed_chain"]] == ["old-order"]


def test_a_chain_whose_replacement_is_a_different_order_is_refused(tmp_path):
    """A link that is not the order we submitted ends the walk, and says which field disagreed.

    This is the case the identity check exists for: a replacement for a different symbol or side
    is not the order we sent, and projecting its fill would record a trade we did not make.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-other-symbol"
    _intent(journal, coid, symbol="SPY")
    client = _ChainClient(
        by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
        by_id={"n": _Order("n", "filled", symbol="QQQ", filled_qty=1.0)},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.unresolved_client_order_ids == (coid,)
    [event] = journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED")
    assert "does not match the submitted order" in event.payload["reason"]
    assert "QQQ" in event.payload["reason"]
    # Only the head is on the record: the mismatched link was not accepted into the chain.
    assert [link["order_id"] for link in event.payload["observed_chain"]] == ["old"]


def test_a_chain_that_loops_is_bounded_and_refused(tmp_path):
    """A self-referential or cyclic `replaced_by` must not spin inside a startup path."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-loop"
    _intent(journal, coid)
    client = _ChainClient(
        by_coid={coid: _Order("a", "replaced", client_order_id=coid, replaced_by="b")},
        by_id={
            "b": _Order("b", "replaced", replaced_by="a"),
            "a": _Order("a", "replaced", replaced_by="b"),
        },
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.unresolved_client_order_ids == (coid,)
    [event] = journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED")
    assert "repeats" in event.payload["reason"]
    # Two links walked, then stopped: the walk is bounded by the orders it has already seen.
    assert len(event.payload["observed_chain"]) == 2


def test_an_unreadable_replacement_ends_the_walk_rather_than_raising(tmp_path):
    """A broker that cannot answer about the replacement leaves the block on and records why."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-timeout"
    _intent(journal, coid)
    client = _ChainClient(
        by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
        by_id={"n": _Order("n", "filled")},
        raise_for_id={"n"},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.unresolved_client_order_ids == (coid,)
    [event] = journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED")
    assert "could not be read" in event.payload["reason"]
    # The exception's *type* is recorded, not the broker's message. A message can carry the
    # host, the URL and the request; a type is what an operator acts on, and copying a
    # third party's error text into an append-only journal is how host details end up in a
    # record nobody sanitises.
    assert "ConnectionError" in event.payload["reason"]


def test_a_client_without_order_lookup_records_that_the_chain_was_not_walked(tmp_path):
    """Not being able to walk the chain is a different fact from the chain ending, and both are
    recorded: one needs a different client, the other needs an operator."""
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-no-lookup"
    _intent(journal, coid)

    class NoLookup(FakeAlpacaClient):
        def get_order_by_client_order_id(self, _coid):
            return _Order("old", "replaced", client_order_id=coid, replaced_by="n")

    report = OrderReconciler(client=NoLookup(), journal=journal).reconcile()

    assert report.unresolved_client_order_ids == (coid,)
    [event] = journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED")
    assert event.payload["reason"] == "no order lookup by id"
    assert event.payload["observed_chain"] == []


def test_recovery_after_a_broker_timeout_mid_chain_reaches_the_same_outcome(tmp_path):
    """The recovery case the runbook names: a timeout inside the walk, then a restart.

    A submit whose response was ambiguous blocks trading. The first reconcile attempt reaches the
    replacement link, the broker times out on it, and the block stays - which is correct and is
    what the runbook calls "operator investigation". Then the broker answers, the next reconcile
    walks the same chain to its end, and the order is resolved from the *journal alone*: the
    unresolved set is recomputed from the whole history, so a fresh process with no in-memory state
    arrives at the same verdict.

    This is the property that matters for a restart, and it is why the walk is a pure function of
    the broker and the journal rather than of anything held in memory.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-recovery"
    _intent(journal, coid)

    timed_out = _ChainClient(
        by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
        by_id={"n": _Order("n", "filled", filled_qty=1.0, filled_avg_price=500.0)},
        raise_for_id={"n"},
    )
    report_before = OrderReconciler(client=timed_out, journal=journal).reconcile()

    assert report_before.unresolved_client_order_ids == (coid,)
    [event] = journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED")
    assert "could not be read" in event.payload["reason"]
    assert event.payload["reason"].endswith("ConnectionError"), (
        "the exception type is recorded, not the broker's message"
    )
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []

    # Restart: a new journal object over the same file, and a broker that now answers.
    recovered = JsonlJournal(tmp_path / "journal.jsonl")
    answering = _ChainClient(
        by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
        by_id={"n": _Order("n", "filled", filled_qty=1.0, filled_avg_price=500.0)},
    )
    report_after = OrderReconciler(client=answering, journal=recovered).reconcile()

    assert report_after.unresolved_client_order_ids == (), "restart resolves what it could not"
    assert report_after.matched is True
    [snapshot] = recovered.read_events("ORDER_FILL_SNAPSHOT")
    assert snapshot.payload["filled_quantity"] == 1.0
    # Both passes are on the record, in order: the failure and then the recovery.
    both = recovered.read_events()
    kinds = [e.event_type for e in both]
    assert kinds.index("ORDER_REPLACEMENT_CHAIN_UNVERIFIED") < kinds.index(
        "ORDER_FILL_SNAPSHOT"
    )


def test_a_second_pass_does_not_double_project_a_fill(tmp_path):
    """Re-running recovery must not project the same fill twice.

    `confirmed_fill_events` keeps the *latest* snapshot per client order id, so a second pass
    overwrites its predecessor rather than adding to it. If it accumulated, every restart would
    inflate the ledger's filled quantity and the agent would trade on a position it never took.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    coid = "cid-idempotent"
    _intent(journal, coid)

    def client():
        return _ChainClient(
            by_coid={coid: _Order("old", "replaced", client_order_id=coid, replaced_by="n")},
            by_id={"n": _Order("n", "filled", filled_qty=2.0, filled_avg_price=501.0)},
        )

    first = OrderReconciler(client=client(), journal=journal).reconcile()
    assert first.unresolved_client_order_ids == ()
    snapshots = journal.read_events("ORDER_FILL_SNAPSHOT")
    assert len(snapshots) == 1

    # A second pass over the same history, with the same broker answer.
    second = OrderReconciler(client=client(), journal=journal).reconcile()
    assert second.unresolved_client_order_ids == ()
    assert len(journal.read_events("ORDER_FILL_SNAPSHOT")) == 1, (
        "a second pass must not project the same fill again"
    )

    from autopoiesis.fill_reconciler import confirmed_fill_events

    projected = confirmed_fill_events(journal.read_events())
    assert len(projected) == 1
    assert projected[0].payload["filled_quantity"] == 2.0


def test_a_replaced_order_with_no_pending_intent_is_not_resolved_from_thin_air(tmp_path):
    """The walk only runs for an order this system submitted.

    `_resolve_unknowns` iterates the journal's unresolved intents. An order the broker reports as
    replaced that we have no intent for is not ours to resolve, and inventing a resolution for it
    would put a fill in the ledger for a trade nobody here made.
    """
    journal = JsonlJournal(tmp_path / "journal.jsonl")
    journal.archive_manifest_status()
    foreign = "cid-not-ours"
    client = _ChainClient(
        by_coid={foreign: _Order("foreign", "replaced", client_order_id=foreign,
                                 replaced_by="n")},
        by_id={"n": _Order("n", "filled", filled_qty=1.0)},
    )

    report = OrderReconciler(client=client, journal=journal).reconcile()

    assert report.matched is True
    assert journal.read_events("ORDER_FILL_SNAPSHOT") == []
    assert journal.read_events("ORDER_SUBMISSION_RESOLVED") == []
    assert journal.read_events("ORDER_REPLACEMENT_CHAIN_UNVERIFIED") == []
