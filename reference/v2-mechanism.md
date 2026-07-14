# v2 — Deterministic actor-model engine (mechanism · tools · strategies)

The v2 engine builds an **evidence-grade actor model** — who (role) can do what (action → API → DB → state)
— entirely deterministically (AST / ORM / file reads, **no LLM**), then generates a ranked **candidate** list
of authorization/logic risks. It is the deterministic front-half that feeds the human/LLM gate in `SKILL.md`
(Stage 1 actor-model verify, Stage 2 triage). **It generates candidates; it never returns a verdict.**

Run it: `python3 probes/run.py <repo>` → `.audit/{stack.json, facts/*.json, analysis/*.txt, report.html}`.

---

## 1. Mechanism — facets + set-algebra
Audit intelligence = **join / intersect / diff over independent facets** a codebase already exposes (prior art:
Code Property Graph/Joern, CodeQL/Datalog, Software Reflexion Models). No single scan is authoritative; the
signal is in cross-referencing sources produced independently.

A **facet** is a normalized relation with a fixed schema (`reference/facets.md`). **Probes** extract facets
(stack-specific). **Analyzers** join facets (stack-agnostic — never edited to add a stack). The two actor
models are built **separately** and then intersected:
- **Backend model** = who the server *enforces* (`server_routes`: route → allowedRoles → controller).
- **Client model** = who the UI *exposes* it to (`ui_flows`: role → page → thunk → API call).
- **Intersection** = agreement (truth) vs conflict (bug), and the client consumer context *disambiguates* the
  backend's ambiguous ownership dimension.

---

## 2. Tools — the probes (facet extractors) and analyzers (facet joiners)
Registry with per-stack config knobs: `stacks/capabilities.yaml`. Contract: `reference/facets.md`.

### Probes (deterministic, stack-specific; read `AUDIT_CONFIG` knobs)
| Probe | Facet | Extracts (ICS numbers) |
|---|---|---|
| `backend-actors.js` | `server_routes` | full mount-tree walk; `authentication({allowedRoles})` + **role-set constants resolved** → 128 routes, auth-typed |
| `mongoose-schema.js` | `entities` | ORM owner-link graph → 27 models, 53 edges |
| `guards.js` | `enforcement` | alias/flow-aware ownership-guard detection + the **compared field** (disambiguates `runByTrainingProvider~tpOrganisation`) |
| `db-layer.js` | `db_ops` | controller → **query-wrapper** → `Model.op` (the layer that breaks taint) → 166 query fns, blast radius |
| `client-flows.js` | `ui_routes`+`ui_actions` | route path-consts + `connect`-bound thunks + thunk→axios → 83 routes, role→page→call |
| `redux-state.js` | `ui_state` | `combineReducers` slice tree + thunk→action-type→slice (import-resolved) → 33 slices |

### Analyzers (deterministic, stack-agnostic; consume facets)
| Analyzer | Joins | Emits |
|---|---|---|
| `intersect.py` | server_routes ⋂ ui_flows | **CLIENT-SIDE-ONLY-AUTHZ** (client-only role gate = direct-API bypass), FRONTEND/BACKEND-BROADER, BACKEND-ONLY, AGREEMENT |
| `actor-model.py` | server_routes × enforcement × entities | IDOR candidates (HIGH/MEDIUM/REVIEW) with **sibling-asymmetry predicate** |
| `role-paths.py` | all 5 facets | per-role full-stack **functional requirements**: page → API → controller → `Model.op` → state |
| `report.py` | all | self-contained themed HTML + `payload.json` (every stage serialized) |

---

## 3. Strategies (why it's trustworthy, and where it isn't)
1. **Candidate generator + human gate.** Deterministic extraction proposes and *names* candidates; a human/LLM
   confirms. (An earlier "deterministic replaces the LLM" framing was disproven at 55% FP — the gate is essential.)
2. **Evidence ladder.** `hypothesis` → `probable` (complete static trace) → `verified` (executed red test). Nothing
   is a bug without a repro (SKILL.md Stage 4).
3. **Sibling-asymmetry** (RoleCast-style): the strongest, most defensible IDOR signal — a handler that mutates
   entity E but omits the ownership predicate its *sibling* handlers of E enforce. Names the predicate from the
   field a sibling actually compares (not ORM shortest-path, which mis-picks same-type links).
4. **Consumer-context disambiguation.** When the backend can't tell which tenant dimension scopes a route, the
   *client consumer roles* pick it (only `programmeManager` pages call `PATCH /modules/:id` → scope = `programmesCanDeliver`).
5. **The two-model intersection** is the oracle: `frontend-exposed − backend-enforced`. A role-gate present only
   on the client = a direct-API bypass — provable with no LLM.
6. **The CommonJS taint ceiling → the frontend pivot.** Hand-rolled Babel *and* Joern both fail on
   `controller → query-wrapper → mongoose` interprocedural taint on CommonJS. The **frontend is ESM + explicit
   dispatch names**, so its call graph *is* statically resolvable — which is why the client model + intersection
   carry the authz signal the backend alone can't prove.

---

## 4. Extensibility — teach a new stack
The normalized facet is the contract; probes are swappable implementations. To add a stack:
1. Write probes that emit the same facet shapes (`reference/facets.md`) for it.
2. Register them in `stacks/capabilities.yaml` (facet → probe → knobs) with a `swap_for_stack` note.
3. Add an `extract:` block + detection signature: `stacks/<id>.yaml` + a `SIGNATURES` entry in `detect.py`.
Analyzers, `run.py`, and the report **do not change**. Swap examples encoded: NestJS `@Roles` decorators,
Prisma schema, RTK Query `createApi`, Django `permission_classes`, Rails `before_action`.

Config knobs (auth-middleware name, role-array key, query-dir, http-client names, thunk dir, route-guard
component, tenant-attrs, user-root, `scoped_roles` map) live in the stack profile, merged by `detect.py` →
`.audit/stack.json` → read via `AUDIT_CONFIG`. `backend-actors`, `client-flows`, `db-layer`, `redux-state`,
`guards`, and `mongoose-schema` read their knobs (verified: overriding `auth_fn` collapses all routes to `open`).

> ⚠️ **Known gap (do not overstate):** `actor-model.py` still hardcodes ICS-specific `DIR_ENTITY` (controller-dir →
> entity) and `attr_target` maps, and `scoped_roles` is not auto-inferred (defaults to the ICS map). On a repo
> whose dirs/roles differ, the sibling-asymmetry engine degrades to MEDIUM/empty. Tracked in V2-STATUS as P0-b/P2.
> Earlier drafts claimed "zero project values hardcoded" — that was false for the authz analyzers; corrected here.

---

## 5. Verified on ICS (autonomous verification pass)
- **Determinism:** all 9 probes/analyzers byte-identical across 3 runs; config-driven == hardcoded output.
- **Correctness:** 3 live-verified IDORs → actor-model HIGH; reference impls (`finaliseSession`,
  `updateEmployerOrganisation`) cleared; the verification pass itself found & fixed 2 real bugs
  (controller join-key mismatch → 1 FP; unresolved role-set constant → 14 misclassified routes).
- **Corrected numbers:** 128 routes (69 roles / 32 authed-any / 14 public / 11 open / 2 decide-role);
  intersect 13 client-side-only-authz / 8 frontend-broader / 3 backend-broader / 74 backend-only / 30 agreement;
  IDOR 15 HIGH / 6 MEDIUM / 8 REVIEW; per-role 19–37 capabilities.

## 6. Honest limits (carry these to the gate)
- **Frontend role = page granularity** (upper bound): a role-gated button's thunk inherits the whole page's
  roles. CLIENT-SIDE-ONLY-AUTHZ *class* is sound (backend auth is a hard fact); the exact role list is not.
- **Body-param object ids** (`userId` from body, no `:id` in path, e.g. `updateTrainerCoursesSet`) are outside
  the by-`:id` IDOR list — caught only as authed-any/backend-only, not predicate-named.
- **Query-level scoping** (`filter.tpOrganisation` inside a query fn) can enforce ownership invisibly to
  `guards.js` → a MEDIUM candidate may be a false positive; the gate must confirm.
- Child-component dispatches + `useDispatch` sites (4 on ICS) not followed; `connect` object-shorthand covers 67/99.
