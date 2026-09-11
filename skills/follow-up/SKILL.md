---
name: follow-up
description: Write a quick, pointer-only follow-up note (text, Matt-style handoff) that sends the next agent to the tracker issues it needs — next issue id(s), the show commands, one-line scope, skills to use — and carries no state of its own; durable state stays on the tracker (the epic's STATE block). Use when ending a session or forking a side task inside the same repo ("write a follow-up", "quick handoff", "send it to the issues", "follow-up for <issue>"), and at the epic-autopilot self-compact step. For a portable, stateful handoff to another directory / harness / colleague use `mattpocock-skills:handoff` instead, or `project-handoff` to keep a full handoff in the project's `.handoffs/`.
argument-hint: "<next-issue-id(s)> [path] [skills]"
---

# Follow-up

A follow-up is a **pointer, not a store**. The tracker holds the state (issue body, notes, the
epic's STATE block); the note only tells the next agent *where to look first* and *what to run*.
If you feel the urge to explain something in the note, put it on the issue instead and point
at it. Tracker commands come from the repo's CLAUDE.md (`## Epic-autopilot parameters`);
`bd` is used below as the example.

## Steps

1. **Pick the target.** From the arguments, or the tracker's ready list scoped to the epic
   (`bd ready --parent <epic>`): lowest, dependency-respecting, one issue. Done when: one next
   issue id, plus the ordered ids (or the ready query) after it.
2. **Make the issue self-sufficient first.** Show it: design block with anchors (`file:line`),
   principle / design-doc refs, acceptance, verify recipe. Missing pieces go **onto the issue
   now** (`bd update <id> --append-notes`); if the epic's STATE block is stale, replace the
   marked block. Done when: a context-free agent could work the issue from "show" alone —
   the note never compensates for a thin issue.
3. **Write the note** from the template, ≤ 15 lines. Path: the one passed as argument; else
   `<repo-root>/.followups/FOLLOWUP-<YYYY-MM-DD>-<next-id>.md` (repo root = `git rev-parse
   --show-toplevel`; outside a git repo, the OS temp dir). Create the folder if missing, add
   `.followups/` to the repo's local exclude file (`git rev-parse --git-path info/exclude`,
   append only if absent — never edit the tracked `.gitignore`), never overwrite an existing
   note, and paste the note in the reply too. Throwaway by design — never commit it; never
   treat it as the rolling coordination state.
4. **Redact** secrets / PII; reference files and commits by path/SHA, never paste content.

## Template

```markdown
# Follow-up — <epic-id> <epic title>
Start:   bd show <next-id>        # then: bd show <epic-id>  (STATE block)
Scope:   <one line — outcome, surface/route>
Then:    <next ids in order>  |  bd ready --parent <epic-id>
Skills:  <e.g. epic-autopilot, mattpocock-skills:tdd, mattpocock-skills:code-review, grill-yourself>
Read:    <memory keys · ADR-NNNN · file paths>   (pointers only)
Main:    <sha>  ·  Worktree root: <path>
Command: continue with <next-id> (<scope>) — see <this file>; use <skills>
```

## Completion criterion

Every fact the next agent needs is reachable through a pointer in the note (issue id, memory
key, design doc, path, SHA); no line duplicates what "show" already says. If the note grew past
15 lines, something belongs on an issue.
