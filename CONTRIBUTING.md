# Contributing

Thanks for looking. This project is small, opinionated about evidence, and paper-trading
only. The rules below are the ones that keep its numbers honest.

## Get it running

```bash
python3 -m venv .venv && . .venv/bin/activate
make install
make check            # lint + mypy + tests, no broker, ~1-2 min
make verify           # the full gate, runs offline without credentials
```

No credentials are needed for any of that. `make smoke` and `make doctor` need an Alpaca
**paper** account; see the README.

## The loop

```
edit  ->  make check  ->  make verify  ->  commit
```

`make verify CLASS=<name>` re-runs one check class; `make classes` lists them. Every new
`tests/min_agent/test_*.py` must be added to `TEST_CLASS_MAP` in `tools/verify.py`, or the
gate fails with `test-coverage-map`.

## What a change must carry

- **One coherent change per commit.** Not one file per commit, not a sweep of unrelated
  fixes.
- **A commit message that says why.** The subject names the change and its effect; the body
  gives the cause, the reasoning, and the measured numbers. The diff already says what.
- **A baseline and a way to tell whether it worked**, stated before the code, for anything
  non-trivial. "It looked wrong" is not a baseline.
- **Real results only.** Never present a number you did not get from running something.
  Mock data in tests and synthetic fixtures are fine when labelled as such, and never as
  evidence about the real system. If a number you published turns out wrong, correct it in
  place and say so - do not delete it.

## What will not be merged

These are hard limits, not preferences (`AGENTS.md` §17):

- anything that weakens a Guardian risk limit or adds a path around the Guardian;
- anything that bypasses the market-open check or the paper-only refusal;
- LLM-generated shell or code execution;
- mock or fabricated data on a paper or live path;
- credentials, `runtime/` state, or logs that contain either.

## Reporting a bug

Use the issue template. Include the exact `make` command and its exit code. If you paste
`make doctor` output, check it for keys first - it should never print them, and if it does,
that is a security issue: see [`SECURITY.md`](SECURITY.md) instead.

"My strategy is not making money" is not a bug. "The benchmark reports a number its own
inputs do not support" is.
