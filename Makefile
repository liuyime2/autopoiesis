# One entry point for every check. `make verify` is the gate the objective asks
# for; the individual targets exist so a single failing class can be re-run while
# working on it.
#
# Nothing here swallows a failure. There is no `|| true`, no `-` prefix and no
# piping into something that discards the exit status, because a verification
# command that cannot fail is believed rather than run. `make verify-self-test`
# proves that claim by breaking the gate on purpose.

SHELL := /bin/bash
CONDA_RUN := conda run -n llm --no-capture-output
PY := PYTHONPATH=src $(CONDA_RUN) python
ENVFILE := $(XDG_CONFIG_HOME)/min-agent/env

# Credentials live outside the repo. Every target that touches the broker or the
# model loads them from there; nothing reads a checked-in secret.
ifneq (,$(wildcard $(ENVFILE)))
include $(ENVFILE)
export
endif

.DEFAULT_GOAL := help
.PHONY: help verify verify-self-test test doctor audit integrity classes \
        classes-list run stop restart status clean-pyc

help:
	@echo "make verify           run every check class; non-zero on any failure"
	@echo "make verify-self-test prove the gate can fail, then exit"
	@echo "make test             unit and integration tests only"
	@echo "make classes          list the check classes and their test files"
	@echo "make classes-list     print the class names only"
	@echo "make integrity        runtime state integrity only"
	@echo "make doctor           health and evidence checks"
	@echo "make audit            the 15-defect regression audit"
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

audit:
	@$(PY) tools/audit_defects.py

integrity:
	@$(PY) tools/check_runtime_integrity.py

classes:
	@$(PY) tools/verify.py --list

classes-list:
	@$(PY) -c "from tools.verify import CHECK_CLASSES as c; print('\n'.join(c))" \
		2>/dev/null || grep -oP '(?<=^    ")[a-z-]+(?=",)' tools/verify.py | head -13

run:
	@systemctl --user start min-agent.service

stop:
	@systemctl --user stop min-agent.service

restart:
	@systemctl --user restart min-agent.service

status:
	@./minictrl status

clean-pyc:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
