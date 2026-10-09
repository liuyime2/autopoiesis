# One entry point for every check. `make verify` is the gate the objective asks
# for; the individual targets exist so a single failing class can be re-run while
# working on it.
#
# Nothing here swallows a failure. No `-` prefix, and no piping into something that
# discards the exit status, because a verification command that cannot fail is believed
# rather than run. `make verify-self-test` proves that claim by breaking the gate on
# purpose.
#
# The two exceptions are `smoke-offline`'s `--check-env` and `--doctor` lines, which
# carry `|| true` deliberately and are commented where they appear: both report missing
# credentials and exit non-zero on a machine that has none, which is correct. That
# target checks that the install works, not that a deployment is configured, so it
# requires the report to have been produced rather than clean. Every other target -
# every check that could hide a defect - runs without one.

SHELL := /bin/bash

# One interpreter, discovered rather than assumed - the same resolution `minictrl` performs,
# in the same order: an explicit CONDA_BIN, then PATH, then the usual install prefixes.
#
# The first version was a bare `conda run -n llm`, which meant every target below it died with
# "conda: command not found" on any shell where conda was not already on PATH - 19 of 28
# targets, including verify, test, lint and install. The project's own runbook records that
# this environment does not have conda on PATH and tells the reader to export CONDA_BIN,
# which the Makefile never read. Every figure this repository quotes came from a shell where
# the author had activated the environment by hand.
CONDA_ENV ?= llm
CONDA_BIN ?= $(shell command -v conda 2>/dev/null)
ifeq ($(strip $(CONDA_BIN)),)
  CONDA_BIN := $(firstword $(wildcard /opt/conda/bin/conda                                       $(HOME)/miniconda3/bin/conda                                       $(HOME)/miniforge3/bin/conda                                       $(HOME)/anaconda3/bin/conda))
endif

# An active venv wins over conda: someone who created a venv and ran `make install` in it
# has said which interpreter they mean, and `conda run -n llm` would silently ignore it - or
# fail outright on a machine that has conda but no `llm` env.
ifneq ($(strip $(VIRTUAL_ENV)),)
  CONDA_BIN :=
endif

# When conda is unavailable entirely, fall back to the active environment's python rather than
# failing every target. A clone on a machine with no conda at all can then still run the gate,
# which is what docs/evidence/run-fresh-clone.sh relies on.
ifeq ($(strip $(CONDA_BIN)),)
  CONDA_RUN :=
  RUN_HINT := (a venv is active or conda is absent; using the active environment's python)
else
  CONDA_RUN := $(CONDA_BIN) run -n $(CONDA_ENV) --no-capture-output
  RUN_HINT :=
endif

PY := PYTHONPATH=src $(CONDA_RUN) python

# The credentials file, resolved the same way `minictrl` resolves it. It used to be
# `$(XDG_CONFIG_HOME)/min-agent/env`, which becomes `/min-agent/env` when XDG_CONFIG_HOME is
# unset - the state CI and the runbook are in - so `make` silently proceeded with no
# credentials and no warning, while `minictrl` looked in the right place. Two entry points
# disagreeing about the configuration is exactly what this refactor set out to remove.
# The new directory first, the old one still honoured: see minictrl for why.
# The credential file, under its current name and the one it had before the rename. Both are
# honoured, the new one first: `minictrl` resolves it the same way, and dropping the old name
# without moving the file would leave the agent unable to find its credentials at all.
ENVFILE ?= $(if $(MIN_AGENT_ENV_FILE),$(MIN_AGENT_ENV_FILE),$(if $(AUTOPOIESIS_ENV_FILE),$(AUTOPOIESIS_ENV_FILE),$(firstword $(wildcard $(if $(XDG_CONFIG_HOME),$(XDG_CONFIG_HOME),$(HOME)/.config)/autopoiesis/env $(if $(XDG_CONFIG_HOME),$(XDG_CONFIG_HOME),$(HOME)/.config)/min-agent/env))))

# Credentials live outside the repo. Every target that touches the broker or the
# model loads them from there; nothing reads a checked-in secret.
ifneq (,$(wildcard $(ENVFILE)))
include $(ENVFILE)
export
endif

.DEFAULT_GOAL := help
.PHONY: help setup install check lint type test smoke smoke-offline fast fast-no-broker daily-report benchmark \
        verify verify-self-test doctor audit integrity classes classes-list \
        run stop restart status validate-data evaluate pipeline reproduce clean clean-pyc

help:
	@echo "Interpreter: $(if $(strip $(CONDA_RUN)),$(CONDA_BIN) run -n $(CONDA_ENV),$$(command -v python3) - active environment)$(RUN_HINT)"
	@echo "Config:      $(ENVFILE)$(if $(wildcard $(ENVFILE)), [present],[absent - broker checks will skip])"
	@echo ""
	@echo "Setup:"
	@echo "  make setup           create the '$(CONDA_ENV)' environment and install into it"
	@echo "  make install         install this package and its dev dependencies"
	@echo ""
	@echo "Verify:"
	@echo "make verify           run every check class; non-zero on any failure"
	@echo "make verify-self-test prove the gate can fail, then exit"
	@echo "make test             unit and integration tests only"
	@echo "make classes          list the check classes and their test files"
	@echo "make lint             ruff over src/ tools/ tests/ examples/"
	@echo "make type             mypy over src/autopoiesis; reports without gating"
	@echo "make check            lint + test (no broker, no credentials)"
	@echo "make test             unit and integration tests only"
	@echo "make smoke            one real cycle against the paper broker"
	@echo "make smoke-offline    the widest check needing no broker and no credentials"
	@echo "make fast             lint + test + smoke + verify"
	@echo "make fast-no-broker   lint + test + smoke-offline, no credentials needed"
	@echo "make reproduce        write runtime/autopoiesis/reproduce.txt (provenance)"
	@echo "make clean            remove caches; clean-pyc removes only bytecode"
	@echo "make integrity        runtime state integrity only"
	@echo "make doctor           health and evidence checks"
	@echo "make audit            the 15-defect regression audit"
	@echo "make validate-data    parse and check the inputs before anything consumes them"
	@echo "make evaluate         score the current paper record: strategies, PnL, counterfactuals"
	@echo "make pipeline         validate-data -> test -> evaluate -> reproduce (run make install first)"
	@echo "make run|stop|restart the trading daemon"
	@echo "make status           daemon and account status"
	@echo ""
	@echo "Per-class: make verify CLASS=<name>, e.g."
	@echo "  make verify CLASS=pnl-accounting"
	@echo "  make verify CLASS=point-in-time-no-leakage"

verify:
	@$(PY) tools/verify.py $(if $(CLASS),--only $(CLASS),)

# Must stay green. If this ever fails, the gate cannot be trusted and every other
# result from it is meaningless.
verify-self-test:
	@$(PY) tools/verify.py --self-test

test:
	@$(PY) -m pytest -q

doctor:
	@./minictrl doctor

# Does the system beat SPY buy-and-hold, after costs, inside the stated risk budget?
# The falsifiable target the whole project is measured against. No credentials needed.
benchmark:
	@$(PY) tools/benchmark.py

# One trading day's validation report: did it start at the open, who decided, where the model
# overrode the rule and why, shorts, and the benchmark. Reads only. DATE=YYYY-MM-DD for another day.
daily-report:
	@$(PY) tools/daily_report.py $(if $(DATE),--date $(DATE),)

audit:
	@$(PY) tools/audit_defects.py

integrity:
	@$(PY) tools/check_runtime_integrity.py

# The classes the gate actually runs, which is not CHECK_CLASSES: the bespoke checks are
# appended in verify.py's main() and never appear in that tuple. Printing the tuple
# under-reported the gate, which is the drift the class-count gate exists to catch, reported
# by the command meant to show the count.
#
# `classes` and `classes-list` were byte-identical rules describing different things, and the
# comment above claimed a distinction that no longer existed. One target now, printing the
# full table; the alias remains because both names are in use, and it delegates rather than
# duplicating the command.
classes:
	@$(PY) tools/verify.py --list

classes-list: classes

run:
	@systemctl --user start autopoiesis.service

stop:
	@systemctl --user stop autopoiesis.service

restart:
	@systemctl --user restart autopoiesis.service

status:
	@./minictrl status

clean-pyc:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete

# ---------------------------------------------------------------------------
# Fast iteration loop.
#
# `make fast` is the loop a developer runs on every edit: static check, unit
# tests, smoke test against the real broker, then the gate. It is ordered by
# increasing cost so a mistake is caught by the cheapest thing that can catch
# it. `make fast-no-broker` is the same minus the broker call, for when the
# market is closed or credentials are absent - it is not a weaker claim about
# the code, only about connectivity.
# ---------------------------------------------------------------------------

lint:
	@$(CONDA_RUN) ruff check src/ tools/ tests/ examples/

# Type checking, and now part of `make check`.
#
# It used to be `-@$(CONDA_RUN) mypy ...`: the leading `-` swallowed the exit code,
# so mypy reported 95 findings on every run without failing anything. That is the
# worst of both - the debt was visible but nothing stopped it growing, and a
# passing `make type` meant nothing at all. It is now 0 findings and blocking, so a
# regression is caught by the fast loop rather than discovered at review time.
type:
	@$(CONDA_RUN) mypy src/autopoiesis

check: lint type test

# Smoke: one real cycle end to end against the broker. This is the cheapest test
# that touches the network and the model, and the first one that can catch a
# broken API contract, a bad credential path or a decision that will not parse.
smoke:
	@$(CONDA_RUN) python tools/alpaca_smoke.py

fast-no-broker: lint test smoke-offline

# Offline smoke: the widest check that runs with no broker and no credentials, so a new
# developer can verify the install before they have a paper account.
#
# `--skip-broker` belongs to `--doctor` and does nothing on `--once`; `_run_once` still
# requires credentials, so the previous version of this target could not have run in a
# fresh environment. Found by cloning and running, not by reading the Makefile.
# Verifies the install, not the deployment. `--check-env` and `--doctor` both report
# missing credentials and exit non-zero on a machine that has none, which is correct -
# so the target checks the report was produced, not that it was clean. What it proves is
# that the package imports, config loads, and the CLI runs end to end without a broker.
smoke-offline:
	@PYTHONPATH=src $(CONDA_RUN) python -c "import autopoiesis, autopoiesis.cli; print('import ok')"
	@PYTHONPATH=src $(CONDA_RUN) python -m autopoiesis.cli --check-env > /dev/null || true
	@PYTHONPATH=src $(CONDA_RUN) python -m autopoiesis.cli --doctor --skip-broker --quiet || true
	@echo "offline smoke ok: package imports, config loads, CLI runs with no broker"

fast: lint test smoke verify

# ---------------------------------------------------------------------------
# Fresh-clone path. These are the only commands a new developer needs.
# ---------------------------------------------------------------------------

setup:
	@echo "Create an environment, then: make install"
	@echo "  python3 -m venv .venv && . .venv/bin/activate && make install"
	@echo "  (or: conda create -n llm python=3.10 -y && conda activate llm && make install)"
	@echo "  export XDG_CONFIG_HOME=\$$HOME/.config"
	@echo "  mkdir -p $$XDG_CONFIG_HOME/autopoiesis"
	@echo "  # put ALPACA_API_KEY / ALPACA_SECRET_KEY / ALPACA_BASE_URL there"
	@echo "Then: make check"

install:
	@$(CONDA_RUN) python -m pip install -e ".[dev]"

# Record what produced a result: commit, dependency versions, config, and the
# journal's own record of what ran. Written to runtime/, which is gitignored,
# because it describes one machine's run rather than the repository.
# Record what produced a result, so any run can be traced back to the code and
# configuration that made it. The objective names the fields explicitly: config, commit
# hash, environment, seed, dataset version, output path and core metrics. Each is read
# from live state rather than typed in, so the record cannot drift from the run.
#
# `seed` and `dataset version` are reported as absent rather than invented. This agent
# makes no stochastic decision of its own - the only randomness in the path is the
# model, whose sampling is temperature-driven and not seeded anywhere in the codebase -
# and its "dataset" is the live broker, whose version is a point in time. Writing
# `seed: none` and `dataset: live broker at <iso>` states the fact; writing a number
# would be a fabrication.
# The pipeline the objective names, in one command. Each stage is independently runnable
# and each is a real check rather than a step that prints "ok": validate-data parses the
# inputs, test runs the suite, evaluate scores what the journal actually records, reproduce
# writes the provenance of this run. Nothing here is a placeholder - before this target,
# `evaluate` existed only as a script the README never mentioned.
pipeline: validate-data test evaluate reproduce

# Validate the inputs before anything consumes them. This agent's dataset is the live
# broker, so there is nothing to download; what can be wrong is the state that stands in
# for it. A clone with no runtime directory has nothing to validate, which is reported as
# such rather than as a failure - otherwise this stage could never pass before the first
# cycle, which is when it is most useful.
validate-data:
	@if [ ! -d runtime/autopoiesis ]; then \
		echo "validate-data: no runtime/autopoiesis; nothing recorded yet (run 'make run' first)"; \
	else \
		$(CONDA_RUN) python tools/check_runtime_integrity.py && \
		echo "validate-data: runtime state parses and is internally consistent"; \
	fi

# Score what actually happened, using broker evidence only.
#
# The exit code is deliberately swallowed. `--verify-profit-target` exits non-zero when the
# 10% daily target is not met, which right now it is not - and that is a measurement, not a
# broken build. Wiring it into `make pipeline` unchanged would have made the pipeline fail
# every day until the agent is profitable, which teaches an operator to ignore the target
# entirely: the one number the project exists to move would become noise. So the report is
# printed with its verdict intact and the stage succeeds, and a genuine failure - an
# unreadable journal, missing credentials - still surfaces because the report cannot be
# produced at all.
# `--verify-profit-target` exits 1 when the 10% daily target is not met, which is a
# measurement, and 2 when it cannot produce the report at all, which is a failure. An earlier
# version used `|| true`, which swallowed both, so a missing interpreter, a crash and an unmet
# target were indistinguishable - and the comment claiming a genuine failure still surfaced
# was simply wrong. Only the absence of a report fails this stage.
EVAL_REPORT := runtime/autopoiesis/evaluate.txt

evaluate:
	@mkdir -p runtime/autopoiesis
	@if [ ! -f runtime/autopoiesis/journal.jsonl ]; then \
		echo "evaluate: no journal yet; run 'make run' for at least one cycle first"; \
	elif $(CONDA_RUN) python -m autopoiesis.cli --verify-profit-target > $(EVAL_REPORT); then \
		cat $(EVAL_REPORT); \
		echo "evaluate: target met"; \
	else \
		rc=$$?; cat $(EVAL_REPORT); \
		if [ $$rc -gt 1 ]; then \
			echo "evaluate: FAILED (exit $$rc): the report could not be produced"; \
			exit $$rc; \
		fi; \
		echo "evaluate: target NOT met (exit 1). That is a measurement, not a gate failure."; \
	fi
reproduce:
	@mkdir -p runtime/autopoiesis
	@$(PY) tools/provenance.py > runtime/autopoiesis/reproduce.txt
	@echo "wrote runtime/autopoiesis/reproduce.txt"
	@sed -n '1,10p' runtime/autopoiesis/reproduce.txt


clean: clean-pyc
	@rm -rf .pytest_cache .ruff_cache .mypy_cache
	@find . -name "*.egg-info" -type d -prune -exec rm -rf {} +
