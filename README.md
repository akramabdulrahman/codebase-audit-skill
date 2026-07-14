# codebase-audit

A **deterministic, evidence-first codebase-audit skill**. It reverse-engineers a project's **actor model** —
for each role: what it does, which API it calls, what that writes to the DB, and back to UI state — then
generates a ranked, **provenance-linked** list of authorization/logic risks (IDOR/BOLA, client-side-only
authz, over-exposed endpoints) and an exploitability-triaged dependency report.

Everything in the extraction layer is deterministic AST/ORM analysis (**no LLM, no Joern, no CodeQL** —
custom Babel/AST + Mongoose reflection). The output is a **candidate list for a human/LLM gate**, not a
verdict — every finding cites the exact `file:line` at each hop so you can revise the proof.

Built for **Node/Express + Mongoose (CommonJS)** backends and **React + Redux + React-Router** frontends,
and designed to extend to other stacks by adding probes behind a normalized *facet* contract.

## Quick start
```bash
npm install                       # vendors @babel/parser + @babel/traverse (probes fall back to this)
python3 probes/run.py <repo>      # detect stack → extract facets → analyze → .audit/report.html
```
`run.py` writes `.audit/{stack.json, facts/*.json, analysis/*.txt, report.html}`. Open the HTML report:
per-role functional requirements, the authorization intersection, IDOR candidates with evidence chains, and
a coverage banner that refuses to read as "clean" when the extractor didn't understand your stack.

Requirements: **Python 3** (stdlib only) + **Node** (for the Babel probes). No database, no build step.

## What it produces
- **Per-role actor model** — `role → page → thunk → API → controller → Model.op → state`, each hop with `file:line`.
- **IDOR candidates** (HIGH/MEDIUM/REVIEW) via *sibling-asymmetry*: a handler that mutates an entity but omits
  the ownership check its sibling handlers enforce — named predicate + the sibling that proves it.
- **Authorization intersection** — backend-enforced ∩ client-exposed: `CLIENT-SIDE-ONLY-AUTHZ` (the role gate
  lives only on the client → direct-API bypass), `BACKEND-ONLY` (craft-a-request surface), `AGREEMENT`.
- **Dependency exploitability triage** — every `package.json` (not just `server/`), ranked by
  severity × direct × runtime × imported × fix-available, so you get the *urgent* few, not all N advisories.
- **Coverage signal** — distinguishes "clean" from "the extractor didn't match this stack" (no false clean).

## How it works
`reference/v2-mechanism.md` — the full mechanism, tools, and strategies.
`reference/facets.md` — the normalized facet contract (the stack-independent interface).
`stacks/capabilities.yaml` — the probe → facet → stack → knob registry (how to teach a new stack).
`probes/README.md` — the probes/analyzers index. `probes/V2-STATUS.md` — the honest status + eval history.

Pipeline: **detect stack** (`probes/detect.py`) → **extract facets** (6 stack-specific probes) →
**analyze** (stack-agnostic joins: `intersect.py`, `actor-model.py`, `role-paths.py`) → **report**.
A per-project `.audit/stack.json` holds the config knobs; it's created on first run and reused (hand-editable).

The skill also ships a human-gated pipeline (`SKILL.md` + `stages/1-5`) that consumes these deterministic
candidates for actor-model verification, triage, dependency review, and a live regression-test net.

## Tests
```bash
python3 probes/test/run_tests.py         # golden facets + determinism + semantic invariants + robustness
```
A synthetic fixture app (`probes/__fixtures__/mini-mern`) locks determinism and correctness invariants
(a known IDOR must be HIGH, guarded handlers cleared, no candidate names a field its model doesn't declare)
so the analysis can't silently regress.

## Honest limitations (read before trusting output)
- **Candidate generator, not a verdict.** HIGH ≠ confirmed; every finding needs the human/LLM gate + ideally a
  live cross-role reproduction. Nothing is "verified" without an executed test.
- **`scoped_roles`** (role → tenant attribute) is the one project-specific knob; if it doesn't match your repo's
  roles the coverage signal warns loudly, but you should set it in `.audit/stack.json`.
- **Enforcement detection is an enclosure heuristic**, not full dataflow/dominance — query-level scoping
  (`filter.orgId`) and some control-flow shapes can still fool it. See `probes/V2-STATUS.md` for the open items.
- **Frontend role attribution is page-granularity** (a role-gated button inherits its page's roles).
- Stacks beyond Express/Mongoose + React/Redux need new probes (the facet contract stays the same).

## License
MIT (see LICENSE).
