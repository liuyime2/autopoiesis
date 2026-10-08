# Does a fast "decision model" brain, or turning the model's thinking off, help? (2026-10-08)

Two questions before the open, answered on our own broker record only (the paper account's
journal, 17 trading days of SPY cycles, no outside data):

1. **Thinking on or off** for the 27B model's trading decision (a decision took 30-50s).
2. **A fast brain** in the style of Jev / SemIf / Kev: a frozen LLM reads the logits of the
   answer letters in one forward pass and returns a probability over BUY / HOLD / SELL.

Everything here is read-only: nothing was submitted, journalled or changed in the running system.

## 1. Thinking on or off (`think_eval.py`, `score_think.py`)

12 real SPY cycles of 2026-10-07, each decided three times on the identical production prompt:
twice with thinking (the model's own run-to-run noise) and once without.

| | thinking, run 1 vs run 2 | thinking vs no thinking |
|---|---|---|
| same action | 11 of 12 | **2 of 12** |

With thinking the model followed the strategy's rule (BUY) in 10 of 11 cases; without it, it
overrode the rule to HOLD in 10 of 11. So turning thinking off is a change of behaviour, not a
free speed-up: 36-46s became 3-6s, and the model became systematically more conservative. Over
that day SPY rose in 8 of the 11 hours with an outcome, so "always BUY" won; that is the market,
not evidence that either setting is smarter. **Decision: keep thinking on.**

## 2. A fast brain (`build_dataset.py`, `fastbrain.py`, `score_fast.py`)

289 real SPY cycles (every third open-market cycle), each with the realised return over the next
hour. Classes are balanced: 88 up, 108 flat, 93 down (a move under 0.10% is flat; the round trip
costs about 0.05%). The model sees only price, time and the market block (regime, trend,
volatility, returns), with the strategy rule, risk limits and lessons removed: with the rule in
the prompt the 8B model's actions were identical to the rule's (268 BUY / 21 HOLD / 0 SELL), i.e.
it copied it, and that measures obedience, not judgement.

| Fast brain | accuracy | Brier (uniform = 0.667) | Spearman(P(buy) - P(sell), 1h return) | 1h return captured by acting |
|---|---:|---:|---:|---:|
| qwen3.8:27b | 0.426 | 0.822 | -0.022 | -5.55% |
| qwen3:8b | 0.367 | 1.199 | -0.002 | -4.43% |
| llama3.1:8b | 0.443 | 0.884 | +0.016 | -5.15% |
| gemma2:9b | 0.388 | 1.132 | +0.027 | -5.87% |
| qwen2.5:7b | 0.419 | 1.014 | +0.011 | -5.57% |
| always HOLD | 0.374 | | | 0.00% |
| follow the past hour's return | 0.398 | | | -1.71% |
| the strategy rule (always BUY, mostly) | 0.325 | | | -8.25% |

**No model shows directional skill**: every correlation is within 0.03 of zero, every Brier is
worse than guessing uniformly (the probabilities are over-confident), and acting on any of them
lost money over the hour. The higher accuracy of some comes from answering "flat" often.

Speed, for what it is worth: a letter-logit decision takes 0.4s with the prompt cached and about
4s with a fresh 1,000-token prompt on the 27B (0.3-1.8s on 7-9B models), against 30-50s with
thinking.

## What this does not show

- **Kev-4B, NeoHorse-Jev-4B, Strands Decider 2B and Open-Jev were not run.** They need external
  code (llama.cpp's `/v1/systemone`, or custom modelling code) and running it was declined. They
  are Qwen3.5-based classifiers fine-tuned on public text-classification data (their cards list
  it), so there is no reason to expect market direction to be a skill they have; but that is an
  argument, not a measurement. A shadow run of one of them beside the live decisions would
  measure it.
- qwen3:4b could not be scored (the older Qwen3 template ignores `think:false`); the Qwen3.5-4B
  base that SemIf uses is not in the local model library.
- One instrument, one horizon, 17 days, and neighbouring cycles overlap, so the effective sample
  is far smaller than 289. A correlation of 0.03 is indistinguishable from zero; a correlation
  of 0.15 would not have been distinguishable either.

## Reproduce

`build_dataset.py OUT.json` (needs the journal and `XDG_CONFIG_HOME`), then
`fastbrain.py MODEL OUT.json RESULT.json PORT market-only`, then `score_fast.py RESULT.json...`.
