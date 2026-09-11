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
| [project-handoff](skills/project-handoff/SKILL.md) | Full handoff document saved inside the project, in `<project-root>/.handoffs/HANDOFF-<date>.md` (root = `.beads/` → `CLAUDE.md`/`AGENTS.md` → git top-level; git-excluded locally, never overwritten). |
| [grill-yourself](skills/grill-yourself/SKILL.md) | Self-interrogation resolving technical forks using YOUR principles doc as adjudicator. Principles are user-defined per repo (`docs/PRINCIPLES.md` or path declared in CLAUDE.md) — never bundled. `adversarial-debate` optional (inline steelman fallback). |
| [harvest-learnings](skills/harvest-learnings/SKILL.md) | End-of-session harvest of durable lessons into auto-memory, an existing skill's reference files, or the project tracker (`bd remember`). Filters hard (durable, non-obvious, behaviour-changing, not already stored), routes by scope, and reports what was deliberately NOT saved. |
| [gcp-iam-analyzer](skills/gcp-iam-analyzer/SKILL.md) | gcloud / gsutil / bq commands → exact IAM permissions, predefined vs custom roles, and a ready Terraform snippet (google provider, or the repo's own IAM tfvars shape). |
| [k8s-scoring-fix](skills/k8s-scoring-fix/SKILL.md) | Kubernetes resource-efficiency fixes on helm / kustomize stacks: CPU/memory right-sizing from real usage, probes (Spring Boot, OIDC-protected frontends, Cloud SQL proxy), non-root hardening, validated with `kubectl diff`, plus technical / executive / plain-English reports. |
| [java-wildfly-performance](skills/java-wildfly-performance/SKILL.md) | JVM (JDK 17/21/25) + WildFly (Undertow, io, IronJacamar) tuning for heavy-load Jakarta EE apps. Scripts: unified-GC-log analyzer, thread-dump analyzer, `standalone.xml` validator, JDK-aware JVM flag recommender. |

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
