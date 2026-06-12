---
name: grill-yourself
description: Self-interrogation that resolves the technical forks in a plan YOU must decide, using the user's documented engineering principles/mantras as the adjudicator. Use when the user delegates a technical decision ("you decide", "decide singur", "the technical part is yours", "grill yourself"), or when a design session hits a technical branch the user can't adjudicate.
---

<what-to-do>

You play BOTH roles: interrogator and respondent. Walk down every branch of the design tree
and resolve each fork **yourself**, using the user's principles as the decision authority — do
not ask the user to adjudicate technical merit. You grill yourself; the principles own the
answers.

Escalate to the user **only** when a fork is genuinely a product/business call, or when two
principles irreconcilably conflict. Otherwise: decide, cite the principle, move on.

</what-to-do>

<supporting-info>

## 0. Load the decision authority first

The principles ("mantras") are the adjudicator, so load them before grilling. **They are
user-defined, per project/company — never bundled with this skill.** Resolution order:

1. **The path the repo's CLAUDE.md declares** (e.g. `Principles doc: docs/PRINCIPLES.md`)
   — explicit override wins.
2. **`docs/PRINCIPLES.md`** (or `PRINCIPLES.md` at repo root) — canonical, numbered principles.
3. **`docs/adr/`** — decisions that already instantiate a principle. Default to standing — don't
   relitigate them *casually*. But they are **versioned law, not bedrock**: see the *lex
   posterior* rule at the end of §1.
4. **`CONTEXT.md`** — domain glossary (terms must stay consistent).

Never duplicate the principle text into this session or the plan — reference `PRINCIPLES §N`.
If no documented principles exist, **stop and say so**: ask the user to supply or confirm the
authority (a principles doc, or ad-hoc rules for this session). Do not invent house principles.

## 1. The self-grill loop — per open decision

For each unresolved fork in the plan, in dependency order:

1. **State it** — the decision + at least two *genuine* alternatives (a steelmanned straw man
   is not an alternative).
2. **Gather evidence** — explore the codebase; for any non-trivial mechanism, **research the
   documented industry-standard approach and whether a mature OSS component already owns it**
   — don't make the user find it, and don't reinvent.
3. **Adversarially challenge your preferred answer.** For a contested or hard-to-reverse fork,
   if an `adversarial-debate` skill is installed, invoke it (Optimist vs Devil's Advocate
   subagents, independently researched, points of clash surfaced). If not installed — or for a
   cheap, already-clear fork — steelman the strongest opposing case inline yourself. Either
   way: if you can't make the opposing case, you haven't understood it.
4. **Adjudicate by the principles** — name the specific principle that decides it
   ("config-driven → reject the literal"; "adopt mature OSS → integrate X, don't build";
   "single SoT → derive from the spec"; "fail-closed → deny path wins"). The principle, not
   taste, breaks the tie.
5. **Record** — append to the decision log: `decision → chosen → PRINCIPLES §N / evidence →
   discarded alternative + why`.
6. **Cascade** — resolve dependent decisions next; a choice often closes or reframes later forks.

**Lex posterior — a newer, better-adjudicated decision repeals an older ADR.** ADRs are versioned
decisions, not laws of nature; the *whole point* of continuing to decide is to supersede what no
longer holds (like statute: *lex posterior derogat priori*). So an existing ADR is **evidence and
default, never a veto.** When your fresh adjudication — same principles, more context — lands on
a *more correct / more normalized* answer than an accepted ADR, **supersede or amend it; do not
defer to it and fragment the better model to "respect" the old one.** That deference is a failure
mode: it produces incoherent compromises that satisfy neither the old decision nor the right one.
The bar is merit, not age — if the older ADR is still the best answer, keep it; if your new one is
better under the principles (esp. coherence/single-SoT, correctness-over-cost, fix-the-shape),
repeal it. When you do repeal:

- **Name it in the decision log**: `supersedes ADR-NNNN §DX → because <principle> + <new context>`.
- **Amend the old ADR's record**: update its `Status:` line with a dated "amended/superseded by
  ADR-MMMM §DX" pointer and cross-link both ways. Never silently contradict a live ADR; never
  rewrite its *body* (the historical decision stays legible — you repeal, you don't falsify).
- **A superseding decision is hard-to-reverse by definition** → it clears the ADR bar; file the
  new ADR (or amend an in-flight one) rather than leaving the repeal as a loose claim.

Escalate the repeal to the user (§2) only if it's *also* a product/business call — not merely
because the old ADR was "already accepted."

## 2. When to escalate (the only questions you ask)

- The fork is a **product/business** call (scope, user-facing behaviour, cost trade the user owns).
- **Two principles conflict** and the docs don't rank them.
- Acting is **hard to reverse AND** the evidence is genuinely ambiguous after research.

Bundle escalations; don't drip one trivial question at a time. Everything else: decide.

## 3. Output

- A concise **decision log** (the table from §1.5) surfaced for veto — before exiting planning,
  if planning. The user can override any line; the log makes each call auditable.
- **Offer an ADR** only when all three hold: hard to reverse, surprising without context, the
  result of a real trade-off. File it; reference `PRINCIPLES §N`.

## 4. Stop when

Every branch is resolved, the decision log is complete, and hard-to-reverse calls have ADRs.
Don't keep grilling resolved branches; don't leave a fork silently unmade.

</supporting-info>
