# probes/ — the v2 deterministic extraction + analysis layer

One command: **`python3 probes/run.py <repo>`** → detect stack → extract facets → analyze → `report.html`.
No LLM. Output is a **candidate list** for the human/LLM gate, not a verdict. Full write-up: `../reference/v2-mechanism.md`.
Facet schemas: `../reference/facets.md`. Probe→stack→knob registry: `../stacks/capabilities.yaml`.

## Driver & detection
- `run.py` — the pipeline (detect → 6 facet probes → 3 analyzers → report).
- `detect.py` — sniff repo deps/files → pick backend+frontend stack profiles → merge knobs → `.audit/stack.json` (the `AUDIT_CONFIG` every probe reads). **No project value is hardcoded in probe code.**

## Probes (facet extractors — stack-specific, read `AUDIT_CONFIG` knobs)
| File | Facet | One line |
|---|---|---|
| `backend-actors.js` | `server_routes` | full mount-tree walk; resolves `authentication({allowedRoles})` incl. role-set constants → route→roles→controller |
| `mongoose-schema.js` | `entities` | ORM owner-link graph (model → owner refs) |
| `guards.js` | `enforcement` | alias/flow-aware ownership-guard detection + the compared field |
| `db-layer.js` | `db_ops` | controller → query-wrapper → `Model.op` (through the layer that breaks taint) |
| `client-flows.js` | `ui_routes`+`ui_actions` | route path-consts + `connect`-bound thunks + thunk→axios; also the client actor model |
| `redux-state.js` | `ui_state` | `combineReducers` slice tree + thunk→action-type→slice |

## Analyzers (facet joiners — stack-agnostic, never edited to add a stack)
| File | Emits |
|---|---|
| `intersect.py` | backend⋂client authz classes: CLIENT-SIDE-ONLY-AUTHZ (bypass), FRONTEND/BACKEND-BROADER, BACKEND-ONLY, AGREEMENT |
| `actor-model.py` | IDOR candidates HIGH/MEDIUM/REVIEW with sibling-asymmetry predicate |
| `role-paths.py` | per-role full-stack functional requirements (action⇒DB⇒back) |
| `deps-triage.py` | ranks `npm audit` advisories by EXPLOITABILITY: severity × direct × runtime-not-dev × imported × fix-available → URGENT/REVIEW/LOW (reads auditctl-scan raws for EVERY manifest) |
| `report.py` (+ `report.template.html`) | self-contained themed HTML + `payload.json` (all stages serialized) |

`run.py` also runs `auditctl scan` (audits every `package.json` with a lockfile — not just `server/`) then `deps-triage`. **Caveat:** `npm audit` hits the advisory DB over the network, so the dependency step is NOT deterministic (counts drift as advisories publish) — unlike the AST/ORM probes, which are byte-stable.

## Legacy / superseded
- `client-join.py` — first-cut URL↔route join; superseded by `intersect.py` (kept for the backend-only diff view).
- `extract.py`, `derive_predicates.py`, `query.py` — v2-MVP SQLite fact store + graph type-join; superseded by
  `actor-model.py` (sibling-asymmetry beats shortest-path). Kept for reference; see `V2-STATUS.md` for the eval history.

## Notes
- All probes/analyzers are **deterministic** (verified: byte-identical across 3 runs).
- Node probes need `@babel/parser` + `@babel/traverse` in the target repo's `node_modules` (they already ship in most JS repos).
- Status/eval history: `V2-STATUS.md`.
