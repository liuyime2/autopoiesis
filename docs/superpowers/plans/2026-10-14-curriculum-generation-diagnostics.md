# Curriculum generation fails 6 times out of 6, and the obvious causes are disproven

Written 2026-10-14 07:45 EDT. Pre-edit investigation note. No code change has been made, and
none should be made before the 2026-10-14 session is observed.

## The measurement

`CURRICULUM_FAILED` with `error_type=ValueError, message="constrained response contained no
JSON object"` at

    2026-10-13 10:10  16:47  21:37  22:05
    2026-10-14 00:06  04:40  05:08  05:36

Six out of six attempts since the 2026-10-13 deployment, across three different source
fingerprints. No curriculum proposal has succeeded in that window, and none succeeded before
it either - the last accepted proposals were at 11:25 and 13:39 on 2026-10-13, and the first
failure followed at 16:47.

This blocks self-healing, not trading. It does not gate a cycle, does not touch Guardian, and
the daemon kept trading through all six failures with `error_count=1` for the whole of
2026-10-13.

## What the raise actually means

`_json_load` in `src/autopoiesis/curriculum.py:731` searches the raw response for `{` and `}`:

    start = raw.find("{"); end = raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("constrained response contained no JSON object")

`_ollama_curriculum_transport` in `src/autopoiesis/cli.py:521` returns
`str(response.get("response", ""))`. An Ollama reply with no `response` key - an error body, or
a body shaped differently than the success shape - yields an empty string, and an empty string
takes this branch. So the message means **the transport returned nothing usable**, not
specifically "the model wrote prose".

## Causes tested and ruled out

**Schema-constrained generation is not broken.** Driving `kind_selection_schema()`,
`strategy_spec_schema("TREND_FOLLOW")` and `strategy_spec_schema("FIXED_SIZE")` at the live
model returns well-formed JSON every time, `done_reason=stop`, 293-2120 eval tokens.

**The real entry point works.** `generate_curriculum_task_json` run with a live-shaped context
completes both calls and returns a valid task.

**Prompt overflow is not the cause.** The compacted library view is capped at
`max_strategies_in_prompt = 40` and measures roughly 3,195 tokens against
`CURRICULUM_NUM_CTX = 8192`. A deliberately oversized prompt of 54,960 characters - about
thirteen times the real one - still returns valid JSON, both at `num_ctx=8192` and at 32768.
The hypothesis that Ollama silently returns nothing on a truncated context is wrong.

**Not a model identity problem.** Every probe used the configured `qwen3.8:27b` on the live
`127.0.0.1:11434`.

## What remains, and why it is not yet diagnosable

The remaining candidate is transport-level: `engine.transport` returning a dict without a
`response` key. The six failures carry only `error_type` in the journal; the raw body, the HTTP
status and the URL are all discarded at the `except` in the curriculum stage, so nothing on
record distinguishes an Ollama error body from an empty completion.

That is the actual defect, and it is a defect in the *diagnostics*: an unactionable error
string for a failure that recurs every thirty minutes. The fix is small - journal the raw
response and the status when `_json_load` raises - and it is deliberately not applied here,
because a source change means a redeploy, and redeploying an accounting-and-learning change
twenty minutes before the 08:45 preflight is the wrong trade for a learning-throughput issue
that has never affected a trading decision.

## Success criterion for the fix, when it is made

- `CURRICULUM_FAILED` carries the raw response body (bounded), the status and the URL.
- A curriculum attempt either succeeds or fails with enough detail to name the cause.
- If the cause is an Ollama error body, it is handled rather than swallowed.
- Focused tests for each, then the frozen full check.

## Also open from the same evening

`fixed-size-bil-sell-001` is RETIRED and BIL still has no servable strategy. The re-admission
path exists and is deployed, but it is refused by the capacity check: 13 candidates are in
probation and short of budget, which is `max_probation_queue`. Today's preflight reports
`BIL=NONE`, `XLB=NONE` against SPY/TLT/XLE/XLF selectable. Neither queue nor eligibility may be
relaxed to fix this; the probation queue drains when candidates close lots, which is what the
2026-10-14 session is for.