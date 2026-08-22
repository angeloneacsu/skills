<!-- When the tracker supports notes on the epic (e.g. beads), the sections below live in the
epic's STATE block (replace the marked block each iteration) and the handoff FILE shrinks to the
pointer layer written by the `follow-up` skill. Use this full template only when the tracker
can't hold the state. -->
# Rolling handoff — epic <EPIC-ID> (<one-line theme>) — autopilot

**Loop:** <protocol: stop-after-each-bead | self-compact at ~N tokens>. One clean
issue per iteration, full workflow to pushed-on-<main>. Forks adjudicated via
<principles doc path>.

**Suggested skills for resuming agent:** epic-autopilot, <fork-adjudication skill>,
mattpocock-skills:tdd / code-review / diagnosing-bugs, follow-up.

**Main tip:** `<commit>` on origin/<main>.
**Progress:** <n>/<total> children closed. <Concurrency notes: which worktrees other
agents hold — never touch. Standing rules: branch off FRESH origin/<main>, worktree
root <path>, merge via detached temp worktree, push origin HEAD:<main>.>
**GOTCHA list survives here, not in agent memory.**

## Shipped
- **<issue-id>** — <one line what shipped>. Merge `<commit>`. <Memory key if any —
  but fact itself must be summarized HERE; agent memory is machine-local.>

## NEXT → <issue-id> (<priority>): <title>

**Forward command:**
> continue with <issue-id> (<one-line scope>, <surface/files>) — see <this handoff
> path>; use <skills/memories>

**Issue contract (re-verify in tracker + source — do NOT trust this summary):**
- <current state in code: file:line, what fuses/misses what>
- <what this issue changes; what it explicitly does NOT (deferred to which issue)>
- <named forks to grill, with leading option + why>

**Carry-forward gotchas / verify-don't-trust:**
- <exact gate invocations, env quirks>
- <pre-existing red baseline: which tests, tracked under which issue — check BEFORE debugging>
- <repo never-do list: e.g. no bulk-format, no git add -A, merge recipe pointer>
- <tracker quirks>

## Order of remaining work
1. <issue> (<why next / blocked-by what>)
2. ...
n. (NOT epic, user call) <adjacent issues found en route — never auto-pick>

**Worktree note:** <which worktrees exist/kept/removed; what resumes where>
