---
name: project-handoff
description: Compact the current conversation into a handoff document and save it inside the project, in a hidden `.handoffs/` folder at the project root, instead of a throwaway temp file. Use when the user asks for a handoff that should persist with the project ("handoff in the project", "save the handoff in the repo", "pune handoff-ul in proiect", "/project-handoff"), next to CLAUDE.md, AGENTS.md and the issue tracker. For a throwaway handoff to another directory or colleague use a plain temp-file handoff; for a pointer-only note to the next tracker issue use `follow-up`.
argument-hint: "What will the next session be used for?"
---

# Handoff into the project

A handoff that survives a reboot and sits with the project it describes: in
`<project-root>/.handoffs/`, hidden, and kept out of git by default.

## Where the file goes

Do not guess the path. Run the resolver bundled with this skill, which creates the empty
file and prints its path:

```sh
scripts/handoff-path            # resolves from the current directory
scripts/handoff-path /some/dir  # or from an explicit one
```

It resolves the nearest ancestor with `.beads/`, else `CLAUDE.md`/`AGENTS.md`, else the git
top-level — so it lands at the project root both from a deep sub-directory and from a
nested project with its own tracker. Files are named `HANDOFF-<YYYY-MM-DD>.md`; an existing
one is never overwritten, a second handoff the same day gets a `-2` suffix. It also adds
`.handoffs/` to the repo's local `.git/info/exclude` (not to `.gitignore`, so no tracked
file changes).

**If it exits non-zero**, it could not tell which project you are in. Ask the user which
project this belongs to and pass the directory — do not pick one yourself, and do not fall
back to `/tmp`.

Then read the created (empty) file before writing to it.

## What to write

Summarise the conversation so a fresh agent can continue.

- If the user passed arguments, treat them as the focus of the next session and tailor
  the whole document to it.
- Suggest the skills the next session should load.
- **Do not duplicate** what other artifacts already hold — PRDs, plans, ADRs, tracker
  issues, commits, diffs, the project's own notes. Reference those by path, issue id or URL.
- State plainly what was verified versus inferred, and what could not be checked and
  why. A blocked check is information the next session needs.
- Redact secrets and credentials: record the path where a secret lives, never its value.

## After writing

Tell the user the path, and that `.handoffs/` is excluded from git locally. Do not commit
the handoff unless asked; if the user wants handoffs shared through the repo, they remove
the line from `.git/info/exclude`.
