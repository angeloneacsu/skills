#!/usr/bin/env bash
# Shared config/state helpers for pr-comments. Source this file; do not execute.

PC_DIR_NAME="pr-comments"

pc_config_file() { echo "${XDG_CONFIG_HOME:-$HOME/.config}/$PC_DIR_NAME/config"; }

# Echo (and create) the state dir.
pc_state_dir() {
  local d="${XDG_STATE_HOME:-$HOME/.local/state}/$PC_DIR_NAME"
  mkdir -p "$d"
  echo "$d"
}

# State files. seen/handled hold one GraphQL node ID per line.
PC_SEEN="$(pc_state_dir)/seen"
PC_HANDLED="$(pc_state_dir)/handled"
PC_ERROR="$(pc_state_dir)/error-notified"
PC_AUTH="$(pc_state_dir)/auth-paused"

# Load the bash-sourceable config, applying defaults. Returns 2 if missing.
pc_load_config() {
  local f; f="$(pc_config_file)"
  if [[ ! -f "$f" ]]; then
    echo "pr-comments: config not found: $f (see config.example)" >&2
    return 2
  fi
  ORGS=() CLONE_ROOTS=() IGNORE_AUTHORS=()
  CLONE_DIR="" WORKTREE_DIR="" TERMINAL="" INTERVAL_MIN="" ACTIVE_DAYS="" ACTIVE_HOURS=""
  REMOTE="" RUN_TIMER="" SKILL_CMD=""
  # shellcheck disable=SC1090
  source "$f"
  [[ -n "$WORKTREE_DIR" ]] || WORKTREE_DIR="$HOME/work/temp"
  [[ -n "$TERMINAL" ]] || TERMINAL="gnome-terminal --"
  [[ -n "$INTERVAL_MIN" ]] || INTERVAL_MIN=5
  [[ -n "$ACTIVE_DAYS" ]] || ACTIVE_DAYS="Mon..Fri"
  [[ -n "$ACTIVE_HOURS" ]] || ACTIVE_HOURS="08..18"
  [[ -n "$CLONE_DIR" ]] || CLONE_DIR="$HOME/src"
  [[ -n "$RUN_TIMER" ]] || RUN_TIMER=yes
  [[ -n "$SKILL_CMD" ]] || SKILL_CMD="/angelo-skills:pr-comments"
  return 0
}

# Handled-file encoding: the handled file is plain text, one node ID per line.
# filter.jq reads it via --slurpfile, which needs JSON values, so convert with
#   --slurpfile handled <(pc_handled_json "$PC_HANDLED")
# (one JSON string per line; a missing file yields an empty list).
pc_handled_json() {
  [[ -f "$1" ]] || return 0
  jq -Rc 'select(length > 0)' "$1"
}

# Reads a GraphQL response on stdin; warns on stderr for every truncated page
# (hasNextPage true) instead of silently dropping data.
pc_warn_truncated() {
  jq -r '
    (if .data.search.pageInfo.hasNextPage then "pr-comments: warning: more than 50 open PRs, only the first page is checked" else empty end),
    ((.data.search.nodes // [])[] | select(.number != null) as $pr
      | ["reviewThreads","comments","reviews"][] as $f
      | select($pr[$f].pageInfo.hasNextPage == true)
      | "pr-comments: warning: \($pr.repository.nameWithOwner)#\($pr.number) has more than 100 \($f), only the first page is checked")
  ' >&2
}
