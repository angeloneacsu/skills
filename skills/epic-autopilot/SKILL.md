---
name: epic-autopilot
description: Use when told to drive a multi-issue epic to completion, one clean issue per iteration, with a rolling handoff so a fresh agent (or post-compact self) resumes until the epic is done. Triggers on "run the epic loop on <epic>", "continuă epicul <id>", "/epic-autopilot <epic>".
---

# epic-autopilot

Outer loop driving an epic to full completion. Composes, does not replace, the inner
skills: fork adjudication (grill-yourself/mantras vs the repo's principles doc),
handoff regeneration.

**Project parameters come from the repo's CLAUDE.md** (## Epic-autopilot parameters
block): issue tracker commands, worktree root, quality gates, never-do list,
principles doc path, handoff path convention, stop-vs-self-compact protocol.
Do not hardcode any of these here.

## Mental model
User kicks off ONCE. After that the loop continues only because of:
1. Per-iteration discipline — each issue finished fully to pushed-on-main; repo never
   left half-done when context dies.
2. Rolling handoff — ONE durable file (path per CLAUDE.md) always holding: main tip,
   shipped log, the SINGLE next issue + contract + carried gotchas.
3. Forward command — literal re-entry command written by you, executed by fresh-you:
   minimal + pointer-based (issue id, one-line scope, handoff path, relevant memories).
   Fat command duplicating the handoff = drift.

Over-document the NEXT step, not history.

## Iteration (top to bottom, every issue)
1. **Orient.** `git fetch origin`. Read handoff. Inspect epic in tracker (show epic,
   list ready/unblocked). Pick next unblocked child — dependency-respecting, ONE only.
   Assume other agents moved main since the handoff was written — re-verify, don't trust.
2. **Isolate + claim.** Worktree off FRESH origin/<main> at the repo's worktree root,
   branch = issue id. Claim in tracker. Reproduce per-worktree setup CLAUDE.md lists.
3. **Build.** Scope against real contracts in source, not handoff summaries
   (verify-don't-trust). Every technical fork → adjudicate against the principles doc;
   decide autonomously; escalate only product/business calls or principle conflicts.
   TDD where repo does.
4. **Gate.** Run the repo's real quality gates; read output honestly. Know the
   pre-existing red baseline (CLAUDE.md slot) — don't chase ghosts, never mask failure.
   Unrelated bug found → file issue IMMEDIATELY with clean-base proof; fix inline only
   if hard dependency AND tiny.
5. **Land (concurrency-safe).** Other agents move main. NEVER merge via the main tree
   or touch another agent's worktree. Use the detached-temp-worktree merge recipe from
   CLAUDE.md: merge origin/<main> into branch until ancestor-clean → detached temp
   worktree at origin/<main> → merge --no-ff branch → push origin HEAD:<main> →
   rejected = refetch + retry → remove temp worktree. Stage explicit paths only.
6. **Record.** Close issue in tracker; commit/push tracker export if file-based.
   Durable facts → handoff (agent-local memory does not travel). Remove issue
   worktree + branch.
7. **Refresh handoff.** New main tip, one "shipped" line (id, one-liner, merge commit),
   rewrite NEXT to exactly the next issue: contract, gotchas, verify-don't-trust
   caveats, forward command. Written for a context-free agent: lead with the command,
   reference (don't duplicate) design docs/commits.
8. **Continue per protocol** (CLAUDE.md slot — pick one):
   - **stop-after-each-bead**: refresh handoff, STOP, wait for user to compact +
     re-issue the forward command.
   - **self-compact at ~N tokens**: under threshold → loop to step 1; at threshold →
     finalize handoff, self-compact passing forward command as the compact argument.

## Forward command shape
> continue with <issue-id> (<one-line scope>, <surface/route>) — see <handoff-path>;
> use <skills/memories>

## Stop condition
Every child closed → final handoff marked complete (no NEXT), report to user.
Never silently chain into another epic.

## Break the loop and ask when
- Fork is genuine product/business call, or principles irreconcilably conflict. Bundle escalations.
- Gate failure implies the issue's PREMISE is wrong (contract mismatch, missing
  backend) → file blocker issue, surface, don't fake-pass.
- All remaining children blocked → report, don't spin.
