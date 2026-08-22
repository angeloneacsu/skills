---
name: epic-autopilot-subagents
description: Use when told to drive a multi-issue epic to completion unattended AND to delegate each issue's implementation to a builder sub-agent (reviewer always Fable) instead of coding it in-session — the orchestrated variant of epic-autopilot. Builder model configurable per issue (default Opus; pin Sonnet via a model:sonnet label or builder=sonnet arg; builder=auto weighs it by issue heaviness). Triggers: "epic-autopilot with sub-agents", "drive the epic but delegate the issues", preserving orchestrator context across a long epic.
argument-hint: "<epic-id> [handoff-path] [builder=opus|sonnet|auto] [skills to inject into builder prompts]"
---

# epic-autopilot-subagents

The autonomous epic loop **as orchestrator only**: one clean issue per iteration, unattended,
but **you never write feature code**. Each issue is implemented by a **builder sub-agent** and
checked by a **reviewer sub-agent on Fable** (`model: "fable"`), both dispatched via the Agent
tool from self-sufficient dispatch prompts you assemble. You keep everything that needs
judgement or cross-issue state: issue selection, fork adjudication, landing on main, tracker
state, the epic's STATE block, self-compaction.

Shares its mental model and project parameters with `epic-autopilot` (CLAUDE.md
`## Epic-autopilot parameters` block — tracker commands, worktree root, gates, never-do,
principles doc, STATE location, protocol). The loop below is complete on its own so a
compacted fresh agent can resume from this file alone.

**Position in the mattpocock flow:** `grill-with-docs → to-spec → to-tickets` **→
epic-autopilot-subagents**. Matt's `/implement` is what the builder does; his `/code-review` is
what the reviewer does; you are the loop in between.

## Division of labor (non-negotiable)

| Orchestrator (you, session model) | Builder (sub-agent — model per policy) | Reviewer (Fable sub-agent) |
|---|---|---|
| pick next issue; resolve the builder model; resolve ALL forks via grill-yourself + the principles doc BEFORE dispatch; create worktree + claim; assemble dispatch prompt; adjudicate BLOCKED reports; author repo-mandated close artifacts (CLAUDE.md slot, e.g. a product-narrative doc); land concurrency-safe; close/export in tracker; STATE + `follow-up`; self-compact | implement the decided design in the worktree, test-first (`mattpocock-skills:tdd` at the seams named in the Contract); run gates; commit on the feature branch; return the Report Contract | independent two-axis verdict via `mattpocock-skills:code-review` (Standards vs repo standards + smell baseline; Spec vs the issue's acceptance) plus gotchas; findings go back to the builder |

**Design judgement stays with you.** The builder implements decisions; it never makes them. A
fork discovered mid-build comes back as BLOCKED and you adjudicate. Never delegate the merge.

## Iteration (top to bottom, every issue)

1. **Orient.** `git fetch origin`. Read the epic's STATE. Show epic, list ready. Pick the
   next unblocked child — one per iteration.
2. **Pre-resolve.** Read the issue + any tracker memories. Verify the contract yourself
   (anchors, action strings, route shapes, wire types in source) — never dispatch a guess.
   Run grill-yourself on every real fork NOW. The dispatch prompt carries decisions, not
   open questions. Note any label that mandates a close artifact (CLAUDE.md slot).
3. **Isolate + claim.** Worktree off fresh `origin/<main>` at the worktree root, branch =
   issue id. Claim in tracker. YOU do the per-worktree mechanical setup so the builder
   starts in a working tree.
4. **Dispatch the builder.** Resolve the builder model (policy below). Agent tool,
   `model: "<resolved>"`, prompt built from the Dispatch Prompt Recipe — every section
   filled. A section you cannot fill means you are not ready to dispatch; back to step 2.
5. **Judge the report.**
   - `BLOCKED` → adjudicate (grill-yourself if it's a fork), then **continue the same agent
     via SendMessage** with the decision — don't spawn fresh and lose its context. BLOCKED on
     a *bug* (gate red, premise intact) → send the builder into
     **`mattpocock-skills:diagnosing-bugs`** (tight red loop, fix, regression test) before any
     escalation count starts.
   - `DONE` → dispatch the **reviewer** (`model: "fable"`): worktree path, fixed point
     `origin/<main>`, the issue id as spec (fetched via the tracker) with acceptance criteria
     verbatim, the gotchas; ask it to run **`mattpocock-skills:code-review`** — Standards and
     Spec reported separately, no reranking across axes, hard violations vs judgement calls —
     plus spot-re-running the cheapest gate. (`code-review` spawns its own two sub-agents;
     that is why it lives with the reviewer/orchestrator, never inside the builder.) Findings
     → back to the builder via SendMessage.
   - Still failing after builder→reviewer→builder: no third dispatch — build it yourself
     in-session or escalate the builder's model; don't ping-pong.
6. **Land (orchestrator, concurrency-safe).** First author any repo-mandated close artifact
   and commit it on the feature branch so it merges atomically. Re-run the fastest gate
   yourself in the worktree (the report's verbatim gate tail is evidence, not proof). Then the
   detached-temp-worktree merge recipe from CLAUDE.md → `git push origin HEAD:<main>` → remove
   temp worktree. Never `git add -A`; never touch another agent's tree. Conflict →
   **`mattpocock-skills:resolving-merge-conflicts`** (by intent, never `--abort`), except files
   the repo hands to a merge driver (e.g. a tracker export).
7. **Record.** Close the issue in the tracker (close-reason citing any mandated artifact);
   commit/push the tracker export if file-based. Durable facts from the report's FACTS →
   the epic's STATE (agent-local memory does not travel). Remove worktree + branch.
8. **Refresh STATE, then the pointer.** New main tip, shipped row (incl. resolved builder
   model), NEXT = exactly the next issue (contract + carried gotchas) → the epic's STATE block
   (replace the marked block). Then rewrite the handoff file as the pointer layer with
   **`follow-up`** (≤ 15 lines, "show <next>" first). Written for a context-free orchestrator.
9. **Continue per protocol.** stop-after-each-issue → STOP after STATE + `follow-up`;
   self-compact at ~N tokens → under threshold loop to step 1; at threshold → `follow-up`,
   self-compact with the forward command:
   > continue with <issue-id> (<one-line scope>) via epic-autopilot-subagents — see
   > <handoff-path>; builder skills: <skills>

## Builder-model policy (resolve once per issue, at step 4)

The **reviewer is always Fable** (`model: "fable"`) — judgement isn't cheapened. Only the
builder's model is a knob. Precedence (first match wins; config-driven, nothing hardcoded):

1. **Per-issue label**: `model:sonnet` → Sonnet; `model:opus` → Opus.
2. **Run argument** `builder=opus|sonnet|auto`.
3. **Nothing set → Opus.** Lead with the strong model; downgrade only by a deliberate label/arg.

**`auto`** (opt-in only): builder = Opus if the issue is *heavy* — a `weight:heavy` label, or
notes carrying a serious re-scope / several adjudicated forks / acceptance spanning multiple
capabilities — else Sonnet. In doubt → Opus: a wrong-cheap builder costs a whole
builder→reviewer→builder bounce, a wrong-expensive one costs only tokens.
Log the resolved model + which rule fired in the STATE shipped row.

## Dispatch Prompt Recipe (REQUIRED sections, in this order)

The builder has **no session context** — paste, don't point (pointing at a skill on disk is
fine; the rule is about session context).

1. **Mission** — issue id + title + one-line outcome.
2. **Guard (first action)** — literal commands the builder runs before anything else and
   re-checks before every commit:
   ```
   cd <absolute worktree path> && git rev-parse --show-toplevel && git rev-parse --abbrev-ref HEAD
   ```
   Toplevel must equal the worktree path and branch must equal `<issue-id>`; anything else →
   STOP, report BLOCKED, touch nothing.
3. **Contract** — the issue's design block verbatim: anchors (`file:line`), design-doc /
   principle refs, the decided design for every fork you adjudicated, acceptance criteria
   verbatim. Then one **Method** line: "Build test-first with the Skill tool →
   `mattpocock-skills:tdd` at these seams: <seams derived from anchors + acceptance>; red before
   green, one slice at a time; refactoring waits for review."
4. **Verify recipe** — exact gate commands with their cwd, plus the known-red baseline.
5. **Gotchas** — only the carried-forward STATE items and standing repo rules this issue can
   actually hit (stage explicit paths — never `git add -A`; no bulk-format; …).
6. **Boundaries** — work only inside the worktree; commit only on branch `<issue-id>`; NO
   merge, NO push, NO tracker state changes (read-only show/memories is fine); on a contract
   mismatch or an undecided fork: stop and report BLOCKED with the exact question.
7. **Report format** — the Report Contract below, pasted verbatim.

## Report Contract (the builder's final message IS this)

```
STATUS: DONE | BLOCKED
BRANCH/COMMITS: <branch> — <shas>, files touched
GATES: <command> → <verbatim last lines: pass/fail counts>   (one per gate; never paraphrased)
DEVIATIONS: <anything done differently from the contract, and why — or "none">
FACTS: <durable facts worth the STATE block / tracker memory>
BLOCKED-ON: <only when BLOCKED: the exact question or missing decision>
```

## Known failure modes (observed) and their counters

| Observed failure | Counter here |
|---|---|
| cheap sub-agent committed on main instead of the worktree | Guard is the FIRST action; orchestrator pre-creates the worktree and the prompt carries its absolute path |
| sub-agent lacked context and guessed API/permission shapes | Contract pastes design + acceptance verbatim; unfillable section = don't dispatch |
| `git add -A` swept build artifacts / symlinks | Boundaries: stage explicit paths only |
| gate failure paraphrased into "tests mostly pass" | Report Contract: verbatim gate tail required |
| endless builder↔reviewer ping-pong | two-bounce limit, then orchestrator takes over |
| mandated close artifact forgotten → repo hygiene gate fails at session close | step 2 flags the label; step 6 lands the artifact on the branch; step 7 cites it in the close-reason |

## Oversized-issue rule — annotate & defer, don't drain the session

Felt in Pre-resolve (the contract won't fit one dispatch prompt, forks multiply, acceptance is
several capabilities) or when a builder keeps coming back BLOCKED on new scope. Stop and:
annotate the issue self-sufficient on the tracker; if it is honestly N issues, split it with
**`mattpocock-skills:to-tickets`** run on the issue (tracer-bullet children, blockers first,
native blocking edges); then pick a smaller unblocked issue or hand the big one off untouched
(no claim, no worktree) via STATE + `follow-up` and self-compact. Never start-and-abandon.

## Stop condition & escalation

Same as `epic-autopilot`: stop only when every child is closed → final STATE (no NEXT) → report.
Break the loop only for genuine product/business calls, irreconcilable principle conflicts, an
issue whose premise is wrong (file the blocker), or an all-blocked epic. Bundle escalations.
