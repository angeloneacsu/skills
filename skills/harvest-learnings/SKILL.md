---
name: harvest-learnings
description: "Harvest the durable lessons from the current session into long-term storage — the auto-memory directory, an existing skill's reference files, or the project's bd tracker — and deliberately discard the rest. Enforces the judgement that keeps these stores useful: one fact per file, route by scope, dedupe against what already exists, and report what was NOT saved and why. Use when the user says 'ai invatat multe azi', 'salveaza in memorie', 'save what you learned', 'capture learnings', 'harvest', or invokes /harvest-learnings at the end of a working session."
---

# Harvest learnings

The user is asking you to convert this session into long-term memory. The hard part is **not**
writing files — it is deciding what deserves to exist forever. A memory store that accumulates
everything is worse than one that stays empty, because it costs context on every future session and
teaches you to trust stale claims.

Default to saving **nothing**. Make each item earn its place.

## Phase 1 — Enumerate candidates

Scan the session and list, in your head, every concrete thing you now know that you did not know at
the start. Include the failures: a wrong assumption you corrected is usually worth more than a
procedure that worked.

Typical sources, in rough order of value:

- **An error message and its real cause** — especially one that was misleading
- **An assumption that turned out false**, and how you proved it false
- **A verification command** that settled a question the documentation could not
- **A decision the user made** whose rationale is not recoverable from the code
- **A preference or correction the user stated** about how you should work

## Phase 2 — Apply the filter

For each candidate, ask in order. A single **no** kills it.

1. **Is it durable?** Will it still be true in three months? A pod's current image tag, an open
   MR number, a pipeline id — these expire. The *rule* behind them may not.
2. **Is it non-obvious?** Would a competent engineer reading the repo reach it anyway? If it is
   recoverable from the code, `git log`, the tracker or CLAUDE.md, **do not save it**. Duplication
   creates two sources that diverge, and the copy is always the one that goes stale.
3. **Would it change behaviour?** If knowing it changes nothing about what you would do next time,
   it is trivia. Write down the *decision rule*, not the anecdote.
4. **Is it already stored?** Read the existing memory index and grep the relevant skills before
   writing. Updating an existing file beats creating a near-duplicate.

Things that almost never qualify: summaries of what you did this session, restatements of a plan
document, repo structure, "X is in file Y", anything already tracked as an issue.

## Phase 3 — Route by scope

The most common mistake is putting project state into global memory. Scope decides the destination.

| Scope of the fact | Destination |
|---|---|
| How you should work; a user preference or correction; a general verification habit | **auto-memory** (`type: feedback` or `user`) |
| A reusable procedure or gotcha tied to a platform, tool or vendor, useful across projects | **an existing skill's reference file** |
| State, decisions or open items of one project | **the project's tracker** (`bd remember`, or the project's plan document) — *not* memory |
| Ongoing work or a constraint on this project that the code does not record | **auto-memory** (`type: project`), with relative dates converted to absolute |
| A URL, dashboard, ticket or contact | **auto-memory** (`type: reference`) |

Two rules that decide most borderline cases:

- **Prefer editing an existing skill over creating a new one.** A gotcha belongs in the
  troubleshooting reference of the skill that already owns that domain. Creating a new skill is
  justified only by a genuinely new domain, not by a new symptom.
- **Respect the project's own rule.** If the project's CLAUDE.md says to use `bd remember` and not
  MEMORY.md files, project knowledge goes to `bd` — even when auto-memory would be convenient.
  Check CLAUDE.md before writing.
- **Layered skills: route inside the pair.** When a domain has a published, generic leaf skill and
  a local wrapper that loads it and adds one organisation's data, a gotcha that holds everywhere
  goes to the leaf — edited in the leaf's source repository and shipped through its normal review,
  never in the installed plugin copy, which the next update overwrites. Anything naming the
  organisation's systems, projects, hosts, people or internal identifiers goes to the wrapper. A
  lesson with both halves is split: the mechanism in the leaf, the local values in the wrapper.

## Phase 4 — Write

**Auto-memory** — one fact per file, in the session's memory directory:

```markdown
---
name: <short-kebab-case-slug>
description: <one line; this is what future-you reads to decide relevance>
metadata:
  type: user | feedback | project | reference
---

<the fact, stated so it is actionable without the story around it>

**Why:** <the evidence — the error, the observation, the user's words. This is what makes it
trustworthy later.>

**How to apply:** <what to do differently next time>
```

Then add exactly one pointer line to `MEMORY.md`: `- [Title](file.md) — hook`. Never put the
content itself in `MEMORY.md`; it loads into every session.

Link related memories with `[[slug]]`. Link liberally — a `[[slug]]` with no file yet marks
something worth writing, not an error. Match the language of the existing memories.

**Skill reference files** — append a section that matches the file's existing heading style, and
carry the four things that make a troubleshooting entry usable:

1. **Symptom** — the literal error text, so a future grep finds it
2. **Cause** — the mechanism, not the fix
3. **Where it was seen** — cluster, project, date; this is what lets someone judge staleness
4. **Fix** — correct form and, where it helps, the wrong form marked as wrong

State the failure mode plainly when a thing fails *silently* — that is the part worth paying
context for.

## Phase 5 — Report

Tell the user, briefly:

- what you saved and to where, with the one-line reason each earned its place
- **what you considered and deliberately did not save, and why**

The second half is the point. It is what proves the filter ran, and it invites the user to
overrule you on a specific item rather than on the whole batch.

## Anti-patterns

- Saving a session summary. Nobody reads it and it is stale within a week.
- Splitting one lesson across three files, or cramming three lessons into one.
- Writing a memory that names a file, function or flag **without verifying it still exists**.
- Copying a live secret, token or credential into any file. If one appeared in the conversation,
  record the *path where it lives*, never the value.
- Creating a new skill for a one-off symptom that belongs in an existing skill's troubleshooting.
- Reporting only what you saved, hiding the judgement calls.
