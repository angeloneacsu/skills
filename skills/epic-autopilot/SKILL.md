---
name: epic-autopilot
description: Use when told to drive a multi-issue epic to completion, one clean issue per iteration, with a rolling handoff so a fresh agent (or post-compact self) resumes until every child is closed. The unattended outer loop ("continue implementing <epic> until it's all done"); composes mattpocock-skills (tdd, code-review, diagnosing-bugs, resolving-merge-conflicts, to-tickets) inside each iteration and `follow-up` for the forward note.
argument-hint: "<epic-id> [handoff-path] [skills to use per issue]"
---

# epic-autopilot

Outer loop driving an epic to full completion. Composes, does not replace, the inner
skills: fork adjudication (grill-yourself/mantras vs the repo's principles doc),
`mattpocock-skills:tdd` / `code-review` / `diagnosing-bugs` / `resolving-merge-conflicts` /
`to-tickets` inside an iteration, `follow-up` for the forward note.

**Position in the mattpocock flow:** `grill-with-docs → to-spec → to-tickets` (epic + children
with blocking edges) **→ epic-autopilot**. This loop is the unattended form of Matt's
"`/implement` per ticket, `/clear` between": the tracker is the memory, the loop is the agent.

**Project parameters come from the repo's CLAUDE.md** (## Epic-autopilot parameters
block): issue tracker commands, worktree root, quality gates, never-do list,
principles doc path, where the epic's STATE lives, handoff path convention,
stop-vs-self-compact protocol. Do not hardcode any of these here.

## Mental model
User kicks off ONCE. After that the loop continues only because of:
1. Per-iteration discipline — each issue finished fully to pushed-on-main; repo never
   left half-done when context dies.
2. Durable state — the epic's **STATE block** (notes on the epic issue when the tracker
   supports notes, else the rolling handoff file; CLAUDE.md slot) always holding: main tip,
   shipped log, the SINGLE next issue + contract + carried gotchas. Replace the marked
   block each iteration — no accumulation.
3. Forward command — literal re-entry command written by you, executed by fresh-you:
   minimal + pointer-based (issue id, one-line scope, handoff path, relevant memories),
   produced by `follow-up`. Fat command duplicating the STATE = drift.

Over-document the NEXT step, not history.

## Iteration (top to bottom, every issue)
1. **Orient.** `git fetch origin`. Read the epic's STATE / handoff. Inspect epic in tracker
   (show epic, list ready/unblocked). Pick next unblocked child — dependency-respecting,
   ONE only. Assume other agents moved main since STATE was written — re-verify, don't trust.
2. **Isolate + claim.** Worktree off FRESH origin/<main> at the repo's worktree root,
   branch = issue id. Claim in tracker. Reproduce per-worktree setup CLAUDE.md lists.
3. **Build.** Scope against real contracts in source, not STATE summaries
   (verify-don't-trust). Every technical fork → adjudicate against the principles doc;
   decide autonomously; escalate only product/business calls or principle conflicts.
   Build test-first with **`mattpocock-skills:tdd`** at pre-agreed seams — unattended there is
   no user to agree them with, so the seams are the issue's design anchors + acceptance
   criteria; write them onto the issue before the first red test. A gate red on a *real* bug
   (not a missing feature) → **`mattpocock-skills:diagnosing-bugs`** (tight red loop, fix,
   regression test) before any escalation count starts.
4. **Gate.** Run the repo's real quality gates; read output honestly. Know the
   pre-existing red baseline (CLAUDE.md slot) — don't chase ghosts, never mask failure.
   Gates green → **`mattpocock-skills:code-review`**, fixed point `origin/<main>`, spec = the
   issue (fetched via the tracker); read Standards and Spec as separate axes, fix hard
   violations and missing/creeping requirements before landing; smells are judgement calls.
   Refactoring belongs here, not inside the red→green loop.
   Unrelated bug found → file issue IMMEDIATELY with clean-base proof; fix inline only
   if hard dependency AND tiny.
5. **Land (concurrency-safe).** Other agents move main. NEVER merge via the main tree
   or touch another agent's worktree. Use the detached-temp-worktree merge recipe from
   CLAUDE.md: merge origin/<main> into branch until ancestor-clean → detached temp
   worktree at origin/<main> → merge --no-ff branch → push origin HEAD:<main> →
   rejected = refetch + retry → remove temp worktree. Stage explicit paths only.
   Conflict → **`mattpocock-skills:resolving-merge-conflicts`** (by intent, never `--abort`),
   except files the repo hands to a merge driver (CLAUDE.md slot, e.g. a tracker export).
6. **Record.** Close issue in tracker; commit/push tracker export if file-based.
   Durable facts → the epic's STATE (agent-local memory does not travel). Remove issue
   worktree + branch.
7. **Refresh STATE, then the pointer.** New main tip, one "shipped" line (id, one-liner,
   merge commit), NEXT = exactly the next issue: contract, gotchas, verify-don't-trust
   caveats — into the epic's STATE block (replace the marked block). Then rewrite the
   handoff file as the pointer layer with **`follow-up`** (≤ 15 lines, "show <next>" first).
   Written for a context-free agent: lead with the command, reference (don't duplicate)
   design docs/commits/issues.
8. **Continue per protocol** (CLAUDE.md slot — pick one):
   - **stop-after-each-issue**: refresh STATE + follow-up, STOP, wait for user to compact +
     re-issue the forward command.
   - **self-compact at ~N tokens**: under threshold → loop to step 1; at threshold →
     `follow-up`, self-compact passing the forward command as the compact argument.

## Forward command shape
> continue with <issue-id> (<one-line scope>, <surface/route>) — see <handoff-path>;
> use <skills/memories>

## Oversized-issue rule — annotate & defer, don't drain the session
An issue that proves to be several: the moment you sense it (Orient, or the first real
read — not after 80k tokens), annotate it self-sufficient on the tracker (anchors, sub-parts,
verified contracts, each fork with its principle-adjudicated leaning, verify recipe); if it is
honestly N issues, split it with **`mattpocock-skills:to-tickets`** run on the issue
(tracer-bullet children, blockers first, native blocking edges, a design block per child).
Then pick a smaller unblocked issue, or hand the big one off untouched via STATE + `follow-up`
and self-compact. Never start-and-abandon across a compaction.

## Stop condition
Every child closed → final STATE marked complete (no NEXT), report to user.
Never silently chain into another epic.

## Break the loop and ask when
- Fork is genuine product/business call, or principles irreconcilably conflict. Bundle escalations.
- Gate failure implies the issue's PREMISE is wrong (contract mismatch, missing
  backend) → file blocker issue, surface, don't fake-pass.
- All remaining children blocked → report, don't spin.
