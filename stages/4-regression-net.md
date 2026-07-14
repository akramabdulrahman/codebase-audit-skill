---
name: regression-net
description: Stage 4 of the audit pipeline. First brings the app LIVE via the stack adapter (install deps + capture the dependency-audit report → set up the DB via discovered scripts, safety-gated → run the app → find the live test runner), then builds the right-sized regression net for the HUMAN-APPROVED per-role critical paths at all three levels (unit / integration / e2e). Characterization tests (green, pin current behavior) + reproduction tests (red→green, prove approved findings). Stack lifecycle lives in stacks/<stack>.yaml — teach a new stack by adding that file. Use AFTER actor-model + consistency-scan are approved.
---

# Stage 4 — Regression net (characterization + reproduction tests)

Goal: a small, high-value test suite covering the **critical flows the human approved** — not coverage %. Two test colors: characterization (green, guards behavior) and reproduction (red, proves an approved finding; green = the fix's acceptance criterion).

Read `../reference/artifacts.md` first.

## Preconditions (gate check)
`.audit/actors.yaml` and `.audit/findings.md` both `_meta.status: approved`.

## Inputs
`.audit/actors.yaml` (per-role **capabilities** = `MAY`, **must_not**, **crud_matrix**, **critical_flows**), `.audit/findings.jsonl` (approved), `.audit/project-profile.json` (**`stack`** + `lifecycle` + DB safety gate + seed fixtures).

## Phase 4A — Live bring-up (adapter-driven; do this before writing tests)
Live proof needs a running app + DB. Follow the stack adapter, in order. **Read `project-profile.json.stack`, load `../stacks/<stack>.yaml`** (the single place stack lifecycle lives — teach a new stack by adding that file). If `stack: unknown` or no adapter, do this manually and note the gaps.

1. **Install deps** — per the adapter's `install`. **Capture the vulnerability report as a first-class output**: run `lifecycle.audit_command` (`npm audit` / `pip-audit` / `bundle audit` / `govulncheck`) → `.audit/live/deps-warnings.md` (keep high/critical across runs so the trend is visible). Do **not** auto-`audit fix` — reachability is triaged in Stage 3.
2. **Set up the DB** — use the discovered project scripts, don't invent: `lifecycle.setup_scripts` (build:db / seed / migrate), `lifecycle.db_bringup_files` (docker-compose / .env.example). **SAFETY GATE:** confirm the TEST DB URI (`profile.db_env_vars`, name contains `test`) ≠ dev/prod before any reset; if the gate can't be established (e.g. gwent had **no DB env vars detected**), STOP and ask rather than risk a wipe.
3. **Run the app** — `lifecycle.run_scripts` (dev/start/server); use alt ports if siblings hold them. **Health check**: hit a known route and confirm it responds before proceeding.
4. **Verify the LIVE test runner** — cross-reference CI with declared test scripts, then run each candidate; use the one that actually passes (e.g. ICS: vitest live, jest broken). Now author + run tests (Phase 4B).
5. **Teardown** when done — stop the app, stop/remove the DB container; never drop a non-test DB.

Record what actually happened in `.audit/live/bringup.md` (commands run, ports, which steps worked / were `blocked` and why). If the env can't be stood up, Phase 4B still emits runnable skeletons marked `blocked` + a fixtures plan.

## Test-level selection (the pyramid, driven by the functional model)
Build the suite as a **matrix: role × critical-path × level**. Right-sized, not coverage% — many fast unit tests, fewer integration, a few e2e.

| Level | What it covers (source in `actors.yaml`) | ICS live stack |
|---|---|---|
| **Unit** (many, fast, no DB) | Pure business rules pulled out of `capabilities` + `crud_matrix`: money/rounding, eligibility (mandatory-training/certificate), state-transition guards, and each **ownership predicate as a pure function**. | vitest |
| **Integration** (backbone) | Each `critical_flow` driven through the real entry point + DB, **per role**. The `must_not` rows become the **access-control matrix** here: replay each request as each role / as owner-A-vs-B (OWASP Autorize/AuthMatrix). | vitest + seeded test DB |
| **E2E** (few, headline journeys) | The multi-step actor journeys from `critical_flows` (participant register→attend→certificate; org-admin invite→book→export). Only the load-bearing ones. | Playwright |

Mapping rule: a **`MAY` capability** → a **characterization** test at the lowest level that exercises it (pure rule→unit; flow→integration; journey→e2e). A **`MUST NOT`** → a **reproduction/authz-matrix** test at integration (cross-owner → expect deny). Every approved finding → a red reproduction test at the level that reproduces it.

These are **characterization / golden-master tests** (Feathers): they pin *observed* behavior, not correct behavior. Two must-haves the technique demands:
- **Mask volatile values before asserting** — timestamps, generated ids/tokens, ordering, random, absolute dates. Un-masked golden masters are flaky and get ignored. Normalize (`createdAt`→`<ts>`, sort collections) so the assertion captures *semantic* behavior only.
- **Sample, don't enumerate** — for a wide input space, drive the flow with a *seeded* RNG (`m+p*i`, documented seed) over N inputs and snapshot the outputs. Size N to how much the flow scares you. Cheap broad coverage vs one hand-picked case.

## Phase 4B — Author & run the tests (after 4A is up)
1. From `project-profile.json`: use the **live** test harness (verified in 4A step 4 — e.g. ICS = vitest, NOT jest). Reuse existing fixtures/factories. Honor the **test-DB safety gate** before any DB-resetting test.
2. Build the **access-control fixtures**: one authenticated actor per scoped role (from the profile's seed identities), **plus a second org/owner B**. This fixture set = the OWASP Autorize/AuthMatrix "replay each request as each role" access-control matrix, in test form — it's what makes cross-owner/tenant tests possible.
3. **Characterization tests (green):** walk the role × critical-path × level matrix. For each `MAY` capability, pin CURRENT behavior at its level (pure rule→unit; flow→integration; journey→e2e) with **masked** golden assertions. Per flow cover happy path + the highest-value edges: a **cross-owner/tenancy case** (the ownership predicate — BOLA families 1 & 3), a **boundary case**, a key error case. Skip roles that can't reach a path (the `crud_matrix` says who can).
4. **Reproduction tests (red):** per approved finding, write the failing test that asserts CORRECT behavior (e.g. A acts on B's object → expect 403). It must fail at HEAD (confirms the finding — graduates `probable`→`verified`) and pass once fixed. For BOLA, test **write-paths and lifecycle states** (families 1 & 4), not just reads — read-only tests miss ~half of real cases.
5. **Sanity gate:** run them. Characterization MUST pass at HEAD; reproduction MUST fail at HEAD. Mark `blocked` (with reason) if a flow can't be executed.

## Verification recipe (proven — the fastest path to `verified`)
The highest-value output is flipping the top findings `probable → verified`. On a Node/Express/Mongoose repo this is cheap because the existing test harness already gives you auth + a seeded DB:
1. **Reuse the project's login + request helpers.** Look for an existing auth test (`__test__/**/auth/*.test.js`): it usually has a `login(app, {email,password,loginRole})` helper and drives `app.js` in-process via `supertest` — no separately-running server needed for integration level.
2. **Use existing seed actors.** The fixtures very often already seed two orgs + one actor per role (e.g. `tpAdminX/tpAdminY`, sessions tagged `runByTrainingProvider`). Grep the fixture id map before inventing seed data — the cross-owner pair you need is usually already there.
3. **Write the red test = the `MUST NOT`.** `login` as owner-**B**, send the real request against owner-**A**'s object, `expect(res.status).toBe(403)`. It FAILS at HEAD (server returns 200) — that failure IS the confirmation. Prefer a route with no request body (reopen, delete) for a clean assertion.
4. **Pair a green.** The same route hit by an actor who legitimately can't reach it (a participant) → 403 — pins that the role gate holds.

Bring-up gotchas seen in the wild (log them to `bringup.md`):
- **Replica-set hostname:** a Mongo on `:27017` may be a replica-set advertising an unresolvable internal container hostname (`getaddrinfo ENOTFOUND <id>`). Fix: point the TEST URI at a standalone mongo (`?directConnection=true`), isolated DB name.
- **Live runner ≠ declared:** run the candidate test commands; use the one that passes (e.g. vitest live while jest is declared-but-broken).
- **`timeout` is absent on macOS** — rely on the runner's own timeouts.

## Output
- Test files in the project's established test locations, at the right level (unit + integration in the unit stack, e2e in the e2e stack).
- `.audit/tests/<flow>.yaml` + `.audit/regression-net.md` — the **role × critical-path × level** traceability matrix (which role, which capability/`MAY` or `MUST NOT`, which level, which test file, green/red, links to the finding it guards). This matrix IS the deliverable: it shows at a glance what's covered and what's `blocked`.

## Note
This is the stage that needs live per-project runtime (DB, fixtures). It gets furthest on projects with an existing harness; where the env can't be stood up, it emits runnable skeletons + a fixtures plan and marks them `blocked`.
