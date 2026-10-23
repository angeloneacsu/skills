# skills

Agent skills by [@angeloneacsu](https://github.com/angeloneacsu). One repo, all skills,
multiple install paths.

## Install

**npx (any agent supporting skills):**
```bash
npx skills add angeloneacsu/skills
```

**Claude Code (plugin marketplace):**
```
/plugin marketplace add angeloneacsu/skills
/plugin install angelo-skills@angeloneacsu
```

**Manual:**
```bash
git clone https://github.com/angeloneacsu/skills
cp -r skills/skills/<name> ~/.claude/skills/        # user-level
# or <repo>/.claude/skills/<name>                   # per project
```

## Skills

| Skill | What |
|---|---|
| [epic-autopilot](skills/epic-autopilot/SKILL.md) | Drive a multi-issue epic to completion, one clean issue per iteration, multi-agent-safe, surviving context limits via the epic's STATE block + a `follow-up` pointer note. Composes `mattpocock-skills` (tdd, code-review, diagnosing-bugs, resolving-merge-conflicts, to-tickets) inside each iteration. |
| [epic-autopilot-subagents](skills/epic-autopilot-subagents/SKILL.md) | Same loop as orchestrator only: builder sub-agent (model per issue: Opus default / Sonnet / auto) + reviewer sub-agent on Fable running `mattpocock-skills:code-review`. You keep forks, landing, tracker state. |
| [follow-up](skills/follow-up/SKILL.md) | Pointer-only follow-up note (≤ 15 lines): next issue id, show commands, scope, skills. State stays on the tracker; the note just sends the next agent there. Saved in `<repo-root>/.followups/` (git-excluded locally). |
| [project-handoff](skills/project-handoff/SKILL.md) | Full handoff document saved inside the project, in `<project-root>/.handoffs/HANDOFF-<date>.md` (root = `.beads/` → `CLAUDE.md`/`AGENTS.md` → git top-level; git-excluded locally, never overwritten). Nothing a later session needs goes under `/tmp`: the same resolver hands back `.scratch/` for working files, drafts are written to files rather than left in prose, and a handoff is deleted before the last issue it covers is closed. |
| [grill-yourself](skills/grill-yourself/SKILL.md) | Self-interrogation resolving technical forks using YOUR principles doc as adjudicator. Principles are user-defined per repo (`docs/PRINCIPLES.md` or path declared in CLAUDE.md) — never bundled. `adversarial-debate` optional (inline steelman fallback). |
| [harvest-learnings](skills/harvest-learnings/SKILL.md) | Harvest of durable lessons into auto-memory, an existing skill's reference files, or the project tracker (`bd remember`). Three modes: **self-harvest** (the agent captures a single lesson on its own the moment it is corrected, errs, or learns something non-obvious), **full harvest** at the end of a session, and **audit** (verify and prune what is already stored; deletions wait for approval). Filters hard (durable, non-obvious, behaviour-changing, not already stored), routes by scope, and reports what was deliberately NOT saved. With the plugin install, a `SessionStart` hook (`hooks/hooks.json`, matcher `compact`) reminds the agent after a context compaction to harvest what the summary still carries. |
| [gcp-iam-analyzer](skills/gcp-iam-analyzer/SKILL.md) | gcloud / gsutil / bq commands → exact IAM permissions, predefined vs custom roles, and a ready Terraform snippet (google provider, or the repo's own IAM tfvars shape). |
| [k8s-scoring-fix](skills/k8s-scoring-fix/SKILL.md) | Kubernetes resource-efficiency fixes on helm / kustomize stacks: CPU/memory right-sizing from real usage, probes (Spring Boot, OIDC-protected frontends, Cloud SQL proxy), non-root hardening, validated with `kubectl diff`, plus technical / executive / plain-English reports. |
| [java-wildfly-performance](skills/java-wildfly-performance/SKILL.md) | JVM (JDK 17/21/25) + WildFly (Undertow, io, IronJacamar) tuning for heavy-load Jakarta EE apps. Scripts: unified-GC-log analyzer, thread-dump analyzer, `standalone.xml` validator, JDK-aware JVM flag recommender. |
| [pr-comments](skills/pr-comments/SKILL.md) | Desktop notification when a human comments on one of your open PRs (systemd user timer + `gh`); clicking opens a terminal with Claude Code in a temporary detached worktree, where the skill walks through unresolved comments one by one (fix / modify / skip / mark handled) and posts nothing without your OK. Linux + systemd + `notify-send` only. |

### pr-comments setup
Linux with systemd user units, `gh` (authenticated), `jq`, `notify-send` (`busctl` checks that a notification server is running). Scripts are copied out of the
plugin cache, so re-run the installer after a plugin update:
```bash
bash skills/pr-comments/scripts/pr-comments-install    # path inside your clone / the plugin cache
$EDITOR ~/.config/pr-comments/config                   # orgs, clone roots, worktree dir, terminal, schedule
pr-comments-poll --dry-run                             # show what would be notified
```
Installs scripts to `~/.local/bin`, data to `~/.local/share/pr-comments`, and the `pr-comments.timer`
(default every 5 min, Mon-Fri 08:00-19:00). First run notifies every actionable comment, one notification
per PR; `pr-comments-poll --reset [--all]` replays that wave. After an auth error the timer is stopped;
clicking the notification opens `/angelo-skills:pr-comments --fix-auth`, which ends with `pr-comments-poll --resume`
(the poller also resumes by itself once `gh auth status` passes again). The terminal runs the namespaced
plugin form `/angelo-skills:pr-comments`; if you installed the skill manually as a user skill
(`~/.claude/skills/`), the plain `/pr-comments` works instead. On a first install the timer is not enabled
until you have edited the config and re-run the installer.

**Desktop on another machine** (e.g. you work over ssh in a VM without a graphical session): install on
both. On the machine with `gh` and the clones set `RUN_TIMER=no` (no timer there). On the desktop set
`REMOTE=<ssh destination>` (bare, as in `ssh <dest>`) and `TERMINAL` (e.g. `konsole -e`, `terminator -x`); there the timer runs
`ssh REMOTE pr-comments-poll --emit`, shows the notifications and keeps the seen-state, and a click opens a
local terminal with `ssh -t REMOTE pr-comments-open --here …`. The desktop needs `ssh` with a key usable
from a systemd user unit (the installer checks), `jq` and `notify-send`; it does not need `gh`.

### epic-autopilot extras
- `skills/epic-autopilot/templates/HANDOFF-TEMPLATE.md` — copy per epic as the rolling handoff
- `skills/epic-autopilot/docs/CLAUDE-md-block.md` — project-parameter block + concurrency-safe
  merge recipe; paste into each target repo's CLAUDE.md and fill the `<slots>`

Soft dependency: the [mattpocock-skills](https://github.com/mattpocock/skills) Claude Code plugin
(`/plugin install mattpocock-skills`) — the loops call its `tdd`, `code-review`, `diagnosing-bugs`,
`resolving-merge-conflicts`, `to-tickets`; without it those steps degrade to the repo's own
conventions. Place in Matt's flow: `grill-with-docs → to-spec → to-tickets → epic-autopilot(-subagents)`.

**Personalize by wrapping, not forking:** keep these generic; put repo values in the CLAUDE.md
`## Epic-autopilot parameters` block and your house conventions in a thin wrapper skill that invokes
`epic-autopilot` / `epic-autopilot-subagents` with them (e.g. a `<org>-epic-autopilot` that fixes the
tracker, the reviewer model and the forward-command wording).

Requires per target repo: a principles doc (fork adjudication authority) referenced from
CLAUDE.md. Note: agent-local memory does not travel between machines — durable facts go
in the handoff or the issue tracker.

## Layout
```
skills/<name>/SKILL.md       # one dir per skill (npx-skills + Claude Code compatible)
.claude-plugin/              # plugin + marketplace manifests for /plugin install
```

## License
MIT
