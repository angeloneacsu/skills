#!/usr/bin/env bash
# Tests for scripts/filter.jq and lib.sh helpers.
FIXTURE="$PC_TESTS_DIR/fixtures/search.json"

# usage: run_filter IGNORE_JSON [HANDLED_ID...]
run_filter() {
  local ignore="$1"; shift
  local h; h=$(mktemp)
  : > "$h"
  local id; for id in "$@"; do echo "$id" >> "$h"; done
  # shellcheck disable=SC1091
  source "$PC_SCRIPTS_DIR/lib.sh"
  jq -c --arg me me --argjson ignore "$ignore" \
    --slurpfile handled <(pc_handled_json "$h") \
    -f "$PC_SCRIPTS_DIR/filter.jq" "$FIXTURE"
  rm -f "$h"
}
ids() { jq -r '.id' | tr '\n' ' ' | sed 's/ $//'; }

test_filter_humans_unresolved() {
  local got; got=$(run_filter '[]' | ids)
  [[ "$got" == "T1 C1 R1" ]] || { echo "got: $got"; return 1; }
}
test_filter_ignore_list() {
  local got; got=$(run_filter '["andi"]')
  [[ -z "$got" ]] || { echo "got: $got"; return 1; }
}
test_filter_handled() {
  local got; got=$(run_filter '[]' C1 | ids)
  [[ "$got" == "T1 R1" ]] || { echo "got: $got"; return 1; }
}
test_filter_item_shape() {
  run_filter '[]' | jq -e 'select(.id=="T1")
    | .repo=="my-org/svc" and .number==12 and .kind=="thread" and .author=="andi"
      and .title=="Add widget" and (.url|startswith("https://")) and has("createdAt")' >/dev/null
}
test_filter_null_author_skipped() {
  # C0 has a null (ghost) author: must not crash and must not appear
  local got; got=$(run_filter '[]' | ids)
  [[ "$got" != *C0* ]]
}
test_lib_warns_on_truncation() {
  # shellcheck disable=SC1091
  source "$PC_SCRIPTS_DIR/lib.sh"
  local err; err=$(pc_warn_truncated < "$FIXTURE" 2>&1 >/dev/null)
  [[ "$err" == *"my-org/svc#12"* && "$err" == *reviewThreads* ]] || { echo "got: $err"; return 1; }
  # no warning when nothing is truncated
  err=$(jq '.data.search.nodes[0].reviewThreads.pageInfo.hasNextPage=false' "$FIXTURE" | pc_warn_truncated 2>&1 >/dev/null)
  [[ -z "$err" ]]
}

test_lib_config() {
  local d; d=$(mktemp -d)
  # shellcheck disable=SC1091
  ( export XDG_CONFIG_HOME="$d/c" XDG_STATE_HOME="$d/s" HOME="$d/h"
    source "$PC_SCRIPTS_DIR/lib.sh"
    pc_load_config 2>/dev/null; [[ $? -eq 2 ]] || exit 1
    mkdir -p "$d/c/pr-comments"
    echo 'ORGS=(my-org other)' > "$d/c/pr-comments/config"
    pc_load_config
    [[ ${#ORGS[@]} -eq 2 && $INTERVAL_MIN == 5 && $TERMINAL == "gnome-terminal --" \
       && $WORKTREE_DIR == "$d/h/work/temp" ]] || exit 1
    [[ $PC_SEEN == "$d/s/pr-comments/seen" && -d "$d/s/pr-comments" ]] ) || { rm -rf "$d"; return 1; }
  rm -rf "$d"
}
