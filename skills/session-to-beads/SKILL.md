---
name: session-to-beads
description: "Save the project state of the current session into the beads tracker (bd): what was read and done in chats, meetings, tickets, wiki pages, logs and pasted messages becomes bead comments, new beads for what the user owns, and bd remember entries for decisions and open questions. Use when the user says 'save this to beads', 'actualizeaza beads', 'put the session into the tracker', or invokes /session-to-beads; harvest-learnings runs it first in a project that uses beads. Lessons about how to work go to harvest-learnings instead."
---

# Session to beads

harvest-learnings keeps *lessons* (how to work). This skill keeps *project state*: what people
decided, asked or left open, and what the user did, as read and worked in this session. The
destination is always `bd` in the current project. Only bd is written; every system that was read
(chat, ticket tracker, wiki) stays read-only.

Needs a project that uses beads (`.beads/` at the root, `bd` on the PATH). Without it, say so and
stop.

## 1. Inventory

List every **source** read in this session, or only the one named in the argument: each chat or
meeting (messages, meeting notes, transcript), ticket, wiki page, log or paste. For each: name,
date, who spoke. Then list the **work done** in the session: changes made, checks run, replies sent.

Done when the list matches the tool calls of the session. A source read only before a compaction
counts too (the summary names it).

## 2. Extract

From each source pull only these **items**, with author, time and the exact words:

- **decision** - what was agreed, and the owner when one is named;
- **request** - something asked of the user or of the user's team;
- **action** - a task with an owner (anyone), and its date;
- **open question** - raised and not answered;
- **state change** - on a ticket, bead, environment or person (joins, leaves, out of office).

From the work done pull the **outcomes**: what is now true that was not before, and what is left.

Exposition, greetings and system notices are not items.

## 3. Route each item

`bd search <ticket key | keywords>` first, then:

| Item | Destination |
|---|---|
| touches an open bead | `bd comments add <id> "<source>, <author>, <date time>: \"<quote>\" ... <what it changes>"` |
| request or action for the user, no bead yet | `bd create "<ticket/subject>: <what>" --labels <source label> -d "<source, author, time, quote>"` |
| decision, open question or team fact, nothing for the user to do | `bd remember --key <topic>-<yyyy-mm-dd> "<source>: <decisions; open questions; owners>"` - one key per source; `bd memories <topic>` first, to update instead of duplicating |
| outcome of work done | comment on the bead that owns the work; a new bead only for what is left to do |

- One meeting = one `bd remember` key holding all its decisions and open questions, plus a bead
  only for each item the user owns.
- A long text (transcript, notes) goes to a file under the project's docs folder; the bead or the
  memory points at the path.
- Secrets (passwords, tokens, links with credentials): write where they live, never the value.
- Times: bd stores UTC. Quote the time as the source shows it and name the time zone.
- Closing a bead needs its own end-to-end check; this skill only comments and creates.
- The project's CLAUDE.md may ask for more on every bead write (labels, a status block, a naming
  rule). Read it before the first write and follow it.

## 4. Report (short, in the user's language)

- per bead: what was written, in one line;
- new beads, with their ids;
- skipped items, one line each with why (already on the bead, not actionable, secret).

Done when every source and every piece of work from step 1 appears in the report, written or
skipped.
