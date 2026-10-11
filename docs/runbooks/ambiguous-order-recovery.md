# Paper order ambiguity and journal recovery

This procedure applies when startup reconciliation reports an unresolved client order ID, when
an order submission response was lost, or when the journal archive baseline is not trusted. The
agent is paper-only. An unresolved attempt blocks every later non-HOLD order and has no expiry.
Do not clear it or submit a replacement merely because an exact-ID lookup returned no order.

## Preserve and investigate an ambiguous submission

1. Stop the paper daemon with the documented `make stop` target. Do not delete or edit journal
   files. Copy the active journal, `journal-archive/`, heartbeat, and daemon logs to a restricted
   evidence directory and record checksums before investigating.
2. Run the read-only `make status` and `make doctor` reports and preserve their output. Keep the
   agent stopped if reconciliation is not matched.
3. In the Alpaca paper account, inspect order history by the exact `client_order_id` and a broad
   time window around the durable `ORDER_SUBMISSION_INTENT.timestamp`. Inspect `order_id`,
   status, `filled_qty`, `filled_avg_price`, and the account's positions/activity. A not-found
   response, timeout, or empty open-order list is not proof that the POST was rejected: the order
   may have filled or become terminal and disappeared from open orders.
4. If positive broker evidence identifies the order, preserve the broker response and use normal
   `make reconcile` recovery. Reconciliation records `ORDER_SUBMISSION_RESOLVED` only for an exact
   match on the requested client ID and a non-replaced broker order. Fill snapshots are separately
   journaled as `ORDER_FILL_SNAPSHOT` and remain cumulative broker observations.
5. If the broker cannot establish the disposition, keep the agent stopped and contact broker
   support through the account's normal support channel. Record the support case/reference and
   resolution. Do not manually clear the intent or authorize an automatic resubmit. Any future
   manual-resolution feature must append an immutable `ORDER_SUBMISSION_MANUALLY_RESOLVED` event
   with operator identity, timestamp, evidence reference, and terminal disposition; it must not
   imply that another order is safe to send.
6. A broker status of `replaced` remains blocked until the replacement chain can be positively
   reconciled and represented in the fill ledger. Do not treat it as terminal no-fill.

## Attest an existing journal archive baseline

The archive manifest proves continuity only from the baseline recorded in it forward. A legacy
journal created before manifests cannot prove that older generations were never lost. Before
attesting, inspect and preserve all `journal-archive/` generations and the active journal, verify
they are the intended complete baseline, and record an external evidence reference. The command
validates every cycle/event line and rejects a corrupt or already-invalid manifest. It cannot
repair missing history, and it does not resolve ambiguous orders.

Set these values in the operator's shell; they are not stored in the repository or credential
file:

```sh
export AUTOPOIESIS_OPERATOR_ID='your audited operator identity'
export AUTOPOIESIS_EVIDENCE_REFERENCE='ticket-or-evidence-id'
export AUTOPOIESIS_ATTESTATION_REASON='why this local history is the trusted baseline'
make attest-archive-baseline
```

An existing trusted manifest cannot be replaced by this command. A missing or corrupt manifest
must be investigated and restored from preserved evidence rather than re-attested. After the
operation, retain the manifest and command output alongside the evidence. Re-run `make doctor`
and `make reconcile`; start paper operation only after journal integrity and broker order state
are both clean.
