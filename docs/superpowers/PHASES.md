# Phases, evidence, and what comes next

This is the fact-level system document the objective asks for: per-phase tasks,
dependencies, test methods, exit criteria and verification results; what is
**proven** versus what is only a **design target**; and the next one to three changes
worth making.

Every number here was produced by running the code against the live journal on
2026-09-28. Nothing is estimated, and where a number is unavailable it says so
rather than carrying a placeholder.

---

## 1. Phase status, with verification results

`make verify` — 21 classes, 0 failed, 614 distinct tests, 91/91 defect audit,
`doctor` RESULT OK, exit 0. `make verify-self-test` — green on 7 conditions.

| phase | exit criteria | verification method | result |
| --- | --- | --- | --- |
| **0** startup / restart / recovery / reconciliation | every class PASS; recovery proven under real failure | `make verify`; SIGKILL test | **MET.** SIGKILL at 07:34:55 → `NRestarts=1`, new `MainPID` at 07:35:25, pidfile rewritten, 920 cycles + 5782 events re-read cleanly, journal re-parsed with zero corrupt lines |
| **1** real trading and PnL attribution | broker-confirmed fills; per-strategy attribution | 24 orders submitted, 24 broker-confirmed fills; FIFO ledger | **MET.** 23 closed lots, +564.39, `broker_strategy_closed_lot_pnl_verified` |
| **2** counterfactual and decision quality | every decision scored against a real later quote, after cost | `point-in-time-no-leakage` class; live report | **MET.** hold quality 0.687 over 350 scored; 67 pending, 50 refused as gaps |
| **3** strategy / model / experiment registry | one source of truth; lineage; champion; failed trials | `lifecycle-invariants`, `data-integrity` classes | **MET.** strategy registry, derived experiment view, lineage and champion all exist. Model registry added: `TradeDecision.model` records which model produced each decision, and `model_registry.py` groups decisions by it |
| **4** backtest / walk-forward / cost / leakage / stress / overfitting | strict OOS, cost on both sides, leakage guard, stress, trial control | `research/` package; `point-in-time-no-leakage`, `pnl-accounting` | **MET, and negative.** 27 of 27 strategies return `INSUFFICIENT`: 3 out-of-sample trades against the 10 required for 27 trials. Not one strategy has enough OOS evidence to be called anything |
| **5** shadow trading | real decision path, no broker contact, no PnL leakage | `shadow-live-consistency` class; live journal | **MET.** `status=SHADOWED`, `order_id=None`, cycle count unchanged 920/920, account realized PnL `None` |
| **6** evidence-gated probation | no promotion without verifiable evidence | `lifecycle-invariants` class; live registry | **MET.** both PROBATION strategies blocked with the reason recorded |
| **7** prospective promote / pause / retire | decisions driven by accumulated prospective evidence | lifecycle runs on evidence and has retired 12 strategies | **INCOMPLETE.** the mechanism works, but prospective *duration* is 1 clean session. No strategy has been promoted or retired on prospective evidence specifically |
| **8** regime-aware allocation, retraining, evolution | — | — | **NOT STARTED, correctly.** Phase 7 has not finished. Regime is *measured* (§3) but nothing allocates by it |

### Dependencies, stated honestly

Phase 4 depends on enough market history to be worth anything, and there is not
enough (§2). Phase 6 depends on Phase 4's evidence existing, and it does not, which is
why both PROBATION strategies are blocked rather than promoted. Phase 7 depends on
elapsed prospective time. Phase 8 depends on all of the above and must not be started
until Phase 7's exit criteria are met — the objective's ordering is a rule, not a
checklist.

---

## 2. The binding constraint: data volume, not configuration

Three separate findings turned out to have one cause.

```
distinct observations : 467
journal spans         : 2026-06-09 → 2026-09-28  (111.4 days)
distinct trading days : 10          ← 9% of elapsed days
mean interval         : 344 min     against a configured 300 s
gap percentiles       : p50 5 min   p90 15 min   p99 1050 min   max 140841 min (97.8 days)
```

The **median** gap is 5 minutes — exactly the configured `daemon_interval_seconds`.
The sampling rate is not the problem. The daemon simply did not run: 10 of 111 days,
with a 97.8-day gap at one point.

| day | bars | first | last | span | median gap |
| --- | --- | --- | --- | --- | --- |
| 2026-06-11 | 28 | 09:30 | 15:59 | 6.5 h | 15 m |
| 2026-06-18 | 24 | 09:30 | 15:59 | 6.5 h | 15 m |
| 2026-09-28 | 67 | 11:20 | 15:59 | 4.7 h | 1 m |

On the days it ran, coverage was a full session. The most recent day shows 1-minute
granularity, better than the 5-minute configured interval.

**This is not a configuration defect and there is nothing to fix in the code.** The
historical gap is unrecoverable. The consequence is arithmetic: 10 trading days cannot
support an out-of-sample claim, a calibration verdict, or a regime attribution, and
every one of those instruments now says so rather than guessing. The constraint
resolves by running, and the current rate is healthy.

---

## 3. Regime: measured, and unusable on this data

`regime.py` derives a point-in-time label from the realized price series. On the live
journal:

```
UNKNOWN           391 bars (84%)
TRENDING_DOWN      76 bars (16%)
contiguous windows: 78 of 467
current regime:    UNKNOWN — the last 78 bars span a gap over 30 minutes
PnL by regime at lot open:  UNKNOWN  23 lots  +564.39
```

Every profitable lot was opened in a window that cannot be labelled, because the
surrounding bars are not contiguous. So regime is `CANNOT ATTRIBUTE` **as a
measurement result**, not as a missing feature — which is a strictly better position
than not knowing that the question could not be answered.

Limitation stated: these are last-trade prints sampled on a 5-minute cycle, not
exchange bars. There is no OHLC, no volume and no VIX in the system, so volatility is
noisier than a bar-based measure and a gap between samples reads as a move that may
have occurred anywhere inside it.

---

## 4. Deliverable ⑥ — what actually caused the PnL

| cause | verdict | evidence |
| --- | --- | --- |
| **model** | **NOT THE CAUSE** | **0.00** of +564.39 came from an `llm` or `fallback` decision. All 23 lots were opened by the `baseline` source, on 2026-06-11, 06-12 and 06-18. The LLM path's first decision was 2026-09-28 11:20 — 2.5 months after the last profitable lot |
| signal | attributable | all 23 lots came from the `baseline` rule, so the PnL belongs to that rule, not to anything the model inferred |
| strategy | attributable | 3 strategies carry it; largest `tiny-fixed-size-001` at +362.66 |
| cost | **not a factor here** | the paper broker charged **0.00** across 23 lots. The figure is gross and overstates what a live venue would have paid |
| allocation | partly attributable | 36 orders were refused, so the PnL is what got through, not what was wanted |
| execution | not a factor here | fill ratio 1.00 — submitted quantity filled in full |
| regime | **cannot attribute** | §3 |

**The headline profit is not evidence that the model works.** That is the most
important single statement in this document.

The two PnL numbers in the payload are **not** a reconciliation and must never be
subtracted from each other:

* `strategy_realized_pnl` **+564.39** — the agent's *closed* lots, SPY only, opened
  2026-06-11 → 06-18.
* `account_realized_or_reported_pnl` **−406.54** — the broker's *cumulative*
  `profit_loss` for the whole account, all time, including BIL and TLT.

Different scopes, different periods. The 970.93 difference is not a loss.

---

## 5. Deliverable ⑦ — proven versus design target

### Proven by evidence on record

| capability | evidence |
| --- | --- |
| real end-to-end trading | 24 orders, 24 broker-confirmed fills, 23 closed lots |
| per-strategy PnL attribution | 3 strategies, `broker_strategy_closed_lot_pnl_verified`, FIFO with opener/closer recorded |
| crash recovery | SIGKILL → self-restart, state intact, zero corrupt lines |
| Guardian cannot be bypassed | 67 tests; a submitted order without Guardian approval fails the gate |
| exposure scoping | verified against the **real** account: 5 positions worth $37,082, all outside the allowlist; a 5-share BUY is approved; the $20,000 cap still binds on the agent's own book at $19,000 |
| decision-outcome scoring | 350 scored decisions, after cost, point-in-time with a proven leakage guard |
| shadow cannot become a fill | 4 shadow orders produce realized PnL `None`; a gate fails if any module groups `SHADOWED` with an executed status |
| promotion requires evidence | both PROBATION strategies blocked with reasons recorded |
| the whole toolchain is green | 21 verify classes, 614 tests, 91/91 audit, self-test on 7 conditions |

### Design targets only — no evidence either way

| target | why it is unproven |
| --- | --- |
| **that the model adds value** | 0.00 of realized PnL; 67 decisions, **0 scored**, mean confidence 0.276 |
| **that any strategy has an edge** | 27 of 27 `INSUFFICIENT` out-of-sample |
| **that hold quality generalises** | 0.687 is 350 in-sample-scored decisions with no walk-forward confirmation |
| **long-term after-cost risk-adjusted live PnL** | 1 clean session of prospective data; costs are 0.00 because paper |
| **regime awareness** | measured, unusable on this data |
| **champion–challenger performance** | champion `fixed-size-buy-001` (+120.37) exists; no challenger has been measured against it |
| **model provenance for the 920 existing decisions** | the field was added after they were written, so all 920 report `UNATTRIBUTED`. They are deliberately **not** back-filled: assigning them the currently configured model would assert a fact about the past that is not known, and would make every future comparison look like evidence for a model that never ran |
| **unrealized PnL** | not implemented; only realized is attributed |

---

## 6. Deliverable ⑧ — the next three changes

Chosen by what unblocks the most, not by what is most interesting to build.

### 1. Run, and let the evidence accumulate

Everything above is blocked on the same thing: **10 trading days**. The highest-value
action is not a change at all — it is to keep the daemon running through enough
sessions that Phase 7 can close.

The one mechanical change that protects that accumulation is making the systemd units
reboot-persistent. `FragmentPath=/run/user/29424/systemd/user/min-agent.service`
confirms they live in `/run`, which is a tmpfs and is empty after a reboot. They
cannot be moved to `~/.config/systemd/user` because **`mkdir` there fails with
`Disk quota exceeded`**.

`df -h ~` reports 1.8T free on `/home`, which looks like there is plenty of space and
is misleading: `df` shows the filesystem, and the limit that bites is a *per-user
quota*. `$HOME` holds 11G, of which `.cache` is 835M and `.local` is 707M. Freeing
either would lift the quota and let `minictrl install-service` write a permanent unit.
The watchdog is what limits the damage today — it restarts the daemon, but it cannot
survive a reboot that clears `/run`.

Expected effect, stated in advance so it can be falsified: after ~20 additional
trading days, the 67 pending LLM decisions resolve, `model calibration` moves off
`INSUFFICIENT`, and at least one strategy crosses the 10-out-of-sample-trade bar.

### 2. Unrealized PnL

Only realized PnL is attributed, so open lots contribute nothing to the account
picture and the account figure and the agent figure cannot be reconciled even in
principle. Small, and it removes a standing source of the confusion §4 documents.

### Done since this was written

The Phase 3 model-registry gap is **closed**: `TradeDecision.model` now records which
model produced each decision, and `model_registry.py` groups them. It is a view over
the journal rather than a store, so it cannot drift. The 920 existing cycles report
`UNATTRIBUTED` and are not back-filled; the warning clears itself as soon as the
market is open and new decisions are stamped.

### Explicitly not next

Phase 8 (regime-aware allocation, retraining, evolution). Its exit criteria are not
met, and the objective forbids entering it early. Building it now would produce a
system that looks more autonomous and is not more profitable — which is the failure
mode the whole objective is written against.
