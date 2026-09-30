# Capability classification

The objective's first requirement is not a list of features. It is a judgement,
per capability, about whether the thing actually reaches the production decision
path. Everything below is classified from evidence gathered in this session, and
the evidence is named so the claim can be re-checked rather than taken on trust.

Categories are the objective's own: **actively used**, **partially wired**,
**implemented but unused**, **obsolete**, **duplicated**, **broken**, **missing**.

Every count below was re-verified against the journal immediately before this
file was written, and the figures that move as the daemon runs are stated
explicitly rather than left to rot. Offline verdicts had already drifted once
during this session - 20 strategies with 14 inconclusive became 22 with 16 - which
is why they are quoted with the as-of caveat in the closing section.

Three rules were applied, each of which exists because I got it wrong first:

1. **A symbol referenced only by its own file is alive.** A first dead-code scan
   excluded each symbol's defining file and reported 59 unreferenced symbols.
   Counting real AST name loads across all production files gave **3**.
2. **A check that cannot fail is not a control.** `_check_admission_provenance`
   was wired, tested, and green while the library held five strategies the gate
   had never approved, three of which traded 33 broker-confirmed fills.
3. **Report what the code does, not what its name suggests.** `evaluate_cycles`
   looked like the evaluator's entry point; nothing called it, and all four
   importing modules construct `DeterministicEvaluator` directly.

---

## Actively used

Reaches a production decision, and the evidence is in the journal.

| capability | evidence |
| --- | --- |
| observe → snapshot | 987 cycle records, every one `source: alpaca`, zero synthetic sources |
| Guardian risk gate | 34 submitted orders, 0 bypasses; SELL bounded by agent-owned holdings |
| execute → broker fill | 34 `ORDER_FILL_CONFIRMED`, all `paper_only`, 85.0 shares |
| reconcile | `ORDER_RECONCILIATION_REVIEWED` present from the journal's first event |
| PnL attribution | +564.39 across 23 closed lots, cost assumption reported separately |
| counterfactual | 89 `COUNTERFACTUAL_EVALUATED`, hold_quality 0.736 over 416 scored |
| reflection | 710 `REFLECTION_GENERATED` |
| offline validation | 1377 `OFFLINE_VALIDATION_COMPLETED`; 22 strategies have a latest verdict |
| knowledge → decision | `relevant_lessons` → `context["lessons"]` → the model's prompt. Wired end to end; see *partially wired* for the content problem |
| experiment registry | 39 strategies, 26 admitted, verdict distribution reported |
| model calibration | Brier computed over 63 scored llm decisions |
| immutable hard limits | `risk limits vs baseline` unchanged; paper endpoint asserted |
| systemd durability | `units-where-systemd-looks` passes; 0 enablement links into tmpfs |

## Partially wired

Reaches production, but delivers materially less than its shape suggests.

| capability | what is partial | evidence |
| --- | --- | --- |
| **knowledge → decision** | The path is closed, but **10 of 11 accepted lessons share one identical answer**, and `max_lessons=5` fills all five slots with the same text. The mechanism delivers one distinct idea where it appears to deliver five. | 11 artifacts, `distinct answer strings: 2`, doctor reports "2 distinct statement(s)" and passes |
| **curriculum exploration** | 259 proposals, 28 admitted, and the last 4 proposals were the same id. The model is now shown its own refusals, but feeding them back changed the *name* (`-002` → `-003`), not the trade. The cause is saturation, not prompting: for `FIXED_SIZE`+`SELL`+`SPY`, `quantity 1` / `max_position_value 5000` are near the only admissible values, so every correct FIXED_SIZE sell duplicates the one held. | guard verified fail-closed against the real library |
| **experiment chain** | 11 of 39 chains incomplete. Defensible as WARN: no *tradable* strategy lacks evaluation evidence, and the gaps predate the offline screen. | `tradable_without_evidence` empty |
| **model provenance** | Only 67 of 987 decisions name a model. Now a WARN (was OK at 93% unattributed). | 920 unattributed |
| **model calibration** | Computed and reported, and the verdict is `MIS-CALIBRATED`, but nothing consumes it: the mis-calibration has not changed how the model is prompted or gated. | Brier 0.589 vs 0.25 constant baseline |

## Implemented but unused

Present, reachable in code, and not exercised by the run.

| capability | evidence |
| --- | --- |
| `regime.py` classifier | Referenced only as a *label* in `attribution.py`. Phase 8 is not started, so this is by design, not an oversight — but it means "regime" in the attribution output is currently `UNKNOWN` for every lot |
| shadow mode | `config.shadow = False`; 1 `SHADOW_ORDER_INTENT` in 6909 events, ever |
| champion / challenger | `champion=NONE` — no strategy has broker-verified positive realized PnL, so nothing has ever been promoted |

## Duplicated — deleted this session

| removed | why it was duplication |
| --- | --- |
| `evaluator.evaluate_cycles` | Wrapper over `DeterministicEvaluator().evaluate`; all four importing modules construct the class directly |
| `models.DaemonState` | A second, never-validated schema for daemon state, describing `heartbeat.json` field-for-field minus the `pid` that `HeartbeatPayload` — the real, used contract — has |
| `curriculum.json_text` | One-line wrapper over `json.dumps` |

A scan for symbols with zero production references now returns nothing. A scan of
all 35 `AgentConfig` fields and all 22 `.env.optimized` keys finds no key that
nothing reads.

## Broken — found and repaired this session

Each of these reported success while something real was wrong.

| capability | the failure | now |
| --- | --- | --- |
| **admission gate on library membership** | 5 files entered the library after the gate existed; 3 traded 33 broker-confirmed fills; check reported WARN hedged as "legacy **or** written outside the gate" — a hedge the journal resolves itself | post-gate entry is FAIL; refused + not selectable is a complete disposition; refused + selectable is its own worse finding |
| **lifecycle provenance** | Skipped the default states, so it caught a move *into* PAUSED/RETIRED with no decision but not a move *out* — which is how my own first adjudication slipped through | any disagreement in either direction is unaccounted for |
| **model registry severity** | All-or-nothing threshold, so 93% unattributed reported OK on the strength of 67 named decisions | proportional; inclusive boundary at half |
| **broker positions** | Reported a count only, so $37,074 of non-allowlist exposure was invisible | names the composition, and reports unmanaged exposure with its share of equity |
| **systemd unit location** | Units written where the manager never looks; `is-enabled` said "enabled" while the symlink pointed into tmpfs, so a reboot would have left dangling links | asks the manager; refuses tmpfs unless overridden |
| **curriculum context window** | One `num_ctx=4096` applied to two workloads differing 13x in prompt size, truncating every curriculum response to an empty body | per-site windows, both measured |

## Missing

| capability | what is missing |
| --- | --- |
| **visibility of unmanaged account exposure** | Now reported. It did not exist: `daily_loss` is whole-account equity, so a drawdown in BIL or TLT could consume the agent's $500 daily-loss budget for a reason appearing in no decision, PnL record or attribution |
| ~~knowledge pruning~~ | **Closed.** 11 artifacts carrying 2 statements became 2 carrying 2, by merging `source_refs` rather than deleting: the ten copies cited 83 distinct cycle ids between them and the journal records those ids nowhere else, so a plain delete would have destroyed lineage |
| ~~failure-path tests for doctor checks~~ | **Closed.** 10 checks had no test asserting their non-OK path; all 30 report names are now covered, including `champion / search` and `experiment chain`, whose fixtures needed a journal seeded with specific admission and evaluation events plus a real strategy library |
| **attribution for 29 shares** | Sold in June before the SELL bound existed. The cost basis is not in the journal, so that realized PnL is permanently unproven. Correctly reported rather than invented |

---

## What this classification does not establish

- It says nothing about whether the system makes money. Nothing here is evidence
  of profitability.
- `27/27 strategies INSUFFICIENT` was false and I repeated it: as of this writing
  22 strategies have a verdict and **4 are `PASS_SCREENED`**, 16 inconclusive, 2
  rejected. The "need 52 out-of-sample events" bar does not exist in the code; the
  real bar is the 10 informative decisions named in the verdicts. These counts
  grow while the market is open, so treat them as of this commit.
- The full event-by-event replay of all 7,896 journal records has not been
  performed. Every figure here comes from aggregate queries.
- "No dead symbols" is a statement about module-level names. Methods reached only
  dynamically, and a symbol referenced only by tests, would not be caught.

The binding constraint on the goal is not code. It is market time: 70 decisions
await a 24-hour horizon, the model has 10 open SPY shares and **zero** closed
lots, no strategy has broker-verified positive realized PnL, and after assumed
cost the +556.77 belongs entirely to the baseline rule rather than to anything
the model chose.
