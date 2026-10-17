#!/usr/bin/env bash
# Reproduce docs/evidence/fresh-clone.log.
#
# Clones the committed tree into a scratch directory, installs it into a new venv, and runs
# the steps that must work before anyone touches a broker: the documented Makefile
# entry points (lint, type, test, the no-credentials smoke), the full test suite,
# the example cycle, the entry point, provenance, the gate's own self-test, and the gate.
#
# No credentials are read and none are required. If a step fails, the log records
# RESULT: FAIL and the script exits non-zero, so a failing clone cannot be mistaken for
# a passing one by reading only the PASS lines.
#
# Usage: make fresh-clone [FRESH_CLONE_DEST=/new/disposable/directory]
set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-$(mktemp -d "${TMPDIR:-/tmp}/autopoiesis-fresh-clone.XXXXXX")}"
LOG="$REPO/docs/evidence/fresh-clone.log"
: > "$LOG"
trap 'failed_status=$?; echo "RESULT: FAIL (setup or harness, exit $failed_status)" >> "$LOG"; exit "$failed_status"' ERR

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
while IFS= read -r variable; do
  case "$variable" in
    AUTOPOIESIS_*|ALPACA_*|APCA_*|OLLAMA_*) unset "$variable" ;;
  esac
done < <(compgen -v)
# Make includes an env file before running recipes. Unsetting API variables alone
# lets it immediately reload the operator's file. A disposable XDG directory also
# isolates shell/CLI entry points without changing HOME.
CLEAN_ENV=(env -u PYTHONPATH -u MAKEFLAGS -u MFLAGS -u MAKELEVEL
           ENVFILE=/dev/null AUTOPOIESIS_ENV_FILE=/dev/null
           XDG_CONFIG_HOME="$DEST/.isolated-config")

rm -rf "$DEST"
git clone --quiet "$REPO" "$DEST"
mkdir -p "$DEST/.isolated-config"
# A new venv, as the README tells a newcomer to make. Installing into whatever `python3` was
# on PATH proved only that an environment which already had every dependency still had them
# - the previous log's install section was a page of "Requirement already satisfied".
"$BASE_PYTHON" -m venv "$DEST/.venv"
# Every step runs inside the clone, so the venv's python is named relative to it, and pip's
# cache lives in the clone too. The log then records the run and not the operator's home
# directory - it is committed to a public repository - while staying the unedited output of a
# real run.
PYTHON=".venv/bin/python"
export PIP_CACHE_DIR="$DEST/.pip-cache"
interpreter="$(cd "$DEST" && $PYTHON -c 'import sys; print(sys.executable)')"
version="$(cd "$DEST" && $PYTHON -c 'import sys; print(sys.version.split()[0])')"

{
  echo "# Fresh-clone verification log"
  echo
  echo "Produced by running the following in a clone made from the committed tree."
  echo "Harness: docs/evidence/run-fresh-clone.sh (invoked through Make)."
  echo "Reproduce with: make fresh-clone ENVFILE=/dev/null"
  echo
  echo "\$ git clone <repo> fc && cd fc"
  echo "\$ python3 -m venv .venv"
  echo "\$ source .venv/bin/activate && make install ENVFILE=/dev/null"
  echo
  echo "## commit under test"
  git -C "$DEST" log --oneline -1
  echo
  echo "## environment"
  echo "interpreter: $interpreter"
  echo "version: $version"
  # Probed, not assumed. Every one of these is cleared above; if any survives, the log says
  # so rather than claiming a clean room that did not happen.
  leaked=""
  for var in ALPACA_API_KEY ALPACA_SECRET_KEY APCA_API_KEY_ID APCA_API_SECRET_KEY; do
    if [ -n "${!var:-}" ]; then leaked="$leaked $var"; fi
  done
  if [ -n "$leaked" ]; then
    echo "credentials: NOT ISOLATED - still set:$leaked"
  else
    echo "credentials: none present (verified by probing, after unset)"
  fi
  echo "child env files: ENVFILE=/dev/null; AUTOPOIESIS_ENV_FILE=/dev/null"
  echo "child XDG config: empty disposable directory; HOME unchanged"
  echo
  echo "## install"
  echo "\$ source .venv/bin/activate && make install"
} > "$LOG" 2>&1

if ! (cd "$DEST" && "${CLEAN_ENV[@]}" bash -c 'source .venv/bin/activate && make install') >> "$LOG" 2>&1; then
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

run_step lint    "source .venv/bin/activate && make lint"
# The Makefile is the documented developer entry point - the README tells a newcomer
# `make install` then `make check` - and this script used to prove only the raw commands while
# the thing a newcomer actually runs stayed unverified. `make check` is lint + type + test, so
# a broken Makefile target now fails here instead of in a newcomer's shell.
#
# The venv is activated first because the Makefile prefers an active venv over conda: someone
# who made a venv and ran `make install` in it has said which interpreter they mean, and
# `conda run -n llm` would silently ignore it. That also keeps this step from reaching the
# operator's conda environment and proving nothing about the clone.
run_step make-check "source .venv/bin/activate && make check"
# The no-credentials entry point, which is the first thing a contributor without a paper
# account runs. It was invoking the deleted `autopoiesis.cli` module, so it failed outright while
# every other step in this script was green.
run_step smoke-offline "source .venv/bin/activate && make smoke-offline"
run_step test    "source .venv/bin/activate && make test"
run_step example "source .venv/bin/activate && make example-cycle"
run_step entry-point "source .venv/bin/activate && make cli-help"
run_step provenance "source .venv/bin/activate && make reproduce"
run_step self-test "source .venv/bin/activate && make verify-self-test"
# The gate itself, in the clone, with no credentials and no runtime directory. This is the
# step that matters most and the one every earlier version of this script omitted: the other
# six prove the package installs and runs, while this proves a newcomer can run the gate
# before they have a paper account or have recorded a single cycle. It was red here for
# several commits - a gitignored evidence log, two checks that treated absent state as
# corruption, and doctor demanding credentials - and no amount of green lint would have
# found that.
run_step gate "source .venv/bin/activate && make verify"

if [ "$failures" -ne 0 ]; then
  echo "fresh clone: $failures step(s) failed, see $LOG" >&2
  exit 1
fi
echo "fresh clone: all steps pass, log at docs/evidence/fresh-clone.log"
