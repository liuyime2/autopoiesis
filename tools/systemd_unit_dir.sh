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

The systemd user manager searches $HOME/.config/systemd/user unless it was
started with XDG_CONFIG_HOME set. /home is at user quota here, so that path
cannot be created, and the manager's environment does not include
XDG_CONFIG_HOME.

Fix one of:
  1. free a few MB under $HOME, then: minictrl install-service
  2. export MIN_AGENT_UNIT_DIR=/some/writable/path before installing
  3. run without systemd: nohup ./run_forever.sh &   (crash-loop guarded)
MSG
exit 2
