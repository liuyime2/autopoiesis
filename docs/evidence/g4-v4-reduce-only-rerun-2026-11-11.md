# G4 v4 re-run under the reduce-only loss limit (2026-11-11)

**Not a registered result.** The registered G4 v4 run (`reports/20261107/v4/`, result SHA-256 `376bd9cc…`, verdict `NOT_PROVEN`) is unchanged and stays the record of that experiment. This note records what happens to it when the Guardian's daily-loss rule changes, because `research/experiment.py` builds the production Guardian and the registered hash depends on its behaviour.

## What changed in the rule

At `daily_loss >= max_daily_loss` the Guardian used to refuse every non-HOLD order. It now lets a SELL or COVER that is within the agent's own holding go on to the remaining checks; BUY and SHORT are still refused; orders from the core sleeve (`strategy_id == "core-sleeve"`) keep the full refusal. The replay Guardian is now given the sleeve's id, as production's is, so the replay still mirrors production.

## Measurements

All runs: `configs/experiment_portfolio_v4.spec`, the same seven satellites and adjusted daily bars as the registered run (arguments from `reports/20261107/v4/run_manifest.json`), real cached data, no broker.

| Run | Guardian | Result SHA-256 |
|---|---|---|
| Registered 11-07 | old rule | `376bd9cc6434dbff…` |
| Control, 11-11 | old rule, from `HEAD` | `376bd9cc6434dbff…` (reproduces exactly, so nothing else has drifted) |
| First rerun, 11-11 | new rule, replay without the sleeve id | `0b1b9697720d2387…` (arm A moved too, because the replay let the sleeve sell at breach; this is not production's behaviour and is not used) |
| **Rerun, 11-11** | **new rule, replay given the sleeve id** | **`ad9f9e908cc82c1e…`** (`reports/20261111/v4-reduce-only/`; its manifest records `0031bfa` because this change was not yet committed when it ran) |

Orders the new rule approved while `daily_loss >= limit`, over 1,580 days and every arm (a wrapper around `Guardian.review`, script below): **one**.

| Date | Arm | Order | Daily loss |
|---|---|---|---:|
| 2025-02-27 | B (NVDA satellite) | SELL 37 NVDA | $544.64 against a $500 limit |

Effect of that one order:

| | Registered | Reduce-only | Change |
|---|---:|---:|---:|
| A final equity | 66,247.88 | 66,247.88 | none |
| B final equity | 89,137.93 | 88,936.34 | −201.59 |
| B return | +62.069% | +61.702% | −0.367 pt |
| C final equity | 82,015.74 | 82,015.74 | none |
| Fills A / B / C | 100 / 926 / 846 | 100 / 926 / 846 | none |
| Guardian refusals A / B / C | 4 / 5 / 5 | 4 / 4 / 5 | B −1 |
| B − A mean daily difference | +0.0001986 | +0.0001972 | −0.7% |
| B − A Holm p | 0.054 | 0.054 | none |
| C − B Holm p | 0.462 | 0.475 | +0.013 |
| Verdict | `NOT_PROVEN` | `NOT_PROVEN` | none |

## What this does and does not show

- The verdict, the B − A Holm p and every registered conclusion are unchanged. The registered hash is not, because one satellite order was approved on one day and every later figure for arm B moved slightly.
- It does **not** show that the rule is better or worse for returns. One sale on one day, reducing a loss-making position, is a sample of one; the −0.37 point is an artefact of that day, not an estimate.
- It does **not** cap the loss of a held position, and nothing sells by itself.
- On the live record the limit has fired 6 times, all on 2026-09-28 (4 HOLD, 2 BUY, 0 SELL), so the rule has not yet changed a live order.

## Reproduce

```
PYTHONPATH=src python tools/run_portfolio_experiment.py --out <dir> \
  --spec configs/experiment_portfolio_v4.spec --satellites XLB,XLE,XLF,QQQ,NVDA,MSFT,AAPL \
  --universe-label production-permitted \
  --cache-dir runtime/autopoiesis/replay \
  --cache-name '{symbol}_1Day_adjall_2020-07-27_2026-11-06.json' --strict
sha256sum <dir>/result.json        # ad9f9e90… with the rule; 376bd9cc… with the old rule
```

The orders approved at breach, from a wrapper that does not change the result (same hash):

```python
import runpy, sys
from autopoiesis.guardian import Guardian

events = []
_orig = Guardian.review

def review(self, decision, snapshot, *a, **kw):
    result = _orig(self, decision, snapshot, *a, **kw)
    if result.approved and decision.action != "HOLD" and snapshot.account.daily_loss >= self.max_daily_loss:
        events.append((str(snapshot.timestamp)[:10], decision.symbol, decision.action, decision.quantity,
                       decision.strategy_id, round(snapshot.account.daily_loss, 2)))
    return result

Guardian.review = review
sys.argv = ["run_portfolio_experiment.py", *sys.argv[1:]]
try:
    runpy.run_path("tools/run_portfolio_experiment.py", run_name="__main__")
except SystemExit:
    pass
print(len(events), events)
```
