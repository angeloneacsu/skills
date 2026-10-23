#!/usr/bin/env bash
# Tests for scripts/pr-comments-install (sandboxed HOME/XDG, systemctl stub).
INSTALL="$PC_SCRIPTS_DIR/pr-comments-install"

install_setup() {
  rm -rf "$HOME" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME"
  mkdir -p "$HOME"
  STUB_LOG=$(mktemp); export STUB_LOG
  export PATH="$PC_TESTS_DIR/stubs:${PC_ORIG_PATH:=$PATH}"
  UNITS="$XDG_CONFIG_HOME/systemd/user"
  CONF="$XDG_CONFIG_HOME/pr-comments/config"
}

test_install_files() {
  install_setup
  "$INSTALL" >/dev/null
  for b in poll notify open; do [[ -x "$HOME/.local/bin/pr-comments-$b" ]] || return 1; done
  for f in lib.sh filter.jq query.graphql; do [[ -f "$HOME/.local/share/pr-comments/$f" ]] || return 1; done
  [[ -f "$UNITS/pr-comments.service" && -f "$UNITS/pr-comments.timer" ]] || return 1
  grep -qx 'ExecStart=%h/.local/bin/pr-comments-poll' "$UNITS/pr-comments.service" || return 1
  grep -qx 'Type=oneshot' "$UNITS/pr-comments.service" || return 1
}

test_install_timer_calendar() {
  install_setup
  "$INSTALL" >/dev/null
  grep -qxF 'OnCalendar=Mon..Fri *-*-* 08..18:00/5:00' "$UNITS/pr-comments.timer" || { cat "$UNITS/pr-comments.timer"; return 1; }
  grep -qx 'Persistent=false' "$UNITS/pr-comments.timer" || return 1
}

test_install_timer_custom_config() {
  install_setup
  mkdir -p "$(dirname "$CONF")"
  printf 'ORGS=(x)\nINTERVAL_MIN=10\nACTIVE_DAYS="Mon..Sat"\nACTIVE_HOURS="09..17"\n' > "$CONF"
  "$INSTALL" >/dev/null
  grep -qxF 'OnCalendar=Mon..Sat *-*-* 09..17:00/10:00' "$UNITS/pr-comments.timer" || { cat "$UNITS/pr-comments.timer"; return 1; }
}

test_install_creates_config_when_absent() {
  install_setup
  out=$("$INSTALL")
  [[ -f "$CONF" ]] || return 1
  grep -q "edit $CONF" <<<"$out" || { echo "$out"; return 1; }
  grep -qxF 'OnCalendar=Mon..Fri *-*-* 08..18:00/5:00' "$UNITS/pr-comments.timer" || return 1
}

test_install_keeps_existing_config() {
  install_setup
  mkdir -p "$(dirname "$CONF")"
  printf 'ORGS=(mine)\n# custom\n' > "$CONF"
  cp "$CONF" "$CONF.orig"
  "$INSTALL" >/dev/null
  cmp "$CONF" "$CONF.orig" || return 1
}

test_install_enables_timer() {
  install_setup
  "$INSTALL" >/dev/null
  grep -qx 'systemctl --user daemon-reload' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  grep -qx 'systemctl --user enable --now pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
}

test_install_service_has_install_time_path() {
  install_setup
  PATH="/opt/my bin:/home/x/100%/bin:$PATH" "$INSTALL" >/dev/null
  svc="$UNITS/pr-comments.service"
  grep -q '^Environment="PATH=' "$svc" || { cat "$svc"; return 1; }
  grep -qF '/opt/my bin:/home/x/100%%/bin:' "$svc" || { cat "$svc"; return 1; }
  grep -qF "$PC_TESTS_DIR/stubs" "$svc" || return 1
}

test_install_uninstall() {
  install_setup
  "$INSTALL" >/dev/null
  : > "$STUB_LOG"
  "$INSTALL" --uninstall >/dev/null
  for b in poll notify open; do [[ ! -e "$HOME/.local/bin/pr-comments-$b" ]] || return 1; done
  [[ ! -e "$HOME/.local/share/pr-comments" ]] || return 1
  [[ ! -e "$UNITS/pr-comments.timer" && ! -e "$UNITS/pr-comments.service" ]] || return 1
  [[ -f "$CONF" ]] || return 1
  grep -qx 'systemctl --user disable --now pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
}

test_installed_scripts_resolve_lib() {
  install_setup
  "$INSTALL" >/dev/null
  # installed notify/open must find lib.sh in the share dir (not next to themselves)
  "$HOME/.local/bin/pr-comments-open" bad-usage 2>&1 | grep -q 'usage: pr-comments-open' || return 1
}
