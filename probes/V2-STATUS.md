# v2 extractor pipeline — status (measured on ICS)

Baseline: `../../audit-skill-v1/` (frozen). Compare: `diff -r ../../audit-skill-v1 ..`.

## Built & working (deterministic, no LLM)
- `mongoose-schema.js` — ORM owner-link graph. **27 models, 53 edges, 0 errors.**
- `derive_predicates.py` — ownership predicates by graph type-join (incl. correct multi-hop `contract.attendingOrganisationsRestrictedTo ∋ req.user.employerOrganisation`).
- `extract.py` — builds `.audit/facts.db` (SQLite: owner_links, auth_attrs, predicate, enforcement, test_authz) and runs the IDOR join. **99 predicates, 145 controllers, 4 test rows.**

## Result vs v1
- ✅ Correctly **CLEARS** the reference impls `finaliseSession`, `updateEmployerOrganisation` (v1's variable-role FPs are structurally gone once enforcement is detected).
- ✅ Correctly **FLAGS** `deleteSession` / `editSession` / `reopenEvent` — the 3 live-verified IDORs — **with the named missing predicate** (`sessions.… == req.user.tpOrganisation`, etc.).
- ✅ Cross-checks against mined `test_authz` 🔴 rows (session entity marked live-verified).

## Honest gaps (all in ENFORCEMENT detection + reachability — not the predicate core)
1. **Grep enforcement can't follow aliasing/destructuring fully.** Fixed destructuring (`const {tpOrganisation}=req.user`); still FPs on **renamed** `req.user` (`removeUserFromOrganisation` → `currentUser`). → **must become an AST/dataflow extractor** (Babel: resolve `x = req.user`, destructuring, then find comparisons `resource.field <op> x.tenantAttr` that dominate the sink).
2. **Candidate-predicate noise.** Shortest-path emits spurious paths (`sessions.trainers[].employerOrganisation`, `participant` on session-mgmt). → **the disposer**: intersect candidates with the *actual compared field-path* in code (needs the AST from #1), and **join with route→roles** so only roles that can *reach* the route are shown (drop participant self-service, creation).
3. **Route→controller→entity join** is dir-heuristic. → parse the router imports (AST) for the exact map.

## Verdict — REVISED after adversarial evaluation (skeptical CS review on ICS)
Earlier claim "predicate deduction is proven" was **too generous — the review disproved it.** Measured result: **55% false-positive rate (16/29)**, and the flagship predicate is **wrong** (`sessions.tpOrganisation`; the code compares `runByTrainingProvider` — two same-type refs, tie broken by declaration order). One confirmed false negative (`updateUserById` tpOrgAdmin gap hidden by an incidental `_id`). **v2 does NOT beat v1** — `derive_predicates.py` is the same code, and v2 *removed* v1's human gate + counter-evidence + "checked & cleared" that were suppressing exactly these FPs.

**Corrected model:** deterministic extraction is a candidate **generator**, not a replacement for disambiguation + the human gate. Three hard requirements before it's trustworthy:
1. **Disambiguate same-type owner-links by the handler's ACTUAL comparison** (read what it compares; never let declaration order pick). Same-type ambiguity is the core flaw — type-join alone is insufficient.
2. **Alias/flow-aware enforcement** (resolve `const currentUser = req.user`, handle `===`/ternary/`.filter/.every`) — grep is unsafe in both directions.
3. **Reachability gate** — join predicates through route `allowedRoles`; ~3/5 current predicate lines are for roles that can't reach the route.
Keep v1's human Gate-1 + counter-evidence; feed them these candidates. Do not delete them.

New real bug the reviewer found by reading (not the tool): `updateTrainerCoursesSet` — bare `authentication()`, any authed user updates any trainer's courses (`userId` from body).

## CLIENT-FLOWS facet (React-Router + classic-Redux-thunk + axios) — built & measured on ICS `client/`
Backend deterministic taint hit a ceiling (Babel AND Joern fail on CommonJS controller→wrapper→mongoose). The frontend is ESM + explicit dispatch names, so the call graph IS statically resolvable. Two probes:
- `probes/client-flows.js` — per-THUNK axios extraction (not file-BFS, which leaks the whole `src/actions` barrel). **109 thunks → 109 backend calls**, each `thunk → {method,url}` (`${x}`→`:x`). Also route→role (App.jsx `createBrowserRouter` + `PrivateRoute allowedRoles`) and `connect({thunk})`/`dispatch(thunk())` bindings.
- `probes/client-join.py` — reconstructs FULL backend paths (index.js direct + 11 mount prefixes resolved — **closes the sub-router coverage gap** that broke `routes.js**`), then joins client thunk URLs ↔ backend routes.

Result on ICS: **128 backend routes, 104 MATCHED, 24 BACKEND-ONLY, 3 CLIENT-ONLY.**
- **BACKEND-ONLY = API-only-reachable surface** (no UI thunk drives it) — the audit signal. It **contains `PATCH /api/users/admin/update-trainer-courses`** = the `updateTrainerCoursesSet` bug the human found by reading. The frontend diff surfaced it deterministically. Also flags `export-csv/:type`, `users/all`, `staff-list.csv`, attendance mutations. (Not all are bugs — `aicc/hacp` SCORM callback, `file-upload/get-signed-url` called inline — it's a **candidate list to review**, same framing as the backend generator.)
- **CLIENT-ONLY (3)** = dynamic `/api/${category}`, a template-concat quirk, and a param-position near-miss — all explainable, no dead calls.
- Role attribution via `connect()` bindings is partial (many rows `?`): component→thunk binding needs `mapDispatchToProps`/`compose`/props-drilling resolution — the weak spot, honest gap.

**Why this matters:** the axios-URL ↔ backend-route join is the deterministic **frontend-intended vs backend-enforced** oracle. Backend-only mutating routes = "reachable only by crafting a request" = the IDOR/over-exposed candidate class, generated with no LLM. Pairs with the backend candidate generator; both feed the human/LLM gate.

## TWO-MODEL ACTOR MAP + INTERSECTION (the "map separately, then intersect" architecture)
Per user direction: don't bolt the client on as corroboration — build BOTH actor models independently, then intersect; the client consumer context also disambiguates the backend's ambiguous ownership dimension.
- `probes/backend-actors.js` — **authoritative** backend actor model. Walks the full mount tree (index + users + 9 sub-routers), resolves `authentication({allowedRoles|allowPublic|decideRoleFlow})` + role constants. **128 routes**; auth mix: 56 roles / 45 authed-any / 14 public / 11 open / 2 decide-role. Closes routes.js's 2-file gap → recovers the earlier false negatives (`deleteUserInvitation`, `updateUserInvitation`).
- `probes/client-flows.js` (upgraded) — **client** actor model. Resolves route path CONSTANTS (`R.*` via `import * as R`), Component→file, file's connect-bound thunks, thunk→axios URL. **83 routes (was 12), 76 with component, 39 with resolved backend calls** → `role → page → backend call`.
- `probes/intersect.py` — set-algebra over the two: **19 CLIENT-SIDE-ONLY-AUTHZ** (server=authed-any/open, UI gates to a proper role subset → the role gate is client-only = **direct-API bypass**; contains the live-verified `session-edit/:id` + `attendeesList`/`add-session`/`scheduled-emails`), 8 FRONTEND-BROADER (UI offers to roles server rejects), 1 BACKEND-BROADER (over-granted), 74 BACKEND-ONLY (API-only surface), 26 AGREEMENT (truth). Each mutation carries `consumer-scope: <tenant attr>` — the ownership dimension picked from *who actually calls it*.
- `probes/actor-model.py` — backend × guards × ORM IDOR generator, now with **sibling-asymmetry predicate naming** (names the field a sibling handler actually compares, e.g. `sessions.runByTrainingProvider == req.user.tpOrganisation`, instead of ORM shortest-path which mis-picks same-type links). 13 HIGH / 14 MEDIUM / 4 REVIEW; all 3 live IDORs in HIGH.

### Honest limits of the two-model map
1. **Frontend role = PAGE granularity, not button/conditional.** `PrivateRoute.allowedRoles` is the page's role set; a thunk fired by a role-gated button inside the page inherits the whole page's roles → `frontendRoles` is an UPPER BOUND. Inflates FRONTEND-BROADER and the client-side role sets (e.g. `employer-admin-sessions` shows all 5 session roles because ViewSessions hosts them, though only employerOrgAdmin triggers it). CLIENT-SIDE-ONLY-AUTHZ *class* is still valid (backend authed-any is a hard fact); the exact role list is not.
2. **Consumer-scope picks one "tightest" role** (SCOPED order) — mis-identifies the verified dimension when several scoped roles consume a route (session bug is tpOrgAdmin, heuristic picks programmeManager). Reconcile with actor-model.py's sibling-confirmed field for the real name.
3. Child-component dispatches + the 4 `useDispatch` sites not followed (connect object-shorthand = 67/99 covered).

## FULL-STACK ROLE PATHS (action ⇒ DB ⇒ back) — the functional-requirement map
Per user: the client analysis must be end-to-end — "full role paths right from the action ⇒ db and back through the backend, to get accurate functional requirements for each role path." Built the two missing layers + a stitcher:
- `probes/db-layer.js` — **controller → query-fn → Model.operation** (ICS controllers don't call mongoose; they call `database/queries/**` wrappers — the exact layer that broke interprocedural taint). **166 query fns, 101 controllers resolved, 26 models.** Recovers blast radius: `deleteUserById → 8 model writes` (GDPR cascade), `deleteSession → 5-model delete cascade`, `editSession → Session.findByIdAndUpdate*`.
- `probes/redux-state.js` — the "and back": **thunk → dispatch({type}) → reducer case → state slice.** `combineReducers` = 33-slice state tree; 173 action types; 104 thunks → slices (`updateModule → CREATE_MODULE_SUCCESS → state.modules`).
- `probes/role-paths.py` — stitches all 4 layers into **per-role capabilities**: `page ──dispatch(thunk)──▶ METHOD url ──▶ controller[allowedRoles] ──▶ [Model.op…] ──▶ state:[slice]`. ICS: admin 37 / programmeManager 37 / tpOrgAdmin 31 / employerOrgAdmin 27 / trainer 27 / participant 19 capabilities. The IDOR reads directly off the path (`session-edit [authed-any] → Session.findByIdAndUpdate*` reached by tpOrgAdmin via UI = client-side-only gate + direct Session write).
- Out-of-redux API-call detection built (axios outside `src/actions`) — **zero on ICS = a finding**: 100% of API calls route through thunks, no out-of-band data flow.

Layer inventory now complete for the actor model: routes✚roles (backend-actors) · role→page→thunk→url (client-flows) · controller→DB (db-layer) · thunk→state (redux-state) · intersection (intersect) · IDOR generator (actor-model) · full path (role-paths).

Remaining honest gaps: (1) frontend role = page granularity, upper bound; (2) slice attribution is filename-heuristic (over-attributes, e.g. deleteSession→4 slices — should link reducer file→combineReducers import); (3) dynamic/conditional query-fn calls in a controller may be missed; (4) DB ops inside a query fn that calls ANOTHER query fn (2-hop within the query layer) not chased.

## VERIFICATION PASS (determinism + correctness) — autonomous run
Determinism: all 9 probes/analyzers produce byte-identical output across 3 runs (pure AST/file reads). Config-driven (AUDIT_CONFIG) output byte-identical to hardcoded defaults; negative test (`auth_fn=WRONG`) collapses all 128 routes to `open` → knobs proven consumed.

Correctness gates (all green after fixes):
- 3 live-verified IDORs (deleteSession/editSession/reopenEvent) → actor-model HIGH ✓
- Reference impls finaliseSession + updateEmployerOrganisation → CLEARED (guards detect ownership) ✓
- deleteUserById → REVIEW (cross-branch, self-id present) ; updateTrainerCoursesSet → surfaced as authed-any mutation but body-param id (no `:id`) so outside the by-id IDOR list — documented gap.

**Two real bugs found & fixed by verification:**
1. **Controller join-key mismatch** — backend-actors stored the bare require name (`updateEmployerOrganisation`) losing the sub-router dir → gkey produced `controllers/updateEmployerOrganisation.js` ≠ guards' `controllers/employerOrganisation/updateEmployerOrganisation.js`, so the "cleared" skip never fired → FALSE POSITIVE. Fix: resolve controller require to a server-relative path. Ripple: MEDIUM candidates 14→6 (better handler→guard/entity joins).
2. **Unresolved role-set constant** — `allowedRoles: sessionManagementRoles` (an Identifier, not an array) was classified `authed-any`; **14 routes misclassified**. Fix: resolve the identifier against the constants module. Result: authed-any 45→32, roles 56→69; the session mutations correctly become role-gated (the true bug is horizontal cross-org IDOR *within* sessionManagementRoles — a tpOrgAdmin editing another org's session — still HIGH in actor-model), and CLIENT-SIDE-ONLY-AUTHZ 19→13 (6 were false: the server DOES gate by role via the constant). AGREEMENT 26→30.

Net: the two fixes tightened both the authz-conflict framing and the IDOR predicate joins. Corrected ICS numbers: 128 routes (auth: 69 roles / 32 authed-any / 14 public / 11 open / 2 decide-role); intersect 13 client-side-only-authz / 8 frontend-broader / 3 backend-broader / 74 backend-only / 30 agreement; actor-model 15 HIGH / 6 MEDIUM / 8 REVIEW.

## GENERALIZATION TEST — gwent-connect-5 (express4/mongoose6 + react17/redux4/react-router-v5)
Ran the pipeline on a SECOND project to test the stack-independence claim. Result: the facet contract + analyzers + db/state/thunk probes generalized cleanly; the frontend needed one probe extension; the backend authz has one structural gap.
- ✅ **db-layer** (74 query fns, 45 controllers, 10 models), **redux-state** (24 slices, 44 thunks), **backend routes** (62), **thunk extraction** (49) — all generalized via knobs, no code change.
- ✅ **Added react-router v5 support** to `client-flows.js`: v5 uses `<PrivateRoute path=... allowedRoles=... component=.../>` JSX (path is a prop; consts from NAMED imports; lowercase `component`) vs v7's `createBrowserRouter` array. Now handles both → gwent 43 ui routes, 25 with resolved calls, roles trainer/admin/localLead/participant. ICS v7 unchanged (83).
- ✅ **Auto-detection hardened**: `detect.py` now greps the auth middleware to pick `public_key` (gwent `allowForPublic` vs ICS `allowPublic`); `client-flows` autodetects `App.js` vs `App.jsx`; `@babel` vendored into the skill with a target-or-skill resolver (`_babel.js`) so probes run on repos that don't ship babel (gwent server/ didn't).
- ⚠️ **Structural gap — in-controller RBAC**: gwent's `authentication()` takes NO `allowedRoles`; roles are enforced imperatively inside controllers (`req.user.role === userRoles.admin`). So `server_routes` roles come back empty → the 15 CLIENT-SIDE-ONLY-AUTHZ is an **upper bound** (some genuinely client-only gates = real bugs; some enforced in-controller = FPs). **Fix = one new probe** that extracts controller-body role guards → same `server_routes` facet; analyzers unchanged. This is the abstraction working as designed: swap/add a probe for the differing facet.

## Per-project config lifecycle (robustness)
`run.py` now **creates-or-reuses** `.audit/stack.json`: absent → `detect.py` builds it (auto knobs + `_help`); present → reused so **human knob edits persist**; `--redetect` rebuilds. The one genuinely-manual knob is `scoped_roles` (role→tenant-attr semantics); everything else auto-detects. Unknown stack → run.py tells you to add a `SIGNATURES` entry + `stacks/<id>.yaml`.

## Engine choice — custom, NOT Joern/CodeQL
The whole pipeline is **custom Babel/AST + ORM probes**. Joern was evaluated and **failed** on the CommonJS backend (jssrc2cpg models `module.exports.x = fn` as anonymous lambdas → 0 cross-function taint). CodeQL was floated for a backend spike but **never used**. The custom approach is why generalization = "add a knob / a probe mode," not "fight a CPG engine." Trade-off: no deep points-to/taint (we sidestep via the frontend ESM call graph + sibling-asymmetry + the two-model intersection).

## ADVERSARIAL REVIEW (3 skeptics) + FIX ROADMAP
Three hostile reviewers (correctness/determinism, genericity/robustness, maintainability/tooling) ran the skill on ICS+gwent and read the code. They CONVERGED on one root cause:
**The engine has no representation of in-controller / role-conditional enforcement, and silently presents empty/wrong facets as high-confidence findings.**

Confirmed defects:
- Reads RBAC only at the route layer → CLEARS real IDORs when the check is in a controller branch (ICS `updateUserById` tpOrgAdmin path, FN) AND fabricates "CLIENT-SIDE-ONLY-AUTHZ / Highest signal" when the branch enforces (all 15/15 gwent rows false — proven at gwent `addUser.js:19`).
- `guards.js` + `mongoose-schema.js` + `actor-model.py` ignored ALL knobs, hardcoded ICS names → non-ICS = silent no-op or FP flood. ("Zero hardcoded" doc claim was false.)
- **No coverage signal**: detected-but-wrong-shape (RTK/functional/single-file/TS) → empty facets → run.py labels `ok` → report renders confident false "clean" (100% FN). The #1 genericity blocker.
- `guards.js` hand-rolls dominance-free dataflow (guard = any tenant-attr compare ANYWHERE + a file-wide FORBID keyword) — the 55%-FP mechanism.
- Facet store decorative (analyzers re-spawn probes ~20×/run); `norm` dup 8×; `client-join.py` "legacy" but live (2nd regex route extractor); dead code shipped (`derive_predicates`=55%FP engine); `catch(_){}` swallows parse/subprocess failures; **zero tests**; latent nondet (unsorted readdir + first-sibling-wins).

### Roadmap (ranked)
- **P0-a** coverage signal + fail-loud envelope across all probes + run.py refuses ~0-coverage "clean"; analyzers load-or-die.
- **P0-b** wire knobs into guards/mongoose/actor-model + crash-guards.
- **P0-c** determinism: sort readdir; order-independent sibling selection.
- **P1-a** fixture repo + golden-JSON + determinism + negative tests (PREREQ for the correctness rework).
- **P1-b** model in-controller RBAC (extract controller-body role guards → server_routes.roles; per-role-branch, sink-dominating enforcement; exclude self-service from bypass; gate sibling-predicate on role-class).
- **P1-c** honest labeling (HIGH/"Highest signal" → candidate); fix false doc claims.
- **P2** dedup `_lib.js`/`_lib.py`; delete/attic dead code; kill client-join double-extractor; TypeScript (.ts/.tsx + babel plugin).
- **P3** DuckDB fact store (read-once); replace guards dataflow with dominance-aware engine / CodeQL; ts-morph for import/symbol resolution.

### DONE so far (this pass — ICS output byte-identical, verified)
- P0-c: sorted all `readdirSync` loops (5 probes); `mongoose-schema` sorts models.
- P0-b (partial): `guards.js` now reads `tenant_attrs`/`user_root`/`controller_dir`; `mongoose-schema.js` reads `model_dir`; both crash-guarded (missing mongoose/dir → facet_error or `[]` on stderr, no crash). **actor-model.py DIR_ENTITY/attr_target still hardcoded — remaining P0-b.**
- P1-c (partial): corrected the two false doc claims in `facets.md` + `v2-mechanism.md`.

## SKEPTIC ROADMAP — steps 1→2→3 DONE (autonomous run, all tests green)
**Step 1 — test harness (was: zero tests, a glass house).**
- `probes/__fixtures__/mini-mern/` — synthetic MERN app (guarded/unguarded/self-service controllers, mongoose refs + query wrapper, v7 routes, connect thunks, combineReducers).
- `probes/test/run_tests.py` + `golden/` — golden-JSON per probe + determinism (2×) + semantic invariants (deleteWidget is a HIGH IDOR, updateWidget cleared) + robustness (missing dirs degrade) + ICS invariant (the 3 live IDORs stay HIGH). **30 passed / 0 failed.**
- Added `mongoose-schema.js` AST-parse fallback (works without mongoose installed — also robustness win) → unblocked the fixture + fixed the `require(mongoose)` crash.
- Removed the hardcoded ICS `DIR_ENTITY`/`attr_target` from `actor-model.py`: entity now derived from the **DB facet** (models the controller writes; multi-model so cascade-deletes resolve to the right entity), tenant-targets from the **ORM** (User owner-links). Fixture now yields the correct IDOR; ICS 3 live IDORs preserved.

**Step 2 — coverage signal + fail-loud (was: empty facets rendered as "clean" = #1 genericity blocker).**
- `probes/coverage.py` — per facet, RAW candidate sites (route-method calls, route-guard JSX, http-client calls, model files) vs RESOLVED. `run.py` prints per-facet coverage; if a facet resolves ~0 despite candidates → `✗ ZERO` + a LOW-COVERAGE banner; `report.py` renders a red "NOT a clean bill of health" banner. Verified: a detected-but-single-file-routes repo now flags ZERO instead of a false clean.

**Step 3 — in-controller RBAC (the root cause: RBAC read only at the route layer).**
- `backend-actors.js` now detects controller-body role gates (`req.user.role === / !== / [..].includes(...)`, constants-resolved) → `controllerGated`/`controllerRoles` per route.
- `intersect.py`: a `controllerGated` route reclassifies AGREEMENT, not CLIENT-SIDE-ONLY-AUTHZ (kills the gwent `addUser.js:19` false bypass).
- `actor-model.py`: sibling-predicate → HIGH only when a SCOPED role can reach it; any-authenticated routes → MEDIUM ("verify not self-service") — fixes the `confirm-email`/`groupBooking` self-service HIGH FPs. ICS: HIGH 15→12 (FPs removed), 3 live IDORs still HIGH.

**Also wired knobs into the two authz probes** (`guards.js` tenant_attrs/user_root/controller_dir; `mongoose-schema.js` model_dir) + determinism sorts + corrected the two false doc claims. **Everything re-verified deterministic; ICS output correct; 30/30 tests green.**

### Still open (documented, not silently broken)
- ICS `updateUserById` FN — ownership check is role-CONDITIONAL inside a branch; needs guards.js per-branch/dominance analysis (the deeper P3 rework — do behind the test harness).
- guards.js still uses a file-wide FORBID heuristic (no dominance); `client-join.py` double-extractor + dead-code cleanup + `_lib` dedup + DuckDB fact store = remaining P2/P3.

## IMPROVEMENT ROUND 2 (post-skeptic) — done, 33/33 tests green
- **guards.js role-conditional coverage** (the #1 remaining correctness item): guards now emits `guardRoles` (null=covers-all; [roles]=only those). actor-model clears a route only if the guard covers ALL scoped roles that reach it; else flags the UNCOVERED roles. **Recovered the `updateUserById` FN** the correctness skeptic found (PUT /api/users/:credId → HIGH: "guard covers only employerOrgAdmin; tpOrgAdmin/programmeManager bypass"). Reference impls (finaliseSession, updateEmployerOrganisation) stay cleared. Added alias resolution to backend-actors' controller-role detection (`const currentUser = req.user`). Locked with 3 new ICS invariants in the test suite.
- **Removed `client-join.py` double-extractor**: actor-model now computes `backend_only` from the SAME AST facets (backend-actors + client-flows), not a parallel regex route engine.
- **Dead code → `probes/attic/`**: extract.py, derive_predicates.py (the 55%-FP engine), query.py, routes.js, client-join.py. Verified no live references.
- **`_lib.py`** shared `spawn()` — deduped the `node()` probe-spawn (was copied in 6 files).
- Deferred (big bet, left for the loop to re-prioritize): DuckDB fact store (analyzers still re-spawn probes), full `norm`/`listFiles` dedup, replacing guards' file-wide FORBID heuristic with dominance.

## SKEPTIC LOOP ROUND 2 — findings + fixes
Three skeptics re-ran, verifying claimed fixes by reproduction + hunting new defects. Verdict: tier decisions improved (updateUserById FN genuinely recovered, determinism solid), but the fixes reused "pattern-in-file" heuristics so the EVIDENCE (named predicate/entity) became unsound, and the deepest hole was silent-drop via scoped_roles.

FIXED this round (34/34 tests green, ICS 3 live IDORs HIGH, determinism holds):
- **`_lib.py` url_norm drift (active shipped bug):** actor-model split URLs on `?` only while others split `?`+`&` → inconsistent backend_only join key. Wired all 4 analyzers to shared `_lib` (url_norm, ctrl_key, load_knobs); deleted the drifted locals.
- **Predicate soundness (the #1 correctness risk):** the confirmed loop attached a guard's compared field to EVERY model the controller writes → `SessionParticipant.runByTrainingProvider` (field only on Session). Now gated: attach only to models that DECLARE the field. The `guard_uncovered` predicate now names the model that declares the field (`users.employerOrganisation`, not `credentials.…`). Locked with a new test invariant: no candidate names a field its model doesn't declare.
- **scoped_roles silent-drop (the #1 genericity blocker):** on a non-ICS repo the ICS-defaulted `scoped_roles` matched 0 roles → IDOR analyzer silently skipped every role-gated route → false clean. coverage.py now emits `authz_scope ✗ ZERO` + a LOUD banner when scoped_roles ∩ observed-roles is empty. Verified on a blog fixture.
- **Coverage now covers enforcement/db_ops/ui_state** (was 4/8 facets; the silent-empty guards → false-cleared-IDOR hole).
- **root-monolith mis-target:** `dir="."` targeted non-existent server/client → false clean. detect.py now targets "." when the subdir is absent.
- Removed `client-join.py` double-extractor; 5 dead files → attic/; deduped node(); fixed stale docstrings; removed err.log.

STILL OPEN (confirmed, ranked; need the user's steer or are bigger bets):
1. guards `guardingRoles` consequent-only assumption → mislabels trailing default-deny / `!==` / else as partial coverage (latent FP if a 3rd scoped role added). Fix = real dominance / track denied-roles.
2. gwent gate detector misses `const {user}=req` destructuring → false CLIENT-SIDE-ONLY-AUTHZ on admin-gated routes (getAllParticipants). + controllerGated has no dominance (behavior-branch = gate, latent FP).
3. attendeesList → wrong ENTITY (`users.*` on a session route) via db-layer 2-hop query gap → falls back to read-model.
4. Fact store still decorative (~22 re-spawns) → the read-through cache (M) or DuckDB (L).
5. guards file-wide FORBID heuristic still no dominance (the M enclosure fix, or CodeQL for the enforcement facet).
6. user_root only 2-level (ctx.state.user breaks); guards alias hardcoded to req.user. router v6 `<Route element={<Guard>}>` / useRoutes. body-param IDs. entities coverage via owner_links not model_names. auth_context/tests_authz facets have no probe.

## GUARDS DOMINANCE REWORK (collapses skeptic items 1, 2, 5) — done, 34/34 green
Replaced the file-wide `FORBID` heuristic (the 55%-FP mechanism: "a tenant compare AND a forbidding word both exist somewhere in the file") with a real ENCLOSURE check: a comparison counts as an ownership guard only if it IS the test of (or feeds a var tested by) an `if` whose FAIL branch forbids/returns. Handles `if (resource !== user.attr) return forbid`, the `let isAllowed = arr.some(x=>cmp); if(!isAllowed) forbid` role-switch (skips callback nesting to find the assignment), and membership guards. Verified: an incidental tenant compare + an unrelated "forbidden" string now → UNGUARDED (was falsely cleared). ICS: finaliseSession/updateEmployerOrganisation still cleared, 3 live IDORs still HIGH (13/10/8), determinism holds. Controllers call query WRAPPERS not mongoose sinks, so the gating-`if` is the signal (no sink-dominance needed).
