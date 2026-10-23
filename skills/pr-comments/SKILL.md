---
name: pr-comments
description: Walk through the unresolved human review feedback on one of my open PRs and help address it item by item. Use when invoked as "/pr-comments <pr-url>" (usually from a desktop notification opened by the pr-comments poller), when asked to "address PR comments" / "go through the review comments", and as "/pr-comments --fix-auth" to "fix gh auth for pr-comments" after the poller stopped on an auth error.
argument-hint: "<pr-url> | --fix-auth"
---

# pr-comments

Interactive session for one PR. The poller (`pr-comments-poll`, systemd user timer) found new human
feedback, and `pr-comments-open` created a temporary **detached** worktree at `origin/<branch>` and
started this session in it. Never touch the main clone's checkout or working tree.

**Hard rule:** nothing goes to GitHub (push, reply, review, resolve) without the user's explicit OK
in this session. Never resolve threads automatically.

## Mode: `--fix-auth`

1. Run `gh auth status`. If it is healthy, skip to step 3.
2. Tell the user to run the interactive step themselves (you cannot): `! gh auth login`, or
   `! gh auth refresh -h github.com` if the token only expired. Wait, then re-run `gh auth status`.
   Repeat until it passes.
3. Run `pr-comments-poll --resume` (starts the timer again and does one poll). Report the result and stop.

## Mode: `<pr-url>`

### 1. Orient

- If env `PR_COMMENTS_WORKTREE_STATE=kept-dirty` is set (`echo $PR_COMMENTS_WORKTREE_STATE`), say first:
  this worktree is a leftover from an earlier session with uncommitted changes or unpushed commits,
  it was kept as is. Show `git status` and `git log origin/<branch>..HEAD`
  before proposing anything.
- Branch: `gh pr view <url> --json headRefName,number,headRepository,baseRefName`. Push target is
  always `git push origin HEAD:<headRefName>` (the worktree is detached).
- Main clone: `git rev-parse --git-common-dir` -> its parent directory. Needed for cleanup.
- Your login: `gh api user --jq .login` (`$me`).

### 2. Collect the feedback

Same rules as the poller's `filter.jq` (installed at `~/.local/share/pr-comments/filter.jq`; query in
`query.graphql` next to it). Per-PR query with the fields needed to read whole threads:

```bash
gh api graphql -F owner=my-org -F name=my-repo -F number=123 -f query='
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){ pullRequest(number:$number){
    commits(last:1){nodes{commit{committedDate}}}
    reviewThreads(first:100){nodes{ id isResolved isOutdated path line
      comments(first:50){nodes{ id databaseId body createdAt url
        author{login __typename} }}}}
    comments(first:100){nodes{ id body createdAt url author{login __typename} }}
    reviews(first:100){nodes{ id body createdAt url author{login __typename} }}
  }}}'
```

An item is actionable when:
- **Human** author: not null (deleted user), `__typename` is not `Bot`, login does not end in `[bot]`,
  login is not `$me`, and not in `IGNORE_AUTHORS` of `${XDG_CONFIG_HOME:-~/.config}/pr-comments/config`.
- **Thread** (inline): `isResolved == false` and the thread's *last* comment is human. Id = that last comment's `id`.
- **Conversation comment**: human and `createdAt` newer than my last activity.
- **Review body**: human, body non-empty, and `createdAt` newer than my last activity.
- "My last activity" = max of my own comment/review `createdAt` and the last commit's `committedDate`.
- Drop ids already marked handled: `~/.local/state/pr-comments/` (honour `XDG_STATE_HOME`); the poller owns that
  file, only ever write it through `pr-comments-poll --mark-handled`.
- If a connection reports more than 100 nodes (`hasNextPage`), say so; you only see the first page.

If nothing is actionable, say so, go to step 5. Otherwise list the items grouped by file, then thread,
and tell the user how many there are.

### 3. One item at a time

For each item show: reviewer and verbatim text (whole thread for inline items), the current code at
`path:line` in this worktree (read it; flag `isOutdated`), a **proposed fix** and **why**. Questions or
"nit" remarks may need only a reply draft instead of a code change; say so.

Then ask with AskUserQuestion, options:
- **Yes** - apply the proposed fix in the worktree.
- **Modify** - user describes the change; apply that instead.
- **Skip** - leave it untouched; it stays actionable and will keep appearing.
- **Mark handled** - record locally that no action is needed: `pr-comments-poll --mark-handled <id>`
  (nothing is posted). Also run it for items you fixed, after the user confirms they are done.

Apply fixes with minimal, focused edits. Run the host repo's tests/linters as its CLAUDE.md says.

### 4. Wrap up

Summarise per item: fixed / modified / skipped / marked handled. Then offer, each separately and only
on explicit approval:
1. **Commit** - follow the host repo's CLAUDE.md and commit conventions; this skill imposes none. Stage explicit paths.
2. **Push** - `git push origin HEAD:<headRefName>`. Never force.
3. **Reply drafts** - write one per addressed item, show them **verbatim**, post only after the user OKs
   that exact text (they may edit). Inline thread: `gh api -X POST repos/<owner>/<repo>/pulls/<n>/comments/<databaseId>/replies -f body=...`
   (`databaseId` of the thread's last comment). Conversation: `gh pr comment <url> --body ...`.
   Do not resolve any thread; the reviewer or the user does that.

### 5. Cleanup

The worktree is temporary. Check `git status --porcelain` (clean?) and `git log origin/<headRefName>..HEAD`
(nothing unpushed? run `git fetch origin <branch>` first).
- Clean and pushed: tell the user the terminal's cwd is about to disappear, then
  `git -C <main clone> worktree remove <worktree path>` and `git -C <main clone> worktree prune`.
  If they prefer, they can `cd` out first.
- Otherwise: say what would be lost and ask before anything; never use `--force` without explicit approval.
  If they decline, leave it; the next `pr-comments-open` for this PR will report it as kept-dirty.

## Reference

- `pr-comments-poll --mark-handled ID...` | `--resume` | `--dry-run` | `--reset [--all]`
- Config `${XDG_CONFIG_HOME:-~/.config}/pr-comments/config`; state `${XDG_STATE_HOME:-~/.local/state}/pr-comments/`
