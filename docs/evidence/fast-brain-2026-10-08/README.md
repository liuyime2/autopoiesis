# Thinking on or off, fast brains, and a model zoo on one benchmark (2026-10-08)

Parts 1 and 2 are two questions asked before the open, answered on our own broker record only (the paper account's
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


---

# Part 3. The model zoo: does any local brain, or any combination, beat holding? (later on 2026-10-08)

Asked: download what Ollama can, test different brains (Qwen, DeepSeek, Gemma, Llama, Phi,
Granite, sizes from 0.6B to 70B, and the Jev-style decision checkpoints) on the same benchmark,
test combinations, and give one complete comparison. Everything ran on two idle GPUs through
separate Ollama instances; the trading Ollama on GPU 0 was not touched, and the live system
traded normally throughout (100+ cycles, 0 errors).

## Setup

- **Benchmark.** The 289 real SPY cycles above, market-only prompt (price, time, the market
  block), the next hour's realised return as the label. 282 of them are answered by every model
  in the table; the comparison uses those.
- **Method.** One forward pass per example; the probability of the letters A (up), B (flat),
  C (down), renormalised over the three. A model that does not answer in letters is reported,
  not scored.
- **Uncertainty.** Intervals are a day-block bootstrap (whole trading days resampled, because
  neighbouring cycles overlap): a 90% interval on the Spearman correlation.
- **Positive control.** A "brain" that sees the future scores accuracy 1.00, correlation +0.94
  and +91% captured, so the scorer does detect skill when there is some.

## Results

| model | params | GB | s/decision | acc | Brier | BUY/HOLD/SELL | 1h captured | rho 1h [90% CI] | rho 4h |
|---|---:|---:|---:|---:|---:|---|---:|---|---:|
| qwen3.8:27b | 27.3B | 16.2 | 1.78 | 0.426 | 0.818 | 26/159/97 | -5.60% | -0.020 [-0.16, +0.09] | -0.157 |
| qwen3:0.6b | 751.63M | 0.5 | 0.14 | 0.301 | 1.351 | 282/0/0 | -8.12% | -0.149 [-0.25, +0.04] | -0.076 |
| Jev-Style-Qwen3.5-2B-Decision (community GGUF) | 1.94B | 1.2 | 0.24 | 0.379 | 0.679 | 101/91/90 | -3.46% | +0.049 [-0.09, +0.18] | -0.134 |
| qwen3:4b-instruct | 4.0B | 2.3 | 0.28 | 0.401 | 1.158 | 43/117/122 | -7.72% | +0.010 [-0.14, +0.15] | -0.114 |
| gemma3:4b | 4.3B | 3.1 | 0.35 | 0.344 | 1.294 | 137/6/139 | -4.75% | +0.031 [-0.09, +0.18] | -0.099 |
| qwen2.5:7b | 7.6B | 4.4 | 0.40 | 0.418 | 1.011 | 40/150/92 | -5.62% | +0.020 [-0.12, +0.18] | -0.051 |
| llama3.1:8b | 8.0B | 4.6 | 0.30 | 0.443 | 0.883 | 27/209/46 | -5.21% | +0.020 [-0.10, +0.15] | -0.090 |
| deepseek-r1:8b | 8.2B | 4.9 | 0.21 | 0.397 | 0.661 | 109/140/33 | -14.41% | -0.011 [-0.16, +0.11] | -0.093 |
| granite3.1-dense:8b | 8.2B | 4.6 | 0.36 | 0.433 | 0.782 | 89/107/86 | -0.81% | +0.056 [-0.08, +0.19] | -0.108 |
| qwen3:8b | 8.2B | 4.9 | 0.38 | 0.369 | 1.194 | 115/19/148 | -5.21% | +0.006 [-0.15, +0.14] | -0.152 |
| gemma2:9b | 9.2B | 5.1 | 0.78 | 0.390 | 1.124 | 78/70/134 | -5.89% | +0.033 [-0.10, +0.17] | -0.110 |
| gemma3:12b | 12.2B | 7.6 | 0.96 | 0.383 | 1.189 | 90/71/121 | -5.86% | +0.029 [-0.10, +0.15] | -0.102 |
| phi4 | 14.7B | 8.4 | 0.48 | 0.312 | 1.084 | 24/19/239 | -4.18% | +0.006 [-0.13, +0.13] | -0.179 |
| qwen3:14b | 14.8B | 8.6 | 0.50 | 0.415 | 1.145 | 38/150/94 | -7.09% | +0.025 [-0.11, +0.15] | -0.087 |
| qwen3:30b-a3b-instruct (MoE) | 30.5B | 17.3 | 0.59 | 0.376 | 1.126 | 61/60/161 | -4.11% | +0.003 [-0.14, +0.18] | -0.092 |
| deepseek-r1:32b | 32.8B | 18.5 | 0.97 | 0.433 | 0.892 | 29/187/66 | -2.52% | +0.021 [-0.11, +0.15] | -0.152 |
| qwen3.5:35b | 36.0B | 22.2 | 1.36 | 0.436 | 0.835 | 42/175/65 | -5.72% | -0.004 [-0.15, +0.15] | -0.177 |
| llama3.3:70b | 70.6B | 39.6 | 1.77 | 0.426 | 1.128 | 44/144/94 | -6.76% | +0.013 [-0.13, +0.16] | -0.141 |

Column notes: `acc` is three-class accuracy (always HOLD scores 0.376, following the past hour
0.397); `Brier` is lower-is-better and a uniform guess scores 0.667; `1h captured` is the sum of
the next hour's return over the trades a model's top choice implies (always HOLD = 0.00%,
following the past hour = -1.88%); `rho` is the Spearman correlation between P(up) - P(down)
and the realised return.

**Not scorable** (they do not answer in letters, so no probability can be read): qwen3:1.7b,
deepseek-r1:1.5b, deepseek-r1:14b, deepseek-r1:70b, qwen3:30b (the thinking tag), qwen:7b.
mistral:7b (86 of 289) and llama3.2:3b (222 of 289) answered too rarely to compare fairly.
`qwen3:4b` always thinks; its `4b-instruct` tag is in the table.

## What the comparison shows

1. **Nothing beats holding.** Every model's 1h correlation interval contains zero (about +-0.15
   wide), and every one lost money when acted on (-0.8% to -14.4%) against 0.00% for holding.
   The best correlations (granite3.1 +0.056, Jev-Style 2B +0.049) are well inside the noise.
2. **Size does not help.** From 0.6B to 70B the correlation does not move (0.6B's -0.149 is a
   model that always answers "up"; 4B +0.010, 8B +0.006, 14B +0.025, 27B -0.020, 32B +0.021,
   70B +0.013). The families do not separate either. DeepSeek-R1 is the least momentum-like of
   the usable models (its distilled 8B correlates 0.60 with the past hour, the rest 0.75-0.92)
   and did worst (-14.4%): a different signal is not a better one.
3. **They are one signal.** The models' P(up) - P(down) correlates 0.85-0.97 with each other and
   0.75-0.92 with plain past-hour return. Seventeen brains are, in effect, one momentum reader,
   so they cannot correct each other.
4. **Combinations add nothing.** Averaging all models: correlation +0.029 [-0.11, +0.16], -7.04%
   captured. Top three by correlation: +0.039, -4.39%. Big models only: +0.013, -8.29%.
   "At least 3 / 5 / 7 models agree on a direction": right 0.356 / 0.365 / 0.371 of the time they
   act (chance is about 0.33), -3.65% / -3.49% / -5.93%. Majority and past-hour momentum
   agreeing: 0.347, -5.01%. The single most confident model: 0.373, -8.63%. Agreement among
   models that share one input is not evidence.
5. **Calibration is the only differentiator, and it is not skill.** The best Brier scores belong
   to deepseek-r1:8b (0.661) and Jev-Style 2B (0.679): as good as guessing uniformly (0.667),
   because they are *less* confident, not because they know more. The rest are over-confident
   (Brier 0.78-1.35).
6. **The 4-hour column is negative for almost every model** (-0.05 to -0.18): the one input they
   all share, recent momentum, points the wrong way over four hours in this sample (next section).
7. **Speed.** 0.14s (0.6B) to 1.8s (70B, split across two GPUs) per letter-logit decision, against
   30-50s for a thinking decision. Speed was never the obstacle to a better brain; skill was.

## The decision-model checkpoints

| Checkpoint | Result |
|---|---|
| ggml-org/Kev-4B-GGUF | Downloaded through Ollama; **Ollama cannot load it** ("error loading model"): the pointer head needs llama.cpp's `/v1/systemone`, which means building llama.cpp (declined). |
| fabricant451/strands-decider-2B-hobson-v19-GGUF (a third-party conversion of Strands Decider) | Downloaded; **Ollama cannot load it**, same reason. |
| TokenRhythm/NeoHorse-Jev-4B-GGUF | **Ollama refuses the repository** ("not GGUF or not compatible with llama.cpp"); its own runtime is custom code (declined). |
| chaoliangUNSW/Jev-Style-Qwen3.5-2B-Decision-GGUF | Loads as an ordinary language model and is scored above (1.2 GB, 0.24s): correlation +0.049, Brier 0.679. Calibrated, no skill. |

So the actual Kev, Strands and NeoHorse models remain untested. They are classifiers fine-tuned on
public text-classification data on a Qwen3.5 base, the same family as the checkpoint that was
tested, and the finding above (the input carries one momentum signal, and every reader of it
agrees) does not depend on the reader; but that is an argument, not a measurement.

## A finding about the data, not the models

Because the models all read momentum, the benchmark also measures momentum. In the market block,
`trend_pct` against the next four hours' return is **-0.33** (n = 214, 15 days): the stronger
the recent trend, the more it reverses. The first half of the days gives -0.38, the second half
-0.31, and the day-block bootstrap interval is -0.61 to -0.07, so it is not one day's accident.
The mean next-4h return after `TRENDING_DOWN` is +0.60% (n = 40) and after `TRENDING_UP` +0.18%
(n = 17). This is in-sample, on 15 overlapping days, before costs: a hypothesis to put through
the walk-forward harness in `src/autopoiesis/research/`, not a rule to trade.

## Decision for the system

No change. Thinking stays on; no fast brain and no ensemble is added; the 240s timeout is the one
change shipped. The next experiment worth running is the walk-forward test of the reversal
above and, if the Kev-style checkpoints are wanted, a llama.cpp build in an isolated directory
that scores them beside the live decisions in shadow, with no authority.

## Reproduce

`runq.sh PORT TAG model...` runs `fastbrain2.py` over each model on the same dataset
(`build_dataset.py` makes it); `compare.py result.json...` prints the table, the ensembles, the
combination rules and the pairwise correlations; `report.py` joins them with sizes and latency.
