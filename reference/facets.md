# Facet contract — the stack-independent interface

The audit is **set-algebra over normalized facets**. A *facet* is a normalized relation with a
fixed schema. **Probes** are stack-specific implementations that emit a facet; the **downstream
analyzers** (`intersect`, `actor-model`, `role-paths`) consume ONLY these facet schemas and never touch
source. To support a new stack you write probes that emit these same shapes — nothing downstream changes.

> ⚠️ **Implementation note (accuracy):** today the analyzers do NOT read `.audit/facts/*.json`; each one
> re-spawns the Node probes over subprocess and parses stdout (so a probe is re-run 4–5× per audit). The
> facet *schema* is the real contract, but the on-disk fact store is currently a snapshot, not the seam.
> Making analyzers read the fact store once (a SQLite/DuckDB load) is a tracked improvement — see V2-STATUS.

Two roles:
- **Probe** = extractor for one (facet, stack). Reads source, emits the facet JSON. Coupled to the stack.
- **Analyzer** = joins/diffs facets. Stack-agnostic. Never edited to add a stack.

`_ref` fields are opaque cross-facet join keys (a controller id, a component id) — any stable string;
analyzers only compare them for equality, they don't parse them.

## The facets (normalized schemas)

### `server_routes`  — who the server ENFORCES  (backend actor model)
```
{ method, path, roles:[role], auth:"roles"|"authed-any"|"public"|"open"|<custom>, handler_ref }
```
`auth` semantics: `roles`=role-gated · `authed-any`=any authenticated, no role gate · `public`=explicitly public · `open`=no auth middleware at all.

### `entities`  — the data model + ownership links  (ORM graph)
```
{ model, fields:[field], owner_links:[{ field, ref_model, is_array }] }
```

### `auth_context`  — the caller's tenant attributes
```
{ role, tenant_attrs:[{ attr, ref_model }] }   # e.g. tpOrgAdmin -> tpOrganisation:TPOrganisation
```

### `enforcement`  — per handler: does it self-check ownership
```
{ handler_ref, has_ownership_guard, compared:[{ field, attr }], acts_on_self }
```

### `db_ops`  — the DB touch  (handler -> entity.operation, through any wrapper layer)
```
{ handler_ref, ops:[{ model, op, write:bool }] }
```

### `ui_routes`  — who the client EXPOSES a page to
```
{ path, roles:[role], component_ref, flags:[flag] }
```

### `ui_actions`  — component -> the API calls it can trigger
```
{ component_ref, calls:[{ method, url, action_ref, in_redux:bool }] }
```
`in_redux:false` = an API call made outside the state layer (direct component/hook fetch).

### `ui_state`  — the "and back": an action -> the state slice it writes
```
{ action_ref, slices:[slice] }
```

### `tests_authz`  — intended authz from tests  (the spec)
```
{ role, method, path, expected_status, kind:"repro"|"char" }
```

## Join keys (how analyzers stitch facets)
- `server_routes.path/method  ⋈  ui_actions.calls.url/method`  → frontend-intended vs backend-enforced.
- `server_routes.handler_ref  ⋈  enforcement.handler_ref  ⋈  db_ops.handler_ref`  → route → guard → DB.
- `ui_routes.component_ref  ⋈  ui_actions.component_ref`  → role → page → calls.
- `ui_actions.action_ref  ⋈  ui_state.action_ref`  → action → state slice.
- `entities.owner_links  ⋈  auth_context.tenant_attrs` (by ref_model) → the ownership predicate.

Everything the analyzers produce (CLIENT-SIDE-ONLY-AUTHZ, sibling-asymmetry IDOR, per-role paths)
is a query over these joins. Add a stack = supply probes emitting these shapes for it.
