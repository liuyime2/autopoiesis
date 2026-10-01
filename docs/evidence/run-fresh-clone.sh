#!/usr/bin/env bash
# Reproduce docs/evidence/fresh-clone.log.
#
# Clones the committed tree into a scratch directory, installs it, and runs the six
# steps that must work before anyone touches a broker: lint, the full test suite, the
# example cycle, the entry point, provenance, and the gate's own self-test.
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
PYTHON="${PYTHON:-python3}"

rm -rf "$DEST"
git clone --quiet "$REPO" "$DEST"

{
  echo "# Fresh-clone verification log"
  echo
  echo "Produced by running the following in a clone made from the committed tree."
  echo "Reproduce with: docs/evidence/run-fresh-clone.sh"
  echo
  echo "\$ git clone <repo> fc && cd fc"
  echo "\$ python -m pip install -e \".[dev]\""
  echo
  echo "## commit under test"
  git -C "$DEST" log --oneline -1
  echo
  echo "## environment (no credentials present)"
  echo "interpreter: $($PYTHON -c 'import sys; print(sys.executable)')"
  echo "version: $($PYTHON -c 'import sys; print(sys.version.split()[0])')"
  echo "credentials: none present; the gate's broker checks skip rather than fail"
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
  if (cd "$DEST" && eval "$cmd") >> "$LOG" 2>&1; then
    echo "RESULT: PASS" >> "$LOG"
  else
    echo "RESULT: FAIL" >> "$LOG"
    failures=$((failures + 1))
  fi
}

run_step lint    "$PYTHON -m ruff check src/ tools/ tests/ examples/"
run_step test    "$PYTHON -m pytest -q"
run_step example "$PYTHON examples/minimal_cycle.py"
run_step entry-point "min-agent --help"
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