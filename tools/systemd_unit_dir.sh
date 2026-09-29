#!/usr/bin/env bash
# Print a directory the systemd user manager will actually search.
#
# The manager resolves unit paths from its own start-time environment, which on
# this host has no XDG_CONFIG_HOME. So it searches $HOME/.config/systemd/user -
# and /home is at user quota, so that cannot be created. Try the paths in
# priority order and report clearly when none is usable, rather than writing
# units somewhere that will never be read.
set -uo pipefail

candidates=(
  "${MIN_AGENT_UNIT_DIR:-}"
  "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  "$HOME/.config/systemd/user"
  "/run/user/$(id -u)/systemd/user"
)

for dir in "${candidates[@]}"; do
  [ -n "$dir" ] || continue
  if [ -d "$dir" ] && [ -w "$dir" ]; then
    echo "$dir"
    exit 0
  fi
  parent="$(dirname "$dir")"
  if [ -d "$parent" ] && [ -w "$parent" ]; then
    echo "$dir"
    exit 0
  fi
done

cat >&2 <<'MSG'
FATAL: no writable systemd user unit directory.

None of the candidate directories was writable. Checked, in order:
  ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
  $HOME/.config/systemd/user
  any writable path named by MIN_AGENT_UNIT_DIR

On this host XDG_CONFIG_HOME is set to /localscratch/liuyime2/ohome/.config,
which is off $HOME, so the first candidate normally succeeds and $HOME's quota
is irrelevant. If that stops being true, the quota on $HOME - not this system -
is the cause.

Fix one of:
  1. check XDG_CONFIG_HOME is set and writable, then: minictrl install-service
  2. export MIN_AGENT_UNIT_DIR=/some/writable/path before installing
  3. run without systemd: nohup ./run_forever.sh &   (crash-loop guarded)
MSG
exit 2
