# Global Working Principles

These apply to every task in this repository. They are ordered by when they bind:
before a change, while making it, and after.

## Before

### 1. Evaluation First

Any non-trivial modification must first establish, in writing: the problem, the
baseline, the success criterion, and how it will be verified. Only then design, and
only then edit code. Changing code without a target is prohibited — "it looked wrong"
is not a baseline.

State the baseline as a measurement, not an impression. "95 mypy findings" is a
baseline; "type checking is bad" is not.

### 2. First Principles

Do not assume the existing architecture, code, or historical design is correct
merely because it exists. First answer: what problem is this actually solving, and
what is the minimum necessary system? Only then decide what to keep, delete, merge,
or rewrite.

Existing code is evidence, not authority. It was written by someone solving a
problem that may since have changed, under constraints that may no longer hold.

### 3. Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution

- **Think Before Coding** — understand the whole system before editing part of it.
- **Simplicity First** — prefer the smallest sufficient design.
- **Surgical Changes** — no unrelated rewrites or churn in the same change.
- **Goal-Driven Execution** — verify against the user-visible goal, not against
  intent.

### 4. Documentation First

Any code modification must enter Plan Mode first. Do not make direct code changes
without a written plan.

## While

### 5. Real Data and Real Results

Never fabricate data, results, or experiments. Any experiment, benchmark, backtest,
or evaluation result must come from real execution and must be reproducible.

Mock data in tests and synthetic data in simulation are allowed **only** when
clearly labelled as such. They must never stand in as evidence for a claim about
real behaviour.

The distinction that matters: a test proves the code does what the test says. Only
real execution proves what the system does.

### 6. Small Changes, Continuous Validation

Solve one clearly-identified problem at a time. Immediately after each change, run
the relevant tests, benchmarks, and regression checks. When something breaks, locate
the root cause — never accumulate a pile of unverified changes and debug them
together.

### 7. Delete Before Add

Facing a complex system, the default priority is:

```
delete → merge → simplify → reuse → rewrite → add
```

Prefer removing code, collapsing duplicate paths, and dropping wrappers, legacy
compatibility layers, and unnecessary abstractions over introducing a new layer. New
code is the most expensive option and the last one to reach for.

### 8. Infra First, Loop Fast

First make the minimum loop — `install → data → run → evaluate → output` — run
completely end to end. Only then optimise the model, the algorithms, or the
architecture.

Infrastructure must support fast repeated experiments and fast failure isolation.
A change to a model is unmeasurable if each run takes an hour or a failure takes an
afternoon to localise.

### 9. Independent, Reproducible Environment

The environment, dependencies, configuration, and entry points must be explicitly
defined and isolated inside this workspace. Do not depend on implicit state of the
machine.

Concretely, in this repository:

- dependencies are declared in `pyproject.toml` and nothing else;
- the task runner is the `Makefile`; scripts are not run by hand;
- credentials live only in `${XDG_CONFIG_HOME:-$HOME/.config}/min-agent/env`,
  never in the repository;
- `docs/evidence/run-fresh-clone.sh` clones the committed tree, installs it, and
  runs it with credentials removed — that is the proof the machine is not load-bearing.

The test: a fresh clone on a different machine must run per the documentation.

## After

### 10. Evidence-Driven Iteration

Every round forms a closed loop:

```
modify → run → evaluate → locate the problem → next modification
```

The next change is decided by the result of this one, not by intuition or by a
queue of features to add.

### 11. Done Means Verified

"Code is written" is not completion. A task is done only when all of these hold:

- the target metric is met;
- the core path has actually run;
- there is no evident regression in existing behaviour;
- the result is reproducible;
- the documentation is updated to match.

### 12. Git Version Management

One coherent change per commit — not one file per commit, and not a sweep of
unrelated fixes. Before committing, inspect `git status`, `git diff`, and
`git log`.

A commit message states **why**, not **what**. The subject is one imperative line
naming the change and its effect; the body gives the root cause, the reasoning, and
the measured numbers. A diff already says what changed.

```
Merge the three copies of `_field` into `coerce.field_of`

`experiment_registry`, `lineage` and `offline_validation` each carried their own
"read a named field from a dict or an object". Two were byte-identical...
```

Never commit credentials, generated evidence that contradicts a re-run, or a claim
not backed by a command you ran. Do not rewrite published history, force-push, or
amend a commit that has already been pushed. If a commit fails or a hook rejects
it, fix the problem and make a new commit.

## Standing Constraints

### 13. Optimization Mechanism First

Problems in Skill scripts must be discovered and fixed through the optimization
mechanism. Do not manually edit Skills to bypass the mechanism. Record reusable
failure lessons where they belong.

### 14. Skill Usage Standard

Use the Superpowers skill system by default. `planning-with-files` is the default
gate before implementation work.

### 15. Core Mission: Autonomous Self-Evolving Trading Agent

The project goal is to build a self-evolving autonomous trading agent whose
ultimate success metric is real profitability over time.

The agent should eventually be able to:

- collect real market and account data;
- analyze opportunities with large language models;
- make trading decisions autonomously;
- execute approved trades;
- reflect on results;
- improve its own strategy, memory, prompts, and operating policies.

The minimum viable system must start with paper trading and a complete feedback
loop. Live trading is forbidden until paper-trading behavior is audited and hard
risk controls are verified.

### 16. Non-Negotiable Safety Rules

Autonomy is bounded by rules that are never traded away to satisfy a narrower
success criterion:

- no mock or fabricated data in paper/live paths;
- no bypass of Guardian risk checks;
- no bypass of market-open checks;
- no LLM-generated shell execution;
- no automatic weakening of hard risk limits.
