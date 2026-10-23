#!/usr/bin/env bash
# Tests for scripts/pr-comments-open (real local git repos; gh/terminal stubs).
OPEN="$PC_SCRIPTS_DIR/pr-comments-open"

# Builds origin (bare), a clone under CLONE_ROOTS, config and stubs. Sets T, ORIGIN, CLONE, WT.
open_setup() {
  T=$(mktemp -d)
  ORIGIN="$T/origin.git" CLONE="$T/roots/svc" WT="$T/wt"
  local seed="$T/seed"
  git init -q --bare -b main "$ORIGIN"
  git init -q -b main "$seed"
  git -C "$seed" config user.name t; git -C "$seed" config user.email t@example.com
  echo a > "$seed/f"; git -C "$seed" add f; git -C "$seed" commit -q -m one
  git -C "$seed" checkout -q -b feature/x
  echo b >> "$seed/f"; git -C "$seed" commit -q -am two
  git -C "$seed" push -q "$ORIGIN" main feature/x
  mkdir -p "$T/roots"
  git clone -q "$ORIGIN" "$CLONE"
  git -C "$CLONE" config user.name t; git -C "$CLONE" config user.email t@example.com
  git -C "$CLONE" remote set-url origin https://github.com/my-org/svc.git
  git -C "$CLONE" config "url.$ORIGIN.insteadOf" https://github.com/my-org/svc.git
  OPEN_SEED="$seed"
  rm -rf "$XDG_CONFIG_HOME/pr-comments"; mkdir -p "$XDG_CONFIG_HOME/pr-comments" "$HOME"
  cat > "$XDG_CONFIG_HOME/pr-comments/config" <<CFG
CLONE_ROOTS=("$T/roots")
CLONE_DIR="$T/clones"
WORKTREE_DIR="$WT"
TERMINAL="gnome-terminal --"
CFG
  STUB_LOG=$(mktemp); export STUB_LOG
  GH_PR_JSON='{"headRefName":"feature/x","headRepository":{"name":"svc"},"headRepositoryOwner":{"login":"my-org"},"url":"https://github.com/my-org/svc/pull/12"}'
  export GH_PR_JSON GH_CLONE_SRC="$ORIGIN"
  unset GH_EXIT GH_STDERR
  export PATH="$PC_TESTS_DIR/stubs:${PC_ORIG_PATH:=$PATH}"
}

test_open_creates_detached_worktree() {
  open_setup
  git -C "$CLONE" checkout -q feature/x
  echo dirty >> "$CLONE/f"
  local before; before=$(git -C "$CLONE" status --porcelain)
  "$OPEN" my-org/svc 12
  [[ -d "$WT/svc-pr-12" ]] || return 1
  [[ $(git -C "$WT/svc-pr-12" rev-parse HEAD) == $(git -C "$CLONE" rev-parse origin/feature/x) ]] || return 1
  ! git -C "$WT/svc-pr-12" symbolic-ref -q HEAD || return 1
  [[ $(git -C "$CLONE" status --porcelain) == "$before" ]] || return 1
  [[ $(git -C "$CLONE" branch --show-current) == feature/x ]] || return 1
}

test_open_launches_terminal() {
  open_setup
  "$OPEN" my-org/svc 12
  grep '^gnome-terminal' "$STUB_LOG" | grep -q -- "cd $WT/svc-pr-12" || { cat "$STUB_LOG"; return 1; }
  grep '^gnome-terminal' "$STUB_LOG" | grep -q -- '/pr-comments https://github.com/my-org/svc/pull/12' || { cat "$STUB_LOG"; return 1; }
  ! grep -q 'PR_COMMENTS_WORKTREE_STATE' "$STUB_LOG" || return 1
}

test_open_recreates_clean_leftover() {
  open_setup
  mkdir -p "$WT"
  git -C "$CLONE" worktree add -q --detach "$WT/svc-pr-12" origin/main
  "$OPEN" my-org/svc 12
  [[ $(git -C "$WT/svc-pr-12" rev-parse HEAD) == $(git -C "$CLONE" rev-parse origin/feature/x) ]] || return 1
}

test_open_keeps_dirty_leftover() {
  open_setup
  mkdir -p "$WT"
  git -C "$CLONE" worktree add -q --detach "$WT/svc-pr-12" origin/main
  echo mine > "$WT/svc-pr-12/untracked"
  "$OPEN" my-org/svc 12
  [[ -f "$WT/svc-pr-12/untracked" ]] || return 1
  grep '^gnome-terminal' "$STUB_LOG" | grep -q 'PR_COMMENTS_WORKTREE_STATE=kept-dirty' || { cat "$STUB_LOG"; return 1; }
}

test_open_clones_missing_repo() {
  open_setup
  mv "$CLONE" "$T/elsewhere"
  "$OPEN" my-org/svc 12
  grep -q "^gh repo clone my-org/svc $T/clones/svc" "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  [[ -d "$WT/svc-pr-12" ]] || return 1
}

test_open_fork_rejected() {
  open_setup
  export GH_PR_JSON='{"headRefName":"feature/x","headRepository":{"name":"svc"},"headRepositoryOwner":{"login":"someone"},"url":"https://github.com/my-org/svc/pull/12"}'
  local rc=0
  "$OPEN" my-org/svc 12 2>/dev/null || rc=$?
  [[ $rc -eq 5 ]] || { echo "rc=$rc"; return 1; }
  [[ ! -e "$WT/svc-pr-12" ]] || return 1
  grep -q 'notify-send .*PR from fork not supported' "$STUB_LOG" || { cat "$STUB_LOG"; return 1; }
  ! grep -q '^gnome-terminal' "$STUB_LOG" || return 1
}

test_open_fix_auth() {
  open_setup
  "$OPEN" --fix-auth
  grep '^gnome-terminal' "$STUB_LOG" | grep -q -- '/pr-comments --fix-auth' || { cat "$STUB_LOG"; return 1; }
  grep '^gnome-terminal' "$STUB_LOG" | grep -q -- "cd $HOME" || { cat "$STUB_LOG"; return 1; }
}

test_open_ignores_linked_worktree_as_clone() {
  open_setup
  mkdir -p "$T/roots/zzz"; mv "$CLONE" "$T/roots/zzz/svc"; CLONE="$T/roots/zzz/svc"
  sed -i "s#^WORKTREE_DIR=.*#WORKTREE_DIR=\"$T/roots\"#" "$XDG_CONFIG_HOME/pr-comments/config"
  git -C "$CLONE" worktree add -q --detach "$T/roots/svc-pr-12" origin/main
  "$OPEN" my-org/svc 12
  [[ ! -e "$T/roots/svc-pr-12-pr-12" ]] || return 1
  [[ $(git -C "$T/roots/svc-pr-12" rev-parse HEAD) == $(git -C "$CLONE" rev-parse origin/feature/x) ]] || return 1
}

test_open_keeps_leftover_with_unpushed_commit() {
  open_setup
  mkdir -p "$WT"
  git -C "$CLONE" worktree add -q --detach "$WT/svc-pr-12" origin/feature/x
  git -C "$WT/svc-pr-12" commit -q --allow-empty -m local
  local c; c=$(git -C "$WT/svc-pr-12" rev-parse HEAD)
  "$OPEN" my-org/svc 12
  [[ $(git -C "$WT/svc-pr-12" rev-parse HEAD) == "$c" ]] || return 1
  grep '^gnome-terminal' "$STUB_LOG" | grep -q 'PR_COMMENTS_WORKTREE_STATE=kept-dirty' || { cat "$STUB_LOG"; return 1; }
}

test_open_pr_view_failure_notifies() {
  open_setup
  export GH_EXIT=1 GH_STDERR="could not resolve host"
  local rc=0
  "$OPEN" my-org/svc 12 2>/dev/null || rc=$?
  [[ $rc -ne 0 ]] || return 1
  [[ $(grep -c '^notify-send .*my-org/svc#12' "$STUB_LOG") == 1 ]] || { cat "$STUB_LOG"; return 1; }
  ! grep -q '^gnome-terminal' "$STUB_LOG" || return 1
}
