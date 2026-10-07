#!/usr/bin/env bash
# Reproduce docs/evidence/fresh-clone.log.
#
# Clones the committed tree into a scratch directory, installs it into a new venv, and runs
# the seven steps that must work before anyone touches a broker: lint, the full test suite,
# the example cycle, the entry point, provenance, the gate's own self-test, and the gate.
#
# No credentials are read and none are required. If a step fails, the log records
# RESULT: FAIL and the script exits non-zero, so a failing clone cannot be mistaken for
# a passing one by reading only the PASS lines.
#
# Usage: docs/evidence/run-fresh-clone.sh [destination-directory]
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-$(mktemp -d "${TMPDIR:-/tmp}/min-agent-fresh-clone.XXXXXX")}"
LOG="$REPO/docs/evidence/fresh-clone.log"

# Deliberately not the invoking shell's interpreter. A run that inherits the developer's
# active environment proves less than one that has to discover its own, and proving that
# is the point of this script.
BASE_PYTHON="${PYTHON:-python3}"

# Isolation is performed, not asserted. The log used to print
#   "credentials: none present"
# from a hardcoded echo, while the script inherited the operator's environment - so the
# committed evidence claimed a clean-room run that had not happened. The clone's own gate
# output gave it away: it reported only "no runtime state in this checkout", which the gate
# reaches when credentials ARE present. Both the variables and the env file are removed below,
# and the log records what was actually found rather than a claim.
unset ALPACA_API_KEY ALPACA_SECRET_KEY APCA_API_KEY APCA_API_SECRET_KEY \
      ALPACA_BASE_URL APCA_API_BASE_URL XDG_CONFIG_HOME
CLEAN_ENV=(env -u ALPACA_API_KEY -u ALPACA_SECRET_KEY -u APCA_API_KEY -u APCA_API_SECRET_KEY
           -u ALPACA_BASE_URL -u APCA_API_BASE_URL -u XDG_CONFIG_HOME)

rm -rf "$DEST"
git clone --quiet "$REPO" "$DEST"
# A new venv, as the README tells a newcomer to make. Installing into whatever `python3` was
# on PATH proved only that an environment which already had every dependency still had them
# - the previous log's install section was a page of "Requirement already satisfied".
"$BASE_PYTHON" -m venv "$DEST/.venv"
PYTHON="$DEST/.venv/bin/python"

{
  echo "# Fresh-clone verification log"
  echo
  echo "Produced by running the following in a clone made from the committed tree."
  echo "Reproduce with: docs/evidence/run-fresh-clone.sh"
  echo
  echo "\$ git clone <repo> fc && cd fc"
  echo "\$ python3 -m venv .venv"
  echo "\$ .venv/bin/python -m pip install -e \".[dev]\""
  echo
  echo "## commit under test"
  git -C "$DEST" log --oneline -1
  echo
  echo "## environment"
  echo "interpreter: $($PYTHON -c 'import sys; print(sys.executable)')"
  echo "version: $($PYTHON -c 'import sys; print(sys.version.split()[0])')"
  # Probed, not assumed. Every one of these is cleared above; if any survives, the log says
  # so rather than claiming a clean room that did not happen.
  leaked=""
  for var in ALPACA_API_KEY ALPACA_SECRET_KEY APCA_API_KEY APCA_API_SECRET_KEY XDG_CONFIG_HOME; do
    if [ -n "${!var:-}" ]; then leaked="$leaked $var"; fi
  done
  if [ -n "$leaked" ]; then
    echo "credentials: NOT ISOLATED - still set:$leaked"
  else
    echo "credentials: none present (verified by probing, after unset)"
  fi
  echo
  echo "## install"
  echo "\$ $PYTHON -m pip install -e \".[dev]\""
} > "$LOG" 2>&1

if ! (cd "$DEST" && "$PYTHON" -m pip install -e ".[dev]") >> "$LOG" 2>&1; then
  echo "RESULT: FAIL" >> "$LOG"
  echo "fresh clone: install failed, see $LOG" >&2
  exit 1
fi

failures=0
run_step() {
  local name="$1" cmd="$2"
  {
    echo
    echo "## $name"
    echo "\$ $cmd"
  } >> "$LOG"
  if (cd "$DEST" && "${CLEAN_ENV[@]}" bash -c "$cmd") >> "$LOG" 2>&1; then
    echo "RESULT: PASS" >> "$LOG"
  else
    echo "RESULT: FAIL" >> "$LOG"
    failures=$((failures + 1))
  fi
}

run_step lint    "$PYTHON -m ruff check src/ tools/ tests/ examples/"
run_step test    "$PYTHON -m pytest -q"
run_step example "$PYTHON examples/minimal_cycle.py"
run_step entry-point "$PYTHON -m min_agent.cli --help"
run_step provenance "$PYTHON tools/provenance.py"
run_step self-test "$PYTHON tools/verify.py --self-test"
# The gate itself, in the clone, with no credentials and no runtime directory. This is the
# step that matters most and the one every earlier version of this script omitted: the other
# six prove the package installs and runs, while this proves a newcomer can run the gate
# before they have a paper account or have recorded a single cycle. It was red here for
# several commits - a gitignored evidence log, two checks that treated absent state as
# corruption, and doctor demanding credentials - and no amount of green lint would have
# found that.
run_step gate "$PYTHON tools/verify.py"

if [ "$failures" -ne 0 ]; then
  echo "fresh clone: $failures step(s) failed, see $LOG" >&2
  exit 1
fi
echo "fresh clone: all steps pass, log at docs/evidence/fresh-clone.log"