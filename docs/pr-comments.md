# pr-comments — background and follow-up register

Design: [`superpowers/specs/2026-10-23-pr-comments-design.md`](superpowers/specs/2026-10-23-pr-comments-design.md)
Plan: [`superpowers/plans/2026-10-23-pr-comments.md`](superpowers/plans/2026-10-23-pr-comments.md)

## Decisions taken during implementation

- **Scripts call siblings by absolute path, not via PATH.** systemd user units do not
  inherit the interactive PATH; resolving siblings from `$(dirname "$0")` keeps the
  poller → notifier → open chain working from the timer.
- **Install-time PATH is baked into the service** (`Environment=PATH=…`) and the poller
  passes `--setenv=PATH` to `systemd-run`, so `claude` and `gh` resolve after a click.
  Consequence: after changing where `claude`/`gh` live, re-run `pr-comments-install`.
- **First install does not enable the timer.** It only creates the config from
  `config.example`; the timer is enabled on the next run, once the config validates.
  Avoids polling with placeholder values.
- **Auth error stops the timer.** One notification, then `systemctl --user stop`.
  It resumes via `pr-comments-poll --resume` (also run by `--fix-auth`), or on the next
  boot since the timer stays enabled; the poller then re-checks auth before polling.
- **API errors other than auth** (network, 5xx, rate limit, malformed response) do not
  stop the timer: one `--error` notification, then quiet until a run succeeds.
- **Click-path failures notify.** Any failure in `pr-comments-open` after a click
  (PR lookup, clone, worktree) raises an error notification instead of exiting silently.
- **Worktree reuse is conservative.** A leftover worktree is recreated only if it is
  clean, has no unpushed commits, and belongs to the same clone; otherwise it is kept
  and the skill states so up front. Paths that exist but are not a worktree of this
  clone are never touched.
- **The skill is invoked namespaced** as `/angelo-skills:pr-comments <url>`, matching
  how plugin skills resolve.

## Follow-up register

Each entry: what to do — why.

1. **Serialise poll runs with `flock`** — the timer run and a manual `--resume` can
   overlap and both notify / write `seen` concurrently.
2. **Route SAML / insufficient-scope errors to `--fix-auth`** — today they are classed
   as generic API errors, so the click does not lead to the auth-repair flow.
3. **Bound the `seen` file** (drop IDs of PRs no longer open) — it grows without limit.
4. **Make `IGNORE_AUTHORS` matching case-insensitive** — GitHub logins are
   case-insensitive; `Foo-Bot` vs `foo-bot` currently slips through.
5. **Test `pr-comments-notify`** (default action → exec open, unknown flags, missing
   config) — it is the only script without tests.
6. **Align SKILL.md queries with `filter.jq`**: thread comments `last:1` instead of
   `first:50`; whitespace-only review bodies ignored; add `pageInfo` where
   `hasNextPage` is mentioned; `-f` vs `-F` for owner/name; name the `handled` file;
   define "my last activity" the same way the filter does.
7. **Escape PR titles safely** — `@tsv` turns backslashes/tabs in titles into escapes
   that show up in the notification text.
8. **Widen remote normalisation** in `pr-comments-open` (`ssh://`, case) — only
   `git@` and `https` remotes are matched today.
9. **Avoid `--force` when recreating a clean leftover worktree** — it also deletes
   git-ignored files in it (build output, local env files).
10. **PATH rendering under bash 5.2** — `patsub_replacement` corrupts a PATH that
    contains `&` when the installer renders the unit.
11. **Installer validation without `systemd-analyze`** — calendar validation is skipped
    and its test reports PASS; it should report SKIP.
12. **`CLONE_ROOTS` default** when omitted from the config.
13. **Test-runner hardening**: `run_filter` swallows jq exit codes inside `$(…)`;
    duplicate `test_*` names across files are skipped; truncation warning tested only
    for review threads; exit-3 / usage paths in `pr-comments-open` untested.
14. **`--mark-handled` with no IDs** appends a blank line; `--all` without `--reset`
    is silently ignored — both should be usage errors.
15. **Decide whether PENDING/COMMENTED reviews with a body count as actionable** —
    the spec is silent; they currently do.
16. **README: give the concrete installer path** inside the plugin cache.
17. **Spec says `bin/`, code uses `scripts/`** — update the spec.
18. **Optional: port the GraphQL / general-comment handling** to the team review skill
    in its own repo (out of scope here).
