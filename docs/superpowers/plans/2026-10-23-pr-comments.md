# pr-comments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A systemd user timer polls GitHub every 5 min for new human comments on my open PRs, shows one desktop notification per PR, and a click opens Claude Code in a temporary worktree running the `pr-comments` skill.

**Architecture:** Small bash scripts under `skills/pr-comments/scripts/` sharing one `lib.sh` (config + state paths). A single jq program turns one GraphQL response into the list of actionable comments; the poller diffs that list against local state and spawns a detached notifier per PR (`systemd-run --user`) whose click runs the opener. The installer copies scripts to `~/.local/bin` and writes the units. The skill (`SKILL.md`) drives the interactive fix session and the cleanup.

**Tech Stack:** bash, `gh` (GraphQL via `gh api graphql`), `jq`, `notify-send` (libnotify 0.8), `git worktree`, systemd user units. Tests: plain bash (`tests/run.sh`) with stub binaries on `PATH` — no bats/shellcheck (not installed; no new deps).

**Spec:** `docs/superpowers/specs/2026-10-23-pr-comments-design.md`

## Global Constraints

- No client-specific values (org names, paths, logins) anywhere in the repo; examples use `my-org`, `~/src`.
- Nothing is posted/pushed to GitHub except from the skill after explicit user approval.
- The main clone's checkout/working tree is never modified (only `git fetch`, `git worktree add/remove/prune`).
- Config: `${XDG_CONFIG_HOME:-~/.config}/pr-comments/config`; state: `${XDG_STATE_HOME:-~/.local/state}/pr-comments/`.
- Defaults: interval 5 min, Mon–Fri 08:00–19:00; `worktree_dir=~/work/temp`; `terminal="gnome-terminal --"`.
- Worktree path `<worktree_dir>/<clone-name>-pr-<number>`, always detached at `origin/<branch>`; push is `git push origin HEAD:<branch>`.
- First run / empty state notifies every actionable comment (one notification per PR).
- Auth error → one notification + `systemctl --user stop pr-comments.timer`; other errors → one notification, silent until a success.
- Commits: short `-m` message only, no body, no Co-Authored-By; stage paths explicitly.

## Review Focus

1. **Comment author is null** (deleted "ghost" user) → item skipped, no jq crash. Test in Task 1.
2. **PR head lives in a fork** (`headRepository` ≠ base repo) → `pr-comments-open` fails with a clear notification/terminal message instead of a wrong worktree. Test in Task 3.
3. **Branch names with `/`** (`feature/x`) → worktree path still `<clone>-pr-<n>`, fetch/checkout of `origin/feature/x` works. Test in Task 3.
4. **More than 100 open PRs / threads** → first page only (`first: 50` PRs, `first: 100` threads/comments/reviews); poller logs a warning to stderr when `pageInfo.hasNextPage` is true rather than silently dropping. Test in Task 1 (fixture with `hasNextPage: true` → warning).
5. **Network down vs. auth expired** → network failure must not stop the timer. Test in Task 2 (stub `gh` exits 1 with "could not resolve host" → no `systemctl stop`).

---

### Task 1: Comment filter (jq) + shared lib

**Files:**
- Create: `skills/pr-comments/scripts/lib.sh`
- Create: `skills/pr-comments/scripts/filter.jq`
- Create: `skills/pr-comments/scripts/query.graphql`
- Create: `skills/pr-comments/config.example`
- Create: `skills/pr-comments/tests/run.sh`, `skills/pr-comments/tests/test_filter.sh`, `skills/pr-comments/tests/fixtures/search.json`

**Interfaces:**
- Produces:
  - `lib.sh` (sourced): `pc_load_config` → sets `ORGS` (array), `CLONE_ROOTS` (array), `CLONE_DIR`, `WORKTREE_DIR`, `IGNORE_AUTHORS` (array), `TERMINAL` (string), `INTERVAL_MIN`, `ACTIVE_DAYS`, `ACTIVE_HOURS`; returns 2 with stderr message when config missing. `pc_state_dir` → echoes and creates state dir. Constants `PC_SEEN=$(pc_state_dir)/seen`, `PC_HANDLED=…/handled`, `PC_ERROR=…/error-notified`, `PC_AUTH=…/auth-paused` (one node ID per line in seen/handled).
  - Config format: bash-sourceable file (`ORGS=(my-org)` etc.), defaults applied for unset keys.
  - `filter.jq`, invoked as `jq -c --arg me LOGIN --argjson ignore '["x"]' --slurpfile handled <(…) -f filter.jq` on the GraphQL response; emits one JSON object per line: `{id, kind: "thread"|"comment"|"review", repo: "owner/name", number, title, url, author, createdAt}`.
  - Query: `search(query: "is:pr is:open author:@me org:<org>", type: ISSUE, first: 50)` with per PR: `number title url repository{nameWithOwner} headRefName headRepository{nameWithOwner} commits(last:1){nodes{commit{committedDate}}} reviewThreads(first:100){nodes{isResolved comments(last:1){nodes{id author{login __typename} createdAt url}}}} comments(first:100){nodes{id author{login __typename} createdAt url}} reviews(first:100){nodes{id author{login __typename} body createdAt url}}` + `pageInfo{hasNextPage}` at each level.

Filter rules (from spec): human = author non-null, `__typename != "Bot"`, login not ending `[bot]`, login ≠ `$me`, not in `$ignore`. Threads: `isResolved == false` and last comment by a human → item with the **last comment's** id (so a new reply = new id = new notification). Conversation comments and non-empty review bodies: only if `createdAt` > my last activity on that PR (max of my comments/reviews createdAt and last commit date). Anything whose id is in `handled` is dropped.

- [ ] **Step 1: Write fixture + failing tests** in `tests/test_filter.sh` (each test is a function, `run.sh` sources all `test_*.sh` and runs functions named `test_*`, printing PASS/FAIL, exit 1 on any FAIL). Fixture: one PR `my-org/svc#12` with: unresolved thread last-commented by `andi` (id `T1`), resolved thread (`T2`), unresolved thread last-commented by me (`T3`), thread by `greptile-apps[bot]` (`T4`), thread by `__typename: Bot` login `sonar` (`T5`), comment by null author (`C0`), conversation comment by `andi` after my last commit (`C1`), conversation comment by `andi` before my last commit (`C2`), review body `"pls fix"` by `andi` after (`R1`), review with empty body (`R2`); a second page flag `hasNextPage: true` on reviewThreads.
  - `test_filter_humans_unresolved`: ids == `T1 C1 R1` (in that order).
  - `test_filter_ignore_list`: `--argjson ignore '["andi"]'` → empty.
  - `test_filter_handled`: handled contains `C1` → ids == `T1 R1`.
  - `test_filter_item_shape`: item `T1` has `repo=="my-org/svc"`, `number==12`, `kind=="thread"`, `author=="andi"`.
- [ ] **Step 2: Run** `bash skills/pr-comments/tests/run.sh` → FAIL (filter.jq missing).
- [ ] **Step 3: Implement** `filter.jq`, `query.graphql`, `lib.sh`, `config.example` (every key documented with a comment and placeholder value).
- [ ] **Step 4: Run** `bash skills/pr-comments/tests/run.sh` → all PASS.
- [ ] **Step 5: Commit** `git add` each file; `git commit -m "pr-comments: comment filter and config lib"`.

### Task 2: Poller + notifier

**Files:**
- Create: `skills/pr-comments/scripts/pr-comments-poll`, `skills/pr-comments/scripts/pr-comments-notify`
- Create: `skills/pr-comments/tests/test_poll.sh`, `skills/pr-comments/tests/stubs/{gh,notify-send,systemd-run,systemctl}`

**Interfaces:**
- Consumes: Task 1 `lib.sh`, `filter.jq`, `query.graphql`.
- Produces:
  - `pr-comments-poll [--dry-run | --reset [--all] | --resume | --mark-handled ID...]`. Normal run: `gh api user -q .login`, one `gh api graphql` per org, filter, items whose id ∉ seen → group by `repo#number` → per PR one `systemd-run --user --collect --quiet pr-comments-notify <repo> <number> <url> <summary>`; then append the notified ids to seen. `--dry-run`: print `repo#number author kind url` lines, no notify, no state write. `--reset`: truncate seen (and handled with `--all`). `--resume`: remove auth flag, `systemctl --user start pr-comments.timer`, then run once. `--mark-handled`: append ids to handled. Exit 0 on success.
  - Summary text: `"<n> new comment(s) from <authors comma-joined> — <title>"`.
  - `pr-comments-notify <repo> <number> <url> <summary>` / `pr-comments-notify --auth <message>` / `pr-comments-notify --error <message>`: `notify-send --app-name=pr-comments -A default=Open --wait …`; on `default` output exec `pr-comments-open <repo> <number>` (or `pr-comments-open --fix-auth`). Error variant has no action.
  - Errors: if `gh` stderr/exit indicates auth (`gh auth status` non-zero, or message matching `401|Bad credentials|not logged|authentication`) → if `PC_AUTH` absent: create it, notify `--auth`, `systemctl --user stop pr-comments.timer`; exit 3. While `PC_AUTH` exists a normal run exits 3 immediately without calling gh. Other failures → if `PC_ERROR` absent: create it, notify `--error`; exit 4. Any successful run removes `PC_ERROR`.

Stubs record their argv to `$STUB_LOG`; `gh` stub returns `$GH_FIXTURE` for graphql, `$GH_LOGIN` for user, and exits with `$GH_EXIT`/`$GH_STDERR` when set. Tests run with `HOME`, `XDG_*` pointed at a `mktemp -d`.

- [ ] **Step 1: Write failing tests** in `tests/test_poll.sh`:
  - `test_poll_first_run_wave`: empty state → exactly one `systemd-run` call for `my-org/svc 12`, summary starts with `"3 new comment(s) from andi"`; seen contains `T1 C1 R1`.
  - `test_poll_no_repeat`: second run with same fixture → zero `systemd-run` calls.
  - `test_poll_new_reply_notifies`: fixture with T1's last comment id changed to `T1b` → one call, summary `"1 new comment(s)…"`.
  - `test_poll_reset_wave_again`: after a run, `--reset` then run → one call again; handled file untouched; `--reset --all` empties handled.
  - `test_poll_dry_run`: prints 3 lines, no `systemd-run`, seen file unchanged.
  - `test_poll_auth_stops_timer`: `GH_EXIT=1 GH_STDERR="HTTP 401: Bad credentials"` → exit 3, one `pr-comments-notify --auth` spawn, `systemctl --user stop pr-comments.timer` logged; second run → exit 3, no gh call, no new notify.
  - `test_poll_network_error_no_stop` (Review Focus 5): `GH_STDERR="could not resolve host"` → exit 4, one `--error` notify, no `systemctl`; second failing run → no new notify; successful run removes error flag.
  - `test_poll_resume`: with auth flag present, `--resume` → flag gone, `systemctl --user start pr-comments.timer` logged, poll ran.
  - `test_poll_pagination_warning` (Review Focus 4): stderr contains `hasNextPage` warning naming the PR.
- [ ] **Step 2: Run** `bash skills/pr-comments/tests/run.sh` → new tests FAIL.
- [ ] **Step 3: Implement** both scripts (`set -euo pipefail`, source `lib.sh` from the script's own dir, falling back to `~/.local/share/pr-comments/lib.sh` once installed — see Task 4).
- [ ] **Step 4: Run** tests → all PASS.
- [ ] **Step 5: Commit** `git commit -m "pr-comments: poller and notifier"`.

### Task 3: Click handler (`pr-comments-open`)

**Files:**
- Create: `skills/pr-comments/scripts/pr-comments-open`
- Create: `skills/pr-comments/tests/test_open.sh`, stub `tests/stubs/gnome-terminal`

**Interfaces:**
- Consumes: Task 1 `lib.sh`.
- Produces: `pr-comments-open <owner/repo> <number>` and `pr-comments-open --fix-auth`.
  1. `gh pr view <number> -R <repo> --json headRefName,headRepository,url` ; fork head (headRepository ≠ repo) → print error, `pr-comments-notify --error "PR from fork not supported: …"`, exit 5.
  2. Clone lookup: first dir among `CLONE_ROOTS/*/` (depth 1 and 2) whose `git remote get-url origin` normalises (strip `git@github.com:`/`https://github.com/`, `.git`) to `<repo>`; none → `gh repo clone <repo> "$CLONE_DIR/<name>"`.
  3. `git -C <clone> fetch origin <branch>`; path `$WORKTREE_DIR/<clone-basename>-pr-<number>`. If path exists: clean (`git status --porcelain` empty) and `git rev-list origin/<branch>..HEAD` empty → `git worktree remove --force` + recreate; else keep and export `PR_COMMENTS_WORKTREE_STATE=kept-dirty`. Create with `git -C <clone> worktree add --detach <path> origin/<branch>`.
  4. `$TERMINAL bash -lc 'cd <path> && claude "/pr-comments <url>"'` (`--fix-auth`: cd `$HOME`, `claude "/pr-comments --fix-auth"`).

Tests use a real local bare repo as `origin` (`git init --bare`, push `main` and `feature/x`), a clone under a temp `CLONE_ROOTS`, and stubbed `gh` (`pr view` JSON) + `gnome-terminal` (records argv).

- [ ] **Step 1: Write failing tests**:
  - `test_open_creates_detached_worktree` (Review Focus 3): branch `feature/x` → `$WORKTREE_DIR/svc-pr-12` exists, `git -C … rev-parse HEAD` == `origin/feature/x`, `git symbolic-ref -q HEAD` fails (detached); main clone `git status --porcelain` and current branch unchanged (make main clone dirty + on `feature/x` beforehand).
  - `test_open_launches_terminal`: terminal stub argv contains `cd $WORKTREE_DIR/svc-pr-12` and `/pr-comments https://github.com/my-org/svc/pull/12`.
  - `test_open_recreates_clean_leftover`: existing clean worktree at old commit → after run, HEAD == new `origin/feature/x`.
  - `test_open_keeps_dirty_leftover`: leftover with untracked file → file still there, terminal command carries `PR_COMMENTS_WORKTREE_STATE=kept-dirty`.
  - `test_open_clones_missing_repo`: no matching clone → `gh repo clone my-org/svc $CLONE_DIR/svc` logged.
  - `test_open_fork_rejected` (Review Focus 2): fork head → exit 5, no worktree, `--error` notify logged.
  - `test_open_fix_auth`: terminal argv contains `/pr-comments --fix-auth`.
- [ ] **Step 2: Run** tests → FAIL.
- [ ] **Step 3: Implement** `pr-comments-open`.
- [ ] **Step 4: Run** tests → all PASS.
- [ ] **Step 5: Commit** `git commit -m "pr-comments: open PR in temp worktree"`.

### Task 4: Installer + systemd units

**Files:**
- Create: `skills/pr-comments/scripts/pr-comments-install`, `skills/pr-comments/systemd/pr-comments.service`, `skills/pr-comments/systemd/pr-comments.timer`
- Create: `skills/pr-comments/tests/test_install.sh`

**Interfaces:**
- Consumes: all scripts from Tasks 1–3.
- Produces: `pr-comments-install [--uninstall]`: copies `pr-comments-{poll,notify,open}` to `~/.local/bin/` (mode 755) and `lib.sh filter.jq query.graphql` to `~/.local/share/pr-comments/`; copies `config.example` to the config path only if absent (then prints "edit <path>"); renders units into `${XDG_CONFIG_HOME:-~/.config}/systemd/user/` with `OnCalendar=${ACTIVE_DAYS} *-*-* ${ACTIVE_HOURS}:00/${INTERVAL_MIN}:00` (defaults `Mon..Fri`, `08..18`, `5` — last run 18:55, matching "until 19:00"), `Persistent=false`; service `Type=oneshot`, `ExecStart=%h/.local/bin/pr-comments-poll`; then `systemctl --user daemon-reload` and `enable --now pr-comments.timer`. `--uninstall` disables timer and removes installed files, keeps config and state.

- [ ] **Step 1: Write failing tests** (HOME=temp, stub `systemctl`): `test_install_files` (bins executable, lib under share), `test_install_timer_calendar` (timer contains `OnCalendar=Mon..Fri *-*-* 08..18:00/5:00`), `test_install_keeps_existing_config` (pre-existing config byte-identical after install), `test_install_enables_timer` (stub log has `--user enable --now pr-comments.timer`), `test_uninstall` (bins gone, config present).
- [ ] **Step 2: Run** tests → FAIL.
- [ ] **Step 3: Implement** installer and unit templates.
- [ ] **Step 4: Run** tests → all PASS. Also `systemd-analyze calendar 'Mon..Fri *-*-* 08..18:00/5:00'` → exit 0.
- [ ] **Step 5: Commit** `git commit -m "pr-comments: installer and systemd units"`.

### Task 5: Skill + docs

**Files:**
- Create: `skills/pr-comments/SKILL.md`
- Modify: `README.md` (Skills table row + short "pr-comments setup" section), `.claude-plugin/plugin.json` (description lists `pr-comments`, version `0.5.1` → `0.6.0`)

**Interfaces:**
- Consumes: `pr-comments-poll --mark-handled`, `--resume`; env `PR_COMMENTS_WORKTREE_STATE`.
- Produces: skill `pr-comments`, `argument-hint: "<pr-url> | --fix-auth"`.

SKILL.md content (frontmatter `name: pr-comments`, description triggering on "address PR comments", "/pr-comments <url>", "fix gh auth for pr-comments"):
1. If `PR_COMMENTS_WORKTREE_STATE=kept-dirty` → say so first.
2. Fetch threads (GraphQL, unresolved, human), conversation comments and review bodies newer than my last activity (same rules as `filter.jq`; may reuse `~/.local/share/pr-comments/query.graphql` + `filter.jq`).
3. Per item: reviewer text, current code, proposed fix, why → AskUserQuestion Yes / Modify / Skip / Mark handled (`pr-comments-poll --mark-handled <id>`).
4. Summary; commit, `git push origin HEAD:<branch>`, and reply drafts — each only on explicit approval; replies shown verbatim before posting. Follow the host repo's CLAUDE.md for commits.
5. Cleanup: clean and pushed → `git -C <main clone> worktree remove <path> && git worktree prune`; otherwise ask first.
6. `--fix-auth` mode: `gh auth status`; tell user to run `! gh auth login` (or `! gh auth refresh`); re-check; on success `pr-comments-poll --resume`.

- [ ] **Step 1: Write** SKILL.md, README row/section, plugin.json bump.
- [ ] **Step 2: Verify** `jq . .claude-plugin/plugin.json` exits 0; `bash skills/pr-comments/tests/run.sh` all PASS; `grep -rniE 'local-ch|localsearch|angelo' skills/pr-comments` → no matches.
- [ ] **Step 3: Commit** `git commit -m "pr-comments: skill and docs"`.

### Task 6: Local setup on this machine (not committed)

- [ ] **Step 1:** `bash skills/pr-comments/scripts/pr-comments-install`; edit `~/.config/pr-comments/config` with `ORGS=(local-ch)`, `CLONE_ROOTS=(~/work/foundation ~/work/devops)`, `CLONE_DIR=~/work/foundation`, `WORKTREE_DIR=~/work/temp`.
- [ ] **Step 2:** `pr-comments-poll --dry-run` → lists current human comments (or nothing) without error.
- [ ] **Step 3:** `pr-comments-poll --reset && pr-comments-poll` → wave of pop-ups; click one → terminal opens in `~/work/temp/<repo>-pr-<n>` with the skill running.
- [ ] **Step 4:** `systemctl --user list-timers pr-comments.timer` → next run within 5 min (during active hours).
