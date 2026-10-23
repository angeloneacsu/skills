#!/usr/bin/env bash
# Tests for scripts/pr-comments-poll (gh/systemd stubs, sandboxed HOME/XDG).
POLL_FIXTURE="$PC_TESTS_DIR/fixtures/search.json"
POLL="$PC_SCRIPTS_DIR/pr-comments-poll"

# Fresh state + config + stubs on PATH. Sets STUB_LOG, GH_FIXTURE, SEEN, HANDLED, STATE.
poll_setup() {
  STATE="$XDG_STATE_HOME/pr-comments"
  rm -rf "$STATE" "$XDG_CONFIG_HOME/pr-comments" "$XDG_RUNTIME_DIR/pr-comments"
  mkdir -p "$STATE" "$XDG_CONFIG_HOME/pr-comments"
  echo 'ORGS=(my-org)' > "$XDG_CONFIG_HOME/pr-comments/config"
  SEEN="$STATE/seen" HANDLED="$STATE/handled"
  STUB_LOG=$(mktemp); export STUB_LOG
  GH_FIXTURE="$POLL_FIXTURE"; export GH_FIXTURE
  unset GH_EXIT GH_STDERR GH_AUTH_EXIT GH_LOGIN SYSTEMD_RUN_EXIT BUSCTL_EXIT SSH_EXIT SSH_STDERR SSH_EMIT
  export PATH="$PC_TESTS_DIR/stubs:${PC_ORIG_PATH:=$PATH}"
}
count_log() { grep -c -- "$1" "$STUB_LOG" || true; }
sorted_seen() { sort "$SEEN" | tr '\n' ' ' | sed 's/ $//'; }

test_poll_first_run_wave() {
  poll_setup
  "$POLL" 2>/dev/null
  [[ $(count_log '^systemd-run') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  grep '^systemd-run' "$STUB_LOG" | grep -q -- 'pr-comments-notify my-org/svc 12 ' || { cat "$STUB_LOG"; return 1; }
  grep '^systemd-run' "$STUB_LOG" | grep -q -- '3 new comment(s) from reviewer' || { cat "$STUB_LOG"; return 1; }
  grep '^systemd-run' "$STUB_LOG" | grep -q -- '--user --collect --quiet --setenv=PATH=' || { cat "$STUB_LOG"; return 1; }
  [[ "$(sorted_seen)" == "C1 R1 T1" ]] || { echo "seen: $(sorted_seen)"; return 1; }
}

test_poll_no_repeat() {
  poll_setup
  "$POLL" 2>/dev/null
  : > "$STUB_LOG"
  "$POLL" 2>/dev/null
  [[ $(count_log '^systemd-run') == 0 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_new_reply_notifies() {
  poll_setup
  "$POLL" 2>/dev/null
  : > "$STUB_LOG"
  local f; f=$(mktemp)
  sed 's/"T1"/"T1b"/' "$POLL_FIXTURE" > "$f"
  GH_FIXTURE="$f" "$POLL" 2>/dev/null
  rm -f "$f"
  [[ $(count_log '^systemd-run') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  grep '^systemd-run' "$STUB_LOG" | grep -q -- '1 new comment(s) from reviewer' || { cat "$STUB_LOG"; return 1; }
  grep -qx 'T1b' "$SEEN"
}

test_poll_reset_wave_again() {
  poll_setup
  echo H1 > "$HANDLED"
  "$POLL" 2>/dev/null
  "$POLL" --reset
  [[ ! -s "$SEEN" ]] || return 1
  [[ "$(cat "$HANDLED")" == H1 ]] || return 1
  : > "$STUB_LOG"
  "$POLL" 2>/dev/null
  [[ $(count_log '^systemd-run') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  "$POLL" --reset --all
  [[ ! -s "$SEEN" && ! -s "$HANDLED" ]]
}

test_poll_mark_handled() {
  poll_setup
  "$POLL" --mark-handled C1 R1
  [[ "$(cat "$HANDLED")" == $'C1\nR1' ]] || return 1
  "$POLL" 2>/dev/null
  [[ "$(sorted_seen)" == "T1" ]]
}

test_poll_dry_run() {
  poll_setup
  local out rc=0
  out=$("$POLL" --dry-run 2>/dev/null) || rc=$?
  [[ $rc -eq 0 ]] || return 1
  [[ $(printf '%s\n' "$out" | wc -l) == 3 ]] || { echo "$out"; return 1; }
  [[ "$out" == *"my-org/svc#12 reviewer thread https://"* ]] || { echo "$out"; return 1; }
  [[ $(count_log '^systemd-run') == 0 ]] || return 1
  [[ ! -s "$SEEN" ]]
}

test_poll_auth_stops_timer() {
  poll_setup
  local rc=0
  GH_EXIT=1 GH_STDERR="HTTP 401: Bad credentials" "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 3 ]] || { echo "rc=$rc"; return 1; }
  [[ $(grep '^systemd-run' "$STUB_LOG" | grep -c -- 'pr-comments-notify --auth') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  grep -qx 'systemctl --user stop pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ -e "$STATE/auth-paused" ]] || return 1
  : > "$STUB_LOG"
  rc=0
  GH_AUTH_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 3 ]] || { echo "rc2=$rc"; return 1; }
  [[ $(count_log '^systemd-run') == 0 && $(count_log '^systemctl') == 0 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_paused_auth_ok_auto_resumes() {
  poll_setup
  touch "$STATE/auth-paused"
  "$POLL" 2>/dev/null
  [[ ! -e "$STATE/auth-paused" ]] || return 1
  grep -qx 'systemctl --user start pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log '^systemd-run') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  [[ "$(sorted_seen)" == "C1 R1 T1" ]] || return 1
}

test_poll_paused_auth_bad_renotifies_once_per_boot() {
  poll_setup
  touch "$STATE/auth-paused"
  local rc=0
  GH_AUTH_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 3 ]] || { echo "rc=$rc"; return 1; }
  [[ $(grep '^systemd-run' "$STUB_LOG" | grep -c -- 'pr-comments-notify --auth') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  [[ -e "$STATE/auth-paused" ]] || return 1
  : > "$STUB_LOG"
  rc=0
  GH_AUTH_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 3 && $(count_log '^systemd-run') == 0 ]] || { cat "$STUB_LOG"; return 1; }
  # a new boot clears the runtime dir -> notified again
  rm -rf "$XDG_RUNTIME_DIR/pr-comments"
  GH_AUTH_EXIT=1 "$POLL" 2>/dev/null || true
  [[ $(count_log '^systemd-run') == 1 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_network_error_no_stop() {
  poll_setup
  local rc=0
  GH_EXIT=1 GH_STDERR="could not resolve host" "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 4 ]] || { echo "rc=$rc"; return 1; }
  [[ $(grep '^systemd-run' "$STUB_LOG" | grep -c -- 'pr-comments-notify --error') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log '^systemctl') == 0 ]] || { cat "$STUB_LOG"; return 1; }
  rc=0
  GH_EXIT=1 GH_STDERR="could not resolve host" "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 4 ]] || return 1
  [[ $(count_log 'pr-comments-notify --error') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  [[ -e "$STATE/error-notified" ]] || return 1
  "$POLL" 2>/dev/null
  [[ ! -e "$STATE/error-notified" ]]
}

test_poll_resume() {
  poll_setup
  touch "$STATE/auth-paused"
  "$POLL" --resume 2>/dev/null
  [[ ! -e "$STATE/auth-paused" ]] || return 1
  grep -qx 'systemctl --user start pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log '^systemd-run') == 1 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_pagination_warning() {
  poll_setup
  local err
  err=$("$POLL" 2>&1 >/dev/null)
  [[ "$err" == *"my-org/svc#12"* && "$err" == *"more than 100"* ]] || { echo "$err"; return 1; }
}

test_poll_graphql_errors_is_error() {
  poll_setup
  local f rc=0; f=$(mktemp)
  echo '{"errors":[{"message":"x"}],"data":null}' > "$f"
  GH_FIXTURE="$f" "$POLL" 2>/dev/null || rc=$?
  rm -f "$f"
  [[ $rc -eq 4 ]] || { echo "rc=$rc"; return 1; }
  [[ $(count_log 'pr-comments-notify --error') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log '^systemctl') == 0 ]] || return 1
}

test_poll_malformed_body_is_error() {
  poll_setup
  local f rc=0; f=$(mktemp)
  echo 'not json {' > "$f"
  GH_FIXTURE="$f" "$POLL" 2>/dev/null || rc=$?
  GH_FIXTURE="$f" "$POLL" 2>/dev/null || true
  rm -f "$f"
  [[ $rc -eq 4 ]] || { echo "rc=$rc"; return 1; }
  [[ $(count_log 'pr-comments-notify --error') == 1 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_5xx_not_auth() {
  poll_setup
  local rc=0
  GH_EXIT=1 GH_STDERR="HTTP 502: Bad Gateway" GH_AUTH_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 4 ]] || { echo "rc=$rc"; return 1; }
  [[ $(count_log '^systemctl') == 0 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_failed_spawn_retries_error_notice() {
  poll_setup
  local rc=0
  GH_EXIT=1 GH_STDERR="could not resolve host" SYSTEMD_RUN_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 4 ]] || return 1
  [[ ! -e "$STATE/error-notified" ]] || return 1
  : > "$STUB_LOG"
  GH_EXIT=1 GH_STDERR="could not resolve host" "$POLL" 2>/dev/null || true
  [[ $(count_log 'pr-comments-notify --error') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  [[ -e "$STATE/error-notified" ]]
}

test_poll_failed_spawn_auth_still_stops_timer() {
  poll_setup
  local rc=0
  GH_EXIT=1 GH_STDERR="HTTP 401: Bad credentials" SYSTEMD_RUN_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 3 ]] || return 1
  grep -qx 'systemctl --user stop pr-comments.timer' "$STUB_LOG" || return 1
  [[ ! -e "$STATE/auth-paused" ]]
}

test_poll_spawn_passes_path() {
  poll_setup
  "$POLL" 2>/dev/null
  grep '^systemd-run' "$STUB_LOG" | grep -qF -- "--setenv=PATH=$PATH" || { cat "$STUB_LOG"; return 1; }
}

test_poll_no_notification_server_keeps_unseen() {
  poll_setup
  local rc=0
  BUSCTL_EXIT=1 "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 6 ]] || { echo "rc=$rc"; return 1; }
  [[ $(count_log '^systemd-run') == 0 ]] || { cat "$STUB_LOG"; return 1; }
  [[ ! -s "$SEEN" ]] || { echo "seen: $(sorted_seen)"; return 1; }
}

test_poll_emit_prints_items_without_state() {
  poll_setup
  local out
  out=$("$POLL" --emit 2>/dev/null)
  [[ $(printf '%s\n' "$out" | wc -l) == 3 ]] || { echo "$out"; return 1; }
  jq -e 'select(.id == "T1") | .repo == "my-org/svc" and .number == 12' <<<"$out" >/dev/null || { echo "$out"; return 1; }
  [[ $(count_log '^systemd-run') == 0 && $(count_log '^busctl') == 0 && $(count_log '^systemctl') == 0 ]] || { cat "$STUB_LOG"; return 1; }
  [[ ! -s "$SEEN" ]]
}

test_poll_emit_auth_error_exit_only() {
  poll_setup
  local rc=0
  GH_EXIT=1 GH_STDERR="HTTP 401: Bad credentials" "$POLL" --emit 2>/dev/null || rc=$?
  [[ $rc -eq 3 ]] || { echo "rc=$rc"; return 1; }
  [[ $(count_log '^systemd-run') == 0 && $(count_log '^systemctl') == 0 ]] || { cat "$STUB_LOG"; return 1; }
  [[ ! -e "$STATE/auth-paused" ]] || return 1
  rc=0
  GH_EXIT=1 GH_STDERR="could not resolve host" "$POLL" --emit 2>/dev/null || rc=$?
  [[ $rc -eq 4 && $(count_log '^systemd-run') == 0 ]] || { echo "rc=$rc"; cat "$STUB_LOG"; return 1; }
}

# Desktop side: config with REMOTE; SSH_EMIT holds what the remote --emit printed.
remote_setup() {
  poll_setup
  local e; e=$(mktemp)
  "$POLL" --emit > "$e" 2>/dev/null
  : > "$STUB_LOG"
  echo 'REMOTE=vm' > "$XDG_CONFIG_HOME/pr-comments/config"
  export SSH_EMIT="$e"
}

test_poll_remote_notifies_from_ssh() {
  remote_setup
  "$POLL" 2>/dev/null
  grep -qx 'ssh -o BatchMode=yes -o ConnectTimeout=15 vm .local/bin/pr-comments-poll --emit' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log '^gh ') == 0 ]] || { cat "$STUB_LOG"; return 1; }
  grep '^systemd-run' "$STUB_LOG" | grep -q -- 'pr-comments-notify my-org/svc 12 .*3 new comment(s) from reviewer' || { cat "$STUB_LOG"; return 1; }
  [[ "$(sorted_seen)" == "C1 R1 T1" ]] || return 1
  : > "$STUB_LOG"
  "$POLL" 2>/dev/null
  [[ $(count_log '^systemd-run') == 0 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_remote_auth_error_stops_timer() {
  remote_setup
  local rc=0
  SSH_EXIT=3 SSH_STDERR="HTTP 401" "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 3 ]] || { echo "rc=$rc"; return 1; }
  [[ $(grep '^systemd-run' "$STUB_LOG" | grep -c -- 'pr-comments-notify --auth') == 1 ]] || { cat "$STUB_LOG"; return 1; }
  grep -qx 'systemctl --user stop pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ -e "$STATE/auth-paused" ]] || return 1
  # fixed on the remote side: the next run resumes and notifies
  : > "$STUB_LOG"
  "$POLL" 2>/dev/null
  [[ ! -e "$STATE/auth-paused" ]] || return 1
  grep -qx 'systemctl --user start pr-comments.timer' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log 'pr-comments-notify my-org/svc 12') == 1 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_remote_unreachable_is_error() {
  remote_setup
  local rc=0
  SSH_EXIT=255 SSH_STDERR="Connection refused" "$POLL" 2>/dev/null || rc=$?
  [[ $rc -eq 4 ]] || { echo "rc=$rc"; return 1; }
  grep '^systemd-run' "$STUB_LOG" | grep -q -- 'pr-comments-notify --error .*cannot reach vm' || { cat "$STUB_LOG"; return 1; }
  [[ $(count_log '^systemctl') == 0 ]] || { cat "$STUB_LOG"; return 1; }
}

test_poll_resume_without_timer_only_clears_flag() {
  poll_setup
  echo 'RUN_TIMER=no' >> "$XDG_CONFIG_HOME/pr-comments/config"
  touch "$STATE/auth-paused"
  "$POLL" --resume >/dev/null
  [[ ! -e "$STATE/auth-paused" ]] || return 1
  [[ $(count_log '^systemctl') == 0 && $(count_log '^systemd-run') == 0 && $(count_log '^gh ') == 0 ]] || { cat "$STUB_LOG"; return 1; }
}
