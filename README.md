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
| [epic-autopilot](skills/epic-autopilot/SKILL.md) | Drive a multi-issue epic to completion, one clean issue per iteration, multi-agent-safe, surviving context limits via a rolling handoff file. |
| [grill-yourself](skills/grill-yourself/SKILL.md) | Self-interrogation resolving technical forks using YOUR principles doc as adjudicator. Principles are user-defined per repo (`docs/PRINCIPLES.md` or path declared in CLAUDE.md) — never bundled. `adversarial-debate` optional (inline steelman fallback). |

### epic-autopilot extras
- `skills/epic-autopilot/templates/HANDOFF-TEMPLATE.md` — copy per epic as the rolling handoff
- `skills/epic-autopilot/docs/CLAUDE-md-block.md` — project-parameter block + concurrency-safe
  merge recipe; paste into each target repo's CLAUDE.md and fill the `<slots>`

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
