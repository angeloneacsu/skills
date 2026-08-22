# Paste into target repo's CLAUDE.md — fill every <slot>

## Epic-autopilot parameters

- **Main branch:** <master|main>
- **Issue tracker:** <bd | GitHub Issues | Jira> — commands: list ready `<cmd>`,
  show `<cmd>`, claim `<cmd>`, close `<cmd>`. Export file to commit (if file-based
  tracker): `<path | none>`.
- **Worktree root:** `<sibling dir, e.g. ../<repo>-worktrees/><issue-id>`, branch = issue id.
  Never under .claude/worktrees/, never loose next to other repos.
- **Per-worktree setup:** <e.g. symlink node_modules, env files | none>
- **Quality gates:** <exact commands: build, test, lint, contract/openapi gate>
- **Pre-existing red baseline:** <tests + tracking issue — check before debugging red gate>
- **Never-do:** <repo-specific: e.g. no bulk-format, no `git add -A`, ...>
- **Principles doc (fork adjudication authority):** <docs/PRINCIPLES.md>
- **Epic STATE lives in:** <tracker notes on the epic issue (e.g. `bd update <epic> --append-notes`,
  replace the marked `## STATE` block) | the handoff file>
- **Handoff path convention:** </tmp/handoff-<epic>.md | repo path> — pointer layer only, written
  by `follow-up`
- **Code review:** `mattpocock-skills:code-review`, fixed point `origin/<main>`, spec = the issue
  (fetched via the tracker; see `docs/agents/issue-tracker.md` if `setup-matt-pocock-skills` ran)
- **Merge-driver files (never hand-resolve first):** <e.g. .beads/issues.jsonl | none>
- **Sub-agent models (epic-autopilot-subagents):** reviewer `fable` (fixed); builder default
  `opus`, per-issue label `model:sonnet|opus`, run arg `builder=opus|sonnet|auto`
- **Mandated close artifacts:** <label → doc the close-reason must cite | none>
- **Loop protocol:** <stop-after-each-bead (user compacts + re-issues forward command)
  | self-compact at ~<N>k output tokens>
- **Unrelated-bug policy:** file issue immediately with clean-base proof; inline fix
  only if hard dependency AND tiny.

## Concurrency-safe merge to <main> (multiple agents, dirty main tree)

When finishing a worktree branch while the MAIN working tree may be dirty
(another agent's uncommitted/staged changes), NEVER merge via the main tree.
Detached temp worktree instead:

1. In your worktree: `git fetch origin && git merge origin/<main> --no-edit`.
   Repeat until `git merge-base --is-ancestor origin/<main> HEAD` is true
   (guarantees conflict-free final merge).
2. `git worktree add --detach /tmp/<name>-merge origin/<main>`
   (detach at ref allowed even though <main> is checked out in main tree —
   you check out the commit, not the branch).
3. `git -C /tmp/<name>-merge merge --no-ff <branch> -m "Merge <branch>"`
4. `git -C /tmp/<name>-merge push origin HEAD:<main>` — atomic; rejected because
   origin advanced → back to step 1, retry.
5. `git worktree remove /tmp/<name>-merge --force && git worktree prune`;
   `git push origin --delete <branch>`.

Never touches main working tree or another agent's staged files. Their local <main>
fast-forwards on next `git pull --rebase`. Handle autonomously — don't ask.

**Shell gotcha:** after `git worktree remove` / `cd` in compound commands, shell cwd
can silently land in the MAIN tree (stale <main>) — cd back into your worktree before
any relative-path command; never run builds/greps from the main tree.
