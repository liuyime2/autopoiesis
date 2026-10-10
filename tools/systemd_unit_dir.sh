#!/usr/bin/env bash
# Print a directory the systemd user manager will actually search, preferring one
# that survives a reboot.
#
# The bug this replaces: the previous version trusted the *shell's*
# XDG_CONFIG_HOME. The systemd user manager resolves unit paths from its own
# start-time environment, which on this host has no XDG_CONFIG_HOME, so it
# searched $HOME/.config/systemd/user and $XDG_RUNTIME_DIR/systemd/user. The
# old script "succeeded" by writing to $XDG_CONFIG_HOME - a directory systemd
# never reads - so the deployed units were dead files, the running daemon kept
# executing stale copies out of /run, and `systemctl is-enabled` cheerfully
# reported "enabled" while the enablement symlink pointed into tmpfs.
#
# So: ask the manager, do not assume.
set -uo pipefail

# What the manager itself believes. Falls back to $HOME/.config, which is the
# documented default when XDG_CONFIG_HOME is unset in the manager environment.
manager_config_home() {
  local env_home
  env_home="$(systemctl --user show-environment 2>/dev/null \
    | sed -n 's/^XDG_CONFIG_HOME=//p' | head -1)"
  if [ -n "$env_home" ]; then
    echo "$env_home"
  else
    echo "$HOME/.config"
  fi
}

# $XDG_RUNTIME_DIR is tmpfs: anything installed there is gone after a reboot, and
# an enablement symlink into it becomes a dangling link. It is a last resort,
# never a first choice.
runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/systemd/user"

candidates=(
  "${AUTOPOIESIS_UNIT_DIR:-}"
  "$(manager_config_home)/systemd/user"
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

# Nothing durable worked. Say so, and name the tmpfs fallback explicitly instead
# of silently producing a configuration that dies at the next reboot.
if [ "${AUTOPOIESIS_ALLOW_RUNTIME_UNITS:-0}" = "1" ] && [ -d "$runtime_dir" ] && [ -w "$runtime_dir" ]; then
  echo "$runtime_dir" >&2
  echo "$runtime_dir"
  exit 0
fi

cat >&2 <<MSG
FATAL: no durable writable systemd user unit directory.

Checked, in order:
  \${AUTOPOIESIS_UNIT_DIR}
  $(manager_config_home)/systemd/user   <- where the manager actually looks
  $runtime_dir                            <- REJECTED: tmpfs, dies on reboot

The manager's own environment reports:
$(systemctl --user show-environment 2>/dev/null | grep -E '^(HOME|XDG_CONFIG_HOME)=' | sed 's/^/  /')

Most likely cause on this host: \$HOME is at user quota, so
$HOME/.config/systemd/user cannot be created. Note that this is the *manager's*
path: setting XDG_CONFIG_HOME in your shell does not affect it, and writing units
to \$XDG_CONFIG_HOME produces files systemd never reads.

Fix one of:
  1. free a few MB under \$HOME so $HOME/.config/systemd/user can be created
     (this is the only location a reboot-persistent *user* unit can live)
  2. export AUTOPOIESIS_UNIT_DIR=/some/writable/path before installing
  3. export AUTOPOIESIS_ALLOW_RUNTIME_UNITS=1 to accept tmpfs units that will NOT
     survive a reboot
  4. run without systemd: minictrl daemon              # foreground, exits on error
MSG
exit 2
