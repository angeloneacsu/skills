# pr-comments — desktop notifications for new PR review comments, one click to address them

## Intent

Human review comments on my own PRs get missed. GitHub email notifications drown;
the failure mode is a colleague asking "did you see my comments?" days later.
Goal: a desktop pop-up shortly after a human comments on one of my open PRs, and
clicking it opens a ready Claude Code session that walks me through the unresolved
comments and helps address them.

The repo is public, so everything committed is generic. Anything specific to the
client/employer I currently work for (GitHub org, local clone layout, extra bots)
lives only in a local config file on the machine where it runs.

## Goals

- Poll GitHub periodically (default every 5 min, Mon–Fri 08:00–19:00, local time)
  for **new human comments on open PRs authored by me** in the configured org(s).
- Show one desktop notification per PR with new activity: who, which PR (repo#number
  + title), how many new comments.
- Clicking the notification opens a terminal running Claude Code in a **temporary git
  worktree** under `~/work/temp/` checked out from `origin/<branch>`, with the new skill
  invoked for that PR. The worktree is removed at the end of the session.
- The skill lists every **unresolved** piece of human feedback and handles them one
  at a time: show comment, show code, propose fix, user approves / modifies / skips.
- Never notify twice for the same comment. A new reply in an existing thread is new
  activity and notifies again.

## What counts as a "comment"

All three GitHub kinds, from humans only:
1. Inline review comments (review threads on code lines) — resolution state taken
   from GraphQL `reviewThreads { isResolved }`; resolved threads are ignored.
2. General PR conversation comments (issue comments on the PR).
3. Review bodies (the text submitted with Approve / Request changes / Comment),
   when non-empty.

Kinds 2 and 3 have no "resolved" state. The skill treats them as needing attention if
they are newer than my last comment/commit on the PR, and lets the user mark them
handled during the session (marking = recorded in local state, nothing posted).

"Human" = author is not my own login, author type is not `Bot` / login does not end in
`[bot]`, and login is not in the config's `ignore_authors` list.

## Non-goals

- PRs where I am only a reviewer / review requested (maybe later).
- Closed or merged PRs.
- Bot comments (Greptile, Copilot, Sonar, Dependabot, …).
- Fixing the team's `lcl-review-fix` skill (it lives in a separate team repo; a separate
  follow-up PR there can port the GraphQL / general-comment fixes).
- Any platform other than Linux with a freedesktop notification daemon + systemd user
  units (my machine: Fedora 42, GNOME 48).

## Anti-goals (would be a failure even if it "works")

- **Posting anything to GitHub without my explicit OK** — no auto-replies, no
  auto-resolving threads, no pushes without approval in the session.
- Touching the working tree of my main clone (switching branches, stashing, etc.).
- Spamming: repeat notifications for already-seen comments, or notifications outside
  the configured hours.
- Client-specific values (org names, paths, logins) committed to the public repo.
- Silent failure: if `gh` is not authenticated or the API fails, I must find out
  (one notification about the error, not one every 5 minutes).

## Components (in `angeloneacsu/skills`)

```
skills/pr-comments/
  SKILL.md                    # the Claude skill (addresses comments for one PR)
  bin/pr-comments-poll        # poller: gh API -> diff vs state -> notify-send
  bin/pr-comments-open        # click handler: locate/clone repo, worktree, launch terminal+claude
  bin/pr-comments-install     # installs systemd user units + example config
  systemd/pr-comments.service
  systemd/pr-comments.timer
  config.example              # documented example, no real values
```

Plugin metadata (`.claude-plugin/plugin.json` description, version bump) and README
get updated to list the new skill.

### Local config — `~/.config/pr-comments/config` (never in the repo)

Keys (example values are mine, shown only to define shape):
- `orgs` — list, e.g. `local-ch`
- `clone_roots` — dirs searched for an existing clone, matched by `origin` URL
  (e.g. `~/work/foundation`, `~/work/devops`)
- `clone_dir` — where a missing repo gets cloned (e.g. `~/work/foundation`)
- `worktree_dir` — where temporary PR worktrees go; default `~/work/temp`, worktree
  path `<worktree_dir>/<clone-name>-pr-<number>`
- `ignore_authors` — extra logins to treat as bots
- `schedule` — interval + active days/hours
- `terminal` — command to open a terminal (default `gnome-terminal --`)

The poller and click handler read the same file.

*(builder's call)* Script location: units and notification actions must not point into
the plugin cache (versioned path, changes on update). `pr-comments-install` copies
`bin/*` to `~/.local/bin/` and writes the units referencing those copies; re-run the
installer after a plugin update to pick up new script versions. Missing config → install script
copies `config.example` and tells the user to edit it; poller exits with a clear error.

### Local state — `~/.local/state/pr-comments/`

IDs (GitHub node IDs) of comments already notified, plus per-PR "handled" marks set
from the skill.

**First run / empty state notifies everything** (decided by the user): every currently
unresolved/unhandled human comment on my open PRs produces notifications (one per PR),
even if that is a big first wave. This is how already-missed comments surface.

A reset must be a deliberate, supported action so the wave can be reproduced for
testing: `pr-comments-poll --reset` (clears the notified-IDs state; keeps "handled"
marks unless `--reset --all`), followed by a normal run, shows the full wave again.
`pr-comments-poll --dry-run` prints what would be notified without notifying or
writing state.

### Click → session flow

1. Notification has a default action; clicking it runs `pr-comments-open <owner/repo> <number>`.
2. Find a clone in `clone_roots` whose `origin` matches; if none, `gh repo clone` into `clone_dir`.
3. `git fetch origin <branch>`, then create a **temporary worktree, always detached at
   `origin/<branch>`** (decided by the user), at
   `<worktree_dir>/<clone-name>-pr-<number>` (default `~/work/temp/…`). Main clone's
   checkout and dirty state untouched; detached avoids the "branch already checked
   out" conflict entirely. Push is `git push origin HEAD:<branch>`, only on approval.
   - *(builder's call)* If that path already exists (leftover from an earlier session):
     clean and no unpushed commits → remove and recreate fresh; otherwise keep it as
     is and the skill states that up front before proposing any fix.
4. Open `terminal` in the worktree running `claude "/pr-comments <PR URL>"`
   (or equivalent invocation of the plugin skill).
5. **Cleanup at the end** (decided by the user): the skill's last step removes the
   worktree (`git worktree remove` + `git worktree prune` in the main clone).
   *(builder's call)* If it has uncommitted changes or unpushed commits, the skill
   asks before removing — never silently discards work.

### Auth / API errors (decided by the user)

- **Auth error** (`gh` not logged in, token expired/401): send **one** notification,
  then **stop the timer** (`systemctl --user stop pr-comments.timer`) — the poller
  does not run again until auth is fixed.
- Clicking that notification opens a terminal with Claude Code and the skill in
  auth-repair mode (`/pr-comments --fix-auth`): it runs `gh auth status`, guides the
  user through `gh auth login` / refresh (interactive steps run by the user), and on
  success restarts the timer (`pr-comments-poll --resume` = start timer + one run).
- *(builder's call)* Other API failures (network, 5xx, rate limit): no stop; one
  error notification, then silent until a run succeeds again.

### Skill behaviour (`/pr-comments <pr-url>`)

1. Fetch the three comment kinds (GraphQL for threads + resolution).
2. Filter to unresolved / unhandled human feedback; group by file and thread.
3. For each: show reviewer text, current code, proposed fix, why → user chooses
   Yes / Modify / Skip / Mark handled.
4. After all items: summarise; offer to commit, push, and draft replies — each only
   on explicit approval. Replies are shown before posting.
5. Respect the host repo's own rules (CLAUDE.md, commit conventions) — the skill does
   not impose its own.
6. Remove the temporary worktree (see cleanup above).

## Constraints

- Tools: bash, `gh` (already authenticated), `jq`, `notify-send` (libnotify 0.8,
  supports `--action`), `git worktree`, systemd user timer. No new runtime deps
  beyond these.
- `notify-send --action` blocks until the notification is closed; the poller must not
  hang the timer on it (detach per notification).
- Timer must work without an open Claude Code session and without a terminal.

## Left to the builder

- Exact GraphQL queries and how the "since last check" window is computed.
- Notification wording and grouping details beyond "one per PR".
- Poller language (bash+jq preferred for consistency; a single small script is fine).
- How the skill invocation is passed to `claude` (positional prompt vs flag).
