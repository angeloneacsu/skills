---
name: project-handoff
description: Compact the current conversation into a handoff document and save it inside the project, in a hidden `.handoffs/` folder at the project root, instead of a throwaway temp file. Also resolves a project-local `.scratch/` for working files and drafts, so nothing a later session needs is written under /tmp. Use when the user asks for a handoff ("write a handoff", "handoff in the project", "save the handoff in the repo", "pune handoff-ul in proiect", "/project-handoff"), or needs a durable place for a draft, rendered manifest or intermediate output. For a pointer-only note to the next tracker issue use `follow-up`.
argument-hint: "What will the next session be used for?"
---

# Handoff into the project

A handoff that survives a reboot and sits with the project it describes: in
`<project-root>/.handoffs/`, hidden, and kept out of git by default.

## Nothing a later session needs goes under /tmp

`/tmp` — including any per-session scratchpad a harness proposes under `/tmp/claude-*/` — is wiped
on reboot and invisible to the next session. Handoffs, drafts and working files all live inside the
project instead. When a harness instruction points at a temp scratchpad, this rule overrides it.

## Where the file goes

Do not guess the path. Run the resolver bundled with this skill, which creates the empty file and
prints its path:

```sh
scripts/handoff-path            # resolves from the current directory
scripts/handoff-path /some/dir  # or from an explicit one
scripts/handoff-path --list     # existing handoffs for this project, oldest first
scripts/handoff-path --root     # just the resolved project root
scripts/handoff-path --scratch  # <root>/.scratch/ for throwaway working files
```

It resolves the nearest ancestor with `.beads/`, else `CLAUDE.md`/`AGENTS.md`, else the git
top-level — so it lands at the project root both from a deep sub-directory and from a nested
project with its own tracker. Files are named `HANDOFF-<YYYY-MM-DD>.md`; an existing one is never
overwritten, a second handoff the same day gets a `-2` suffix. It adds `.handoffs/` (and `.scratch/`
when used) to the repo's local `.git/info/exclude` — not to `.gitignore`, so no tracked file changes
and nothing is accidentally committed.

**If it exits non-zero**, it could not tell which project you are in. Ask the user which project
this belongs to and pass the directory — do not pick one yourself, and do not fall back to `/tmp`.

Then read the created (empty) file before writing to it.

## What to write

Summarise the conversation so a fresh agent can continue.

- If the user passed arguments, treat them as the focus of the next session and tailor the whole
  document to it.
- Suggest the skills the next session should load.
- **Do not duplicate** what other artifacts already hold — PRDs, plans, ADRs, tracker issues,
  commits, diffs, the project's own notes. Reference those by path, issue id or URL.
- State plainly what was **verified** versus **inferred**, and what could not be checked and why.
  A blocked check is information the next session needs.
- Redact secrets and credentials: record the path where a secret lives, never its value.

### Drafts go in a file, not in the prose

Anything drafted during the conversation that the next session will need — an email, a ticket body,
a command, a config snippet — must be written to a **file**, in `.handoffs/` next to the handoff or
in `.scratch/`, and linked from the handoff by path. Describing a draft is not the same as keeping
it: prose like "the draft was written in conversation, not yet sent" leaves the next session with
nothing to send, and the work is done twice.

Say in the handoff whether each draft was **sent** or is **still pending**. If you do not know,
write that you do not know rather than guessing.

## Tracked work, and deleting the handoff when it is done

If the project uses an issue tracker (beads, Jira, GitHub issues), a handoff is a **pointer to
tracked work**, not a second tracker. Open the issues it needs, then list their ids in the handoff:

```markdown
## Covered by
PROJ-61g.2, PROJ-61g.8, PROJ-61g.12
```

**When the work in a handoff is finished, delete the handoff file before closing the last issue it
covers.** A stale handoff is worse than none: the next session reads it, believes the work is still
open, and redoes it. So before closing an issue, check whether it is the last open one named in any
handoff; if it is, delete that file first, then close.

```sh
grep -l "<issue-id>" "$(scripts/handoff-path --root)"/.handoffs/*.md 2>/dev/null
```

If other issues in `Covered by` are still open, keep the file and add a one-line note at the top
saying which parts are already done, so the next session does not repeat them.

## After writing

Tell the user the path, and that `.handoffs/` is excluded from git locally. Do not commit the
handoff unless asked; if the user wants handoffs shared through the repo, they remove the line
from `.git/info/exclude`.
