---
name: actor-model
description: Stage 1 of the audit pipeline. Reverse-engineers the FUNCTIONAL model of the system — per role, what it does (capabilities as `As a [role], I want [X] so that [Y]` user stories with MAY/MUST/MUST NOT), a role×entity CRUD matrix (read/create/update/delete + scope), the ownership predicate per cell, acceptance criteria, and critical flows. Outputs .audit/actors.yaml for HUMAN VERIFICATION before any scanning. Deterministic-first, cheap. Use FIRST — everything downstream depends on this verified model.
---

# Stage 1 — Actor & functional model (human-gated keystone)

Goal: a small, **human-verifiable** model of **what each role does** and **what it must not do**, grounded in code. Not just "who can hit which route" (authorization boundaries) — the full functional picture, because:
- **`MAY` rows** (capabilities) → seed the **regression net** (characterization tests in Stage 4).
- **`MUST NOT` rows** (negative requirements) → seed the **authz/BOLA scan** (Stage 2) — the ownership predicate is the `MUST NOT` "…act on another owner's object."

Read `../reference/artifacts.md` (file bus) and `../reference/bug-classes.md` (C1 BOLA) first. This is modeling, NOT bug-hunting.

## Why this shape (prior art — see `../reference/prior-art.md`)
- Codebase-onboarding/DeepWiki skills describe *structure & functionality* but skip **roles, authorization, and behavior** — our gaps.
- LLM user-story recovery from code works (F1 ~0.8, **one-shot** prompting best) but has **no method to derive roles from code** — it assumes roles are given.
- We supply the missing bridge: we already have the roles (constants) + the entry-point map (`auditctl`), so we can map **code → role → capability** systematically, add **acceptance criteria** (the paper omits them; we need them for tests), and **human-verify** it.

## Inputs
**Start from the v2 deterministic actor model if the stack is supported** (`python3 probes/run.py <repo>` → `.audit/`): `analysis/role-paths.txt` (per-role capabilities: page→API→controller→`Model.op`→state — the `MAY` rows, already grounded), `analysis/intersect.txt` (backend⋂client authz classes — CLIENT-SIDE-ONLY-AUTHZ / BROADER seed the `MUST NOT` rows), `analysis/idor-candidates.txt` (ownership predicates from sibling-asymmetry), `facts/*.json` (the raw facets). This turns Stage 1 into **verify + correct** a computed model instead of inferring one from scratch — much cheaper and grounded. See `../reference/v2-mechanism.md`.
Also: `.audit/routes.jsonl`, `.audit/project-profile.json`, `.audit/bug-history.md` (from `auditctl`); role/scope constants; the auth middleware; `issues.md`/git fix-patterns (reveal intended ownership predicates).

## Procedure
0. **If v2 ran:** load `.audit/analysis/*` as the DRAFT model — role-paths → capabilities, intersect classes → boundaries, idor-candidates → ownership predicates. Your job becomes checking/correcting it against source (mind the honest limits: frontend role = page-granularity upper bound; body-param ids; query-level scoping). If the stack is unsupported, build from scratch below.
1. `auditctl index && profile && history && entrypoints` if not done. Recon with Glob/Grep, **don't Read every file** (onboarding-skill efficiency principle).
2. **Actors:** enumerate roles; mark each **global** (admin) vs **scoped** (owning field on `req.user`, e.g. tpOrgAdmin→`tpOrganisation`, participant→self).
3. **Resources & CRUD matrix:** for each (role × entity), which of read/create/update/delete it performs, from the entry points + queries. Note the **scope** (all / own-org / own).
4. **Capabilities as user stories:** per role, `As a [role], I want [capability], so that [benefit]`, each tagged `MAY`. Group by system/subsystem (DeepWiki style). One-shot; keep each grounded in a specific entry point (cite it).
5. **Negative requirements (`MUST NOT`):** for each scoped role, the boundaries — cross-owner/tenant, privilege escalation, lifecycle. Each `MUST NOT` carries the **ownership predicate** (the condition that enforces it) + where it's correctly implemented, or `unknown`.
6. **Acceptance criteria** on the highest-value capabilities: `Given/When/Then` (these become Stage-4 tests).
6b. **Pattern-conformance pass (C8):** as you model the auth scheme and data relationships, name the canonical pattern each is *attempting* (RBAC vs ABAC vs ReBAC; a relation vs a denormalized copy) and note where the code deviates in a way that drifts — e.g. a scope sourced two different ways (derived for one role, stored for another). **When the intended design is ambiguous, recover intent from git first** (`git blame` + `git log -S"<symbol>"` on the affected lines — the commit progression often shows it's an unfinished migration or accreted patchwork, which answers it); **only add to `open_questions` for Gate 1 if the history is inconclusive.** Compare against the named framework (`../reference/bug-classes.md` C8), not a preference.
7. **Critical flows** per actor → also seed `.audit/flows.yaml`.
8. Write `.audit/actors.yaml` `_meta.status: proposed` and STOP.

## Output: `.audit/actors.yaml`
```yaml
_meta: { status: proposed, produced_by: actor-model, git_sha: <sha>, approved_by: null }
actors:
  - id: employerOrgAdmin
    scope: employerOrganisation                     # global | <owning-field>
    capabilities:                                   # MAY — user stories, each -> a characterization test
      - story: "As an employer-org admin, I want to book my org's staff onto a contracted session, so that they receive training"
        entry: "PATCH /sessions/:id/attendeesList -> updateAttendeesList.js"
        acceptance: "Given a session my org is contracted to, When I add my staff, Then they are registered"
      - story: "As an employer-org admin, I want to manage (invite/edit/remove) staff in my organisation"
        entry: "POST /user-invitation ; PUT /users/:credId"
    must_not:                                       # negative requirements -> the authz/BOLA scan (Stage 2)
      - rule: "MUST NOT mutate a session my org is not contracted to (BOLA family 3: tenant isolation)"
        ownership_predicate: "session.contract.attendingOrganisationsRestrictedTo includes req.user.employerOrganisation"
        predicate_implemented_at: "updateAttendeesList.js (#3213)"   # or: unknown  -> likely bug
      - rule: "MUST NOT edit/delete a user outside my organisation"
        ownership_predicate: "target.employerOrganisation === req.user.employerOrganisation"
crud_matrix:                                        # role x entity -> ops + scope
  session:
    admin: { ops: CRUD, scope: all }
    employerOrgAdmin: { ops: RU, scope: "own-contracted" }
    tpOrgAdmin: { ops: RUD, scope: "runByTrainingProvider === own tpOrg" }
    trainer: { ops: RU, scope: "in session.trainers" }
    participant: { ops: R, scope: "own registrations" }
resources:
  - id: session
    owning_link: "TrainingContract.attendingOrganisationsRestrictedTo (employer); runByTrainingProvider (tp)"
critical_flows:
  - flow_id: FLOW-session-lifecycle
    actor: sessionManagementRoles
    entry: "create -> edit -> finalise -> reopen"
open_questions:
  - "Confirm: should tpOrgAdmin delete sessions of orgs they don't manage?"
```

## ⟶ GATE 1 (you, the human)
Review `actors.yaml` — correct wrong capabilities/predicates, add missing `MUST NOT`s, answer `open_questions`, set `_meta.status: approved`. Downstream refuses to run until approved. **Driver's seat: the scan only checks violations of the model YOU signed off, and the net only guards the capabilities YOU confirmed.**
