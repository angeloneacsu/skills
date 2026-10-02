---
name: harvest-learnings
description: "Harvest the durable lessons from the current session into long-term storage — the auto-memory directory, an existing skill's reference files, or the project's bd tracker — and deliberately discard the rest. Enforces the judgement that keeps these stores useful: one fact per file, route by scope, dedupe against what already exists, and report what was NOT saved and why. Use when the user says 'ai invatat multe azi', 'salveaza in memorie', 'save what you learned', 'capture learnings', 'harvest', or invokes /harvest-learnings at the end of a working session. ALSO invoke it yourself, unprompted and mid-task, the moment one of these happens: the user corrects you, you catch your own mistake or a false assumption, or you learn something non-obvious that would change what you do next time (a misleading error and its real cause, a command that settled a question) — that is a self-harvest of that single lesson, do not wait for the end of the session. Argument 'audit' (or 'curata memoria', 'prune memory') reviews the memories already stored and removes the stale ones."
---

# Harvest learnings

You are converting what this session taught you into long-term memory. The hard part is **not**
writing files — it is deciding what deserves to exist forever. A memory store that accumulates
everything is worse than one that stays empty, because it costs context on every future session and
teaches you to trust stale claims.

Default to saving **nothing**. Make each item earn its place.

## Three modes

| Mode | When | Scope |
|---|---|---|
| **Self-harvest** | Mid-task, on your own initiative, at a trigger below | The one lesson that just happened |
| **Full harvest** | The user asks, or the session is ending | The whole session, Phases 1–5 |
| **Audit** | Argument `audit`, or the user asks to clean the memory | What is already stored |

### Self-harvest — capture at the moment, not at the end

The details that make a lesson usable — the literal error text, the exact command, the user's own
words — are sharpest right when it happens and are the first thing a context compaction drops. So
do not queue lessons for the end of the session. Harvest the single lesson now, when one of these
fires:

- **The user corrects you** — "no", "not like that", "I told you…", or they redo your work.
- **You catch your own mistake** — a wrong command, a wrong file, a claim you had to retract.
- **An assumption turned out false** and you can say what proved it false.
- **You learned something non-obvious** the docs or the repo would not have told you.

A mistake is the strongest trigger. The lesson is never "I made an error"; it is the **check that
would have caught it** — write that as the rule.

Procedure, kept small so it does not derail the task:

1. One candidate only. Skip Phase 1.
2. Run the Phase 2 filter exactly as written. Most candidates still die here — a typo you fixed in
   the next command is not a lesson. Being triggered more often must not mean saving more.
3. Route (Phase 3) and write (Phase 4).
4. Tell the user in **one line** what you saved and where, or nothing at all if the filter killed
   it. Then go back to the task. Do not ask permission first; the one line is what lets the user
   overrule you.

If the same lesson fires a second time in a session, the first memory was not actionable enough:
sharpen that file instead of adding another.

A full harvest later in the session treats what self-harvest already wrote as stored (filter
question 4) and reports it under "saved" so the user sees one complete list.

## Phase 1 — Enumerate candidates

*(Full harvest. Self-harvest starts at Phase 2 with its single candidate.)*

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

*(Full harvest. A self-harvest reports in one line, as described above.)*

Tell the user, briefly:

- what you saved and to where, with the one-line reason each earned its place
- **what you considered and deliberately did not save, and why**

The second half is the point. It is what proves the filter ran, and it invites the user to
overrule you on a specific item rather than on the whole batch.

## Audit — prune what is already stored

The filter guards the way in; nothing guards what is already there. A memory that was true when
written and is false now is worse than a missing one, because it is trusted. Run this when asked,
and offer it at the end of a full harvest when the index has grown past roughly 40 entries.

For every entry in `MEMORY.md`, read its file and check, in order:

1. **Do the things it names still exist?** Verify each file, command, flag, package, host or
   setting with a real lookup (`ls`, `grep`, `command -v`, the tool's `--help`) — not from memory.
2. **Is it still true?** Re-run the verification the **Why** line cites when that is cheap and
   read-only. If you cannot verify it, say so; do not guess either way.
3. **Has it expired?** A `project` memory about work that has since shipped, or a dated constraint
   whose date has passed, has done its job.
4. **Is it duplicated or contradicted** by another memory, CLAUDE.md, or a skill that now covers it?
5. **Is it actionable?** A memory with no decision rule in it is trivia that slipped through.

Then decide per entry: **keep**, **fix** (update the fact, keep the file), **merge** (fold into the
entry it duplicates), or **delete** (file and its `MEMORY.md` line together). Also repair the index:
a line pointing at a missing file, or a file with no line.

Apply the fixes and merges directly. **List the proposed deletions with their reason and wait for
the user's go-ahead** before removing anything — a deleted memory cannot be recovered from the
conversation. Report the result as a table: entry, verdict, evidence.

Audit only the memory directory. Skill reference files and the project tracker have their own
owners and review paths.

## Compaction

A compaction is where un-harvested lessons are lost: the summary keeps that a correction happened
and drops the literal text that made it usable. Self-harvest is the real defence, because nothing
can run between the decision to compact and the summary. As a backstop, whenever you resume from a
compaction summary — the plugin's `SessionStart` hook reminds you, but do it without the reminder
too — look through the summary for a correction, a mistake or a false assumption that was not yet
stored, and self-harvest it before continuing the task. If the summary no longer holds enough
detail to state the rule and its evidence, skip it rather than reconstruct it from guesswork.

## Anti-patterns

- Saving a session summary. Nobody reads it and it is stale within a week.
- Splitting one lesson across three files, or cramming three lessons into one.
- Writing a memory that names a file, function or flag **without verifying it still exists**.
- Copying a live secret, token or credential into any file. If one appeared in the conversation,
  record the *path where it lives*, never the value.
- Creating a new skill for a one-off symptom that belongs in an existing skill's troubleshooting.
- Reporting only what you saved, hiding the judgement calls.
- Self-harvesting every stumble. The trigger fires often; the filter must still kill most of it.
- Turning a self-harvest into a ceremony — a multi-paragraph report, or a question, in the middle
  of someone's task.
- Writing a mistake down as a confession ("I forgot to…") instead of as the check that prevents it.
- Deleting memories during an audit without showing the list first.
