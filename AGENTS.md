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
- credentials live only in `${XDG_CONFIG_HOME:-$HOME/.config}/autopoiesis/env`,
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

### 11. Keep the Todo Reconciled With Reality

The Todo list is the plan; the Goal is the boundary. Reconcile them against measured
progress at the end of every working unit, not only at the start of a session.

Three inputs decide what the Todo should say:

- **progress** — what has actually been run and verified, not what was attempted;
- **objective** — what must be true at the end, restated in the item's wording;
- **current task** — what this cycle is for.

Do not resolve an uncomfortable item by deleting it. An item that is blocked,
superseded, or newly irrelevant is **modified** to record that, with the reason and
the evidence, or **added** to a follow-up. Deleting without that record destroys the
only trace of why the work stopped, and the next session repeats it.

Marking an item done is not a claim that it happened. The proof is the command that
ran and its output; the Todo only records that someone looked.

### 12. Done Means Verified

"Code is written" is not completion. A task is done only when all of these hold:

- the target metric is met;
- the core path has actually run;
- there is no evident regression in existing behaviour;
- the result is reproducible;
- the documentation is updated to match.

### 13. Git Version Management

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

### 14. Optimization Mechanism First

Problems in Skill scripts must be discovered and fixed through the optimization
mechanism. Do not manually edit Skills to bypass the mechanism. Record reusable
failure lessons where they belong.

### 15. Skill Usage Standard

Use the Superpowers skill system by default. `planning-with-files` is the default
gate before implementation work.

### 16. Core Mission: Autonomous Self-Evolving Trading Agent

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

### 17. Non-Negotiable Safety Rules

Autonomy is bounded by rules that are never traded away to satisfy a narrower
success criterion:

- no mock or fabricated data in paper/live paths;
- no bypass of Guardian risk checks;
- no bypass of market-open checks;
- no LLM-generated shell execution;
- no automatic weakening of hard risk limits.

### 18. GPUs 5 to 9 are not ours

This host has ten GPUs and other work on it. **GPUs 0 to 4 are available to this
project; GPUs 5 to 9 are reserved for work that is not ours, and no process started
from this repository may reach them.**

The rule is enforced rather than trusted. `make verify` runs the `gpu-devices-reserved`
class, which reads every GPU pin this repository can reach - the Ollama unit template,
the installed unit, the `AUTOPOIESIS_GPU_DEVICES` configuration and the unit's
`CUDA_VISIBLE_DEVICES` - and fails if any of them names a reserved device. A rule that
lives only in prose is a rule that holds until someone is in a hurry.

Two things this is not. It is not a statement about which device is fastest, and it is
not a ban on *reading* a reserved device's state: `nvidia-smi` may be queried for any
GPU, because knowing what the host is doing is not the same as taking it. What is
forbidden is **pinning this project's work to a device that belongs to someone else**.

Measured 2026-10-16, and the reason the rule is written down at all: the agent's Ollama
was pinned to GPU 0 while a second instance - started outside the unit - ran on GPU 2
and port 11500, so two Ollama servers were competing for a device neither was
documented as owning. A pin is only meaningful if something checks it.

### 19. The gate has two tiers, and they are evidence for different things

`make verify` is the full gate: all 57 classes. `make verify-fast` is the deploy-time tier: 51 of
them.

**Why two.** Measured on the deployment host on 2026-10-18: the full gate takes about 23 minutes.
That is longer than a working session, so "the gate must be green before deploying" had stopped
being a rule anyone could keep - and a rule that cannot be kept is not a rule, it is a preference
that gets broken quietly. Quietly is the part that matters: the deployment record then says the gate
passed when the gate was not run.

**What each tier is evidence for, and nothing else.**

- **`make verify` green on a commit** — that commit's *behaviour* is verified. Required once for
  every commit that will be deployed, and the verdict, with the commit it was taken on, goes into
  the deployment record.
- **`make verify-fast` green at the moment of installing** — that the tree being installed is the
  tree that was verified, and that the artefacts a deploy touches agree with each other: the systemd
  units, the environment file, the daemon's source fingerprint, the figures the documents claim, and
  the daemon's own health after the restart. It is **not** evidence about behaviour and must never be
  quoted as if it were.

A deploy therefore needs both - and **not in that order**, which took building the check to
discover. Two classes are deploy-dependent by construction: `daemon-source-matches-worktree` and
`doctor`'s daemon problem are red whenever a new build is about to be installed, because the daemon
is still running the old one. A full gate is therefore red *before every install*, and a rule
demanding it be green first is unsatisfiable - which is the kind of rule that gets ignored and then
misreported as satisfied.

So the order is: install, then `make verify`, then `make record-deployment`. **A deploy is not
complete until the installed build has a record.** That is what the `deploy-gate-record` class
enforces, and its teeth are in what it refuses: a build whose gate is red cannot be recorded,
because `make record-deployment` writes nothing when the gate is red. So a bad install does not
lose its red - it keeps it, visibly, as an unrecorded build, until the code is fixed. What CI adds
is the other half: every push and pull request runs the full gate, so the code being installed has
already been verified before anyone installs it.

**And it is enforced, not asserted.** `make record-deployment` runs the full gate and writes the
verdict it observed to `docs/evidence/deployments.jsonl`, keyed by the source fingerprint; the
`deploy-gate-record` class fails when the installed fingerprint has no passing record from a *full*
run. The tier is written by that tool rather than typed, so a `--fast` record cannot claim it. This
half of the rule was prose until 2026-10-18, and prose is what gets skipped when someone is in a
hurry.

**What the fast tier leaves out, and why that is a split by kind rather than a convenient one.** It
excludes two kinds of check, and the measured costs confirmed the kinds were the right ones:

- the checks that read the **journalled history** — `self-evolution-closes` alone is 638s of the
  run, because it replays the journal, and the journal grows with the deployment's age. That cost is
  a property of this host's history, not of the commit being deployed.
- the checks that run the **bulk of the test suite** — `unit-integration` is 57 files and 582s.

The membership is declared as an exclusion list, `FAST_TIER_EXCLUDED` in `tools/verify.py`, so a
class added later is in the fast tier by default. A list of what to *run* would mean every new check
silently escapes the tier that runs most often — the same defect `check_all_tests_classified` exists
to prevent one level down.

**Three things this is not.** It is not a weaker gate: no ceiling, acceptance rule or check has been
changed, and nothing is waived. It is not a replacement for CI, which still runs the full gate on
every push and pull request across three Python versions — and is fast there for exactly the reason
above, that a fresh clone has no journalled history to replay. And it is not permission to deploy on
the fast tier alone.
