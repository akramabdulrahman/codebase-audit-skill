#!/usr/bin/env python3
"""DETERMINISTIC ACTOR MODEL -> IDOR proof pipeline (no LLM).
Joins the authoritative backend actor map with per-handler ownership enforcement and the ORM graph:
  backend-actors.js : route -> {auth, roles, controller}          (full mount-tree coverage)
  guards.js         : controller -> {hasOwnershipGuard, comparedFields, selfId}
  mongoose-schema.js: entity owner-link graph                     (names the MISSING predicate)
IDOR candidate = a route that MUTATES an object by :id, is reachable without a scoping role gate,
whose handler applies NO ownership guard and does NOT act on the caller's own identity.
Confidence rises when the predicate is named (ORM link) and when the frontend can't reach it (backend-only).
Usage: actor-model.py <serverDir> [<clientDir>]"""
import json, os, re, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
server = os.path.abspath(sys.argv[1])
client = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None
from _lib import spawn as node, url_norm, ctrl_key as gkey, load_knobs   # shared helpers (one definition; no drift)

actors = node("backend-actors.js", server)["routes"]
guards = {g["file"]: g for g in (node("guards.js", server) or [])}
orm = node("mongoose-schema.js", server)

# ---- ORM graph: name the missing ownership predicate per (entity, tenant attr) ----
def norm(name, names):
    n = (name or "").lower().rstrip("s")
    for m in names:
        if m.lower().rstrip("s") == n: return m
    return name
names = orm.get("model_names", [])
graph = defaultdict(list)
model_fields = defaultdict(set)   # model -> {declared owner-link field names} — to reject unsound predicates
for l in orm.get("owner_links", []):
    graph[l["model"]].append((l["field"], norm(l["ref"], names), l["isArray"]))
    model_fields[norm(l["model"], names)].add(l["field"])
USER = norm("users", names)
# attr_target: derive the tenant-attr -> ref-model map from the User model's OWN owner-links (not hardcoded ICS attrs)
attr_target = {"_id": USER}
for f, ref, arr in graph.get(USER, []): attr_target[f] = ref
SCOPED = load_knobs().get("scoped_roles") or {"programmeManager": "programmesCanDeliver", "tpOrgAdmin": "tpOrganisation", "employerOrgAdmin": "employerOrganisation", "trainer": "_id"}
def link_path(entity, target, maxh=3):
    if not target: return None
    best = [None]
    def dfs(m, trail, h):
        if h > maxh: return
        for f, ref, arr in graph.get(m, []):
            step = f + "[]" if arr else f
            if ref == target and (best[0] is None or h < best[0][1]): best[0] = (".".join(trail + [step]), h)
            if ref in graph and ref != m: dfs(ref, trail + [step], h + 1)
    dfs(entity, [], 1)
    return best[0][0] if best[0] else None

# db-layer: the mutating Model.op sites (also the ENTITY oracle — what the controller actually writes)
db = (node("db-layer.js", server) or {}).get("controllers", {})
def entities_of(k):
    # ALL models the controller mutates (a cascade-delete touches several) — normalized to graph keys.
    ms = db.get(k, {}).get("models", [])
    ws = [norm(o["model"], names) for o in ms if o.get("write")]
    return ws or [norm(o["model"], names) for o in ms]
def primary_entity(k):
    # the model this controller writes that a SIBLING proves a predicate for; else its first write model.
    ents = entities_of(k)
    return next((e for e in ents if confirmed.get(e)), (ents[0] if ents else None))

# SIBLING-ASYMMETRY: the ownership predicate PROVEN by handlers that DO check a model.
# guards.js already disambiguated the real compared field; prefer it over ORM shortest-path (mis-picks same-type links).
confirmed = defaultdict(dict)   # entity -> {attr: {field, loc, sibling}}  (what a sibling handler actually compares)
for k, g in guards.items():
    for ent in entities_of(k):   # attach the guard's comparison ONLY to models that DECLARE that field (sound predicate)
        for c in g.get("comparedFields", []):
            fld = c.get("field")
            if not fld or fld == "*" or c["attr"] in confirmed[ent]: continue
            if fld not in model_fields.get(ent, set()) and fld not in ("_id", "id"): continue   # reject field-on-wrong-model
            confirmed[ent][c["attr"]] = {"field": fld, "loc": c.get("loc"), "sibling": k.replace("controllers/", "")}

# frontend corroboration: which backend routes NO client thunk reaches (API-only).
# Computed from the SAME AST facets + the SHARED url_norm (was a drifted local that split only on '?').
backend_only = set()
if client:
    flows = node("client-flows.js", client) or {}
    client_called = {(c["method"], url_norm(c["url"])) for a in flows.get("frontendActors", []) for c in a.get("backendCalls", [])}
    for r in actors:
        if (r["method"], url_norm(r["path"])) not in client_called:
            backend_only.add((r["method"], re.sub(r":\w+", ":x", r["path"])))

MUT = {"POST", "PUT", "PATCH", "DELETE"}
OBJ = re.compile(r":(id|credId|sessionId|[A-Za-z]*Id|token|slug)\b")
cands = []
for r in actors:
    if r["method"] not in MUT: continue
    if not OBJ.search(r["path"] or ""): continue                 # must act on an object identified in the URL
    if r["auth"] in ("public", "open", "decide-role"): continue  # different bug class (unauth) — not IDOR
    # scoping roles present? if a scoped role is in allowedRoles, ownership still needed; if only admin, skip
    # effective roles = route-level allowedRoles, or (when the route is authed-any) the roles the CONTROLLER gates by
    eff_roles = set(r["roles"]) or set(r.get("controllerRoles", []))
    scoped = eff_roles & set(SCOPED)
    if r["auth"] == "roles" and not scoped: continue             # e.g. admin-only -> global by design
    k = gkey(r["controller"]); g = guards.get(k, {})
    # role-conditional coverage: clear only if the guard covers ALL scoped roles that reach the route.
    guard_uncovered = None
    if g.get("hasOwnershipGuard"):
        gr = g.get("guardRoles")                                 # None = covers all; [roles] = only these
        if gr is None or not scoped or scoped <= set(gr): continue   # fully covered -> cleared
        guard_uncovered = sorted(scoped - set(gr))               # role-conditional guard leaves these roles unchecked
    ent = primary_entity(k)
    writes = [o for o in db.get(k, {}).get("models", []) if o.get("write")]
    # provenance: the route reached + the unguarded mutation site(s) + the sibling that proves the predicate
    prov = [{"step": "reachable route", "loc": r.get("loc"),
             "detail": f"{'/'.join(sorted(scoped)) or r['auth']} reaches {r['method']} {r['path']} (auth={r['auth']})"}]
    for o in writes[:2]:
        prov.append({"step": "mutates, no guard", "loc": o["loc"], "detail": f"{o['model']}.{o['op']} — {k.replace('controllers/', '')} applies no ownership check"})
    if guard_uncovered:                                          # role-conditional guard: some roles are unchecked
        gattr = next((c["attr"] for c in g.get("comparedFields", []) if c.get("field")), None)
        gfield = next((c["field"] for c in g.get("comparedFields", []) if c.get("field")), None)
        # name the predicate with the model that DECLARES the compared field (not necessarily primary_entity) — sound
        gmodel = (next((m for m in entities_of(k) if gfield in model_fields.get(m, set())), None)
                  or next((m for m, fs in model_fields.items() if gfield in fs), ent)) if gfield else ent
        pred = f"{gmodel}.{gfield} == req.user.{gattr}" if (gfield and gattr) else None
        tier = "HIGH"; note = f"ownership guard covers only role(s) {g.get('guardRoles')}; role(s) {guard_uncovered} reach this route with NO ownership check"
        cf = next((c for c in g.get("comparedFields", []) if c.get("field")), {})
        if cf.get("loc"): prov.append({"step": "guard is role-conditional", "loc": cf["loc"],
                                       "detail": f"the ownership check runs only for {g.get('guardRoles')} — {guard_uncovered} bypass it"})
        cands.append({"tier": tier, "method": r["method"], "path": r["path"], "auth": r["auth"],
                      "controller": k.replace("controllers/", ""), "predicate": pred, "note": note, "prov": prov})
        continue
    if g.get("selfId"):                                          # acts on caller's own identity -> self-service, lower risk
        tier = "REVIEW"; note = "handler references caller identity (self-service?) — verify the id is the caller's own"; pred = None
    else:
        sib = confirmed.get(ent, {})
        if sib:                                                  # (1) STRONGEST: a sibling handler proves the predicate
            preds = [f"{ent}.{info['field']} == req.user.{attr}" for attr, info in sib.items()]
            pred = "; ".join(preds)
            # HIGH only when a SCOPED role can reach it (the tenant predicate applies). An any-authenticated route
            # could be self-service (the tenant predicate may not apply) -> MEDIUM, verify. (fixes confirm-email FP)
            reachable_scoped = (r["auth"] == "roles" and scoped)
            tier = "HIGH" if reachable_scoped else "MEDIUM"
            note = (f"siblings enforce {pred} — this handler does not" if tier == "HIGH"
                    else f"siblings enforce {pred}, but this route is any-authenticated — verify it isn't self-service before treating as cross-owner IDOR")
            for attr, info in sib.items():
                prov.append({"step": "sibling enforces", "loc": info.get("loc"),
                             "detail": f"{info['sibling']} checks {ent}.{info['field']} == req.user.{attr} — the predicate this handler omits"})
        else:                                                    # (2) fallback: ORM type-join (weaker)
            role = next(iter(sorted(scoped)), None)
            attr = SCOPED.get(role) if role else "tpOrganisation"
            p = link_path(ent, attr_target.get(attr)) if ent else None
            pred = f"{ent}.{p} == req.user.{attr}" if (ent and p) else None
            tier = "MEDIUM"
            note = f"missing (ORM-proposed, unconfirmed): {pred}" if pred else "mutates object by id, no ownership guard, entity/predicate unresolved"
            if pred: prov.append({"step": "ORM proposes", "loc": None, "detail": f"schema owner-link suggests {pred} (unconfirmed — no sibling compares it)"})
    corrob = " ✅ backend-only (no UI thunk reaches it)" if (r["method"], re.sub(r":\w+", ":x", r["path"])) in backend_only else ""
    cands.append({"tier": tier, "method": r["method"], "path": r["path"], "auth": r["auth"],
                  "controller": k.replace("controllers/", ""), "predicate": pred, "note": note + corrob, "prov": prov})

order = {"HIGH": 0, "MEDIUM": 1, "REVIEW": 2}
cands.sort(key=lambda c: (order[c["tier"]], c["method"], c["path"]))
if "--json" in sys.argv:
    print(json.dumps({"candidates": cands, "counts": {t: sum(1 for c in cands if c["tier"] == t) for t in order}}))
    sys.exit(0)
byauth = defaultdict(int)
for r in actors: byauth[r["auth"]] += 1

print("# DETERMINISTIC ACTOR MODEL — IDOR candidates (generator for the human/LLM gate)\n")
print(f"backend routes (full mount-tree): {len(actors)}  |  auth mix: {dict(byauth)}")
print(f"IDOR candidates (mutate object-by-id, reachable without scoping gate, no ownership guard): {len(cands)}\n")
for tier in ("HIGH", "MEDIUM", "REVIEW"):
    rows = [c for c in cands if c["tier"] == tier]
    desc = {"HIGH": "sibling-asymmetry: a sibling handler proves the predicate this one omits", "MEDIUM": "entity known, predicate unconfirmed (or query-level check this pass can't see)",
            "REVIEW": "self-service handler — confirm the id is the caller's own"}[tier]
    print(f"## {tier} ({len(rows)}) — {desc}")
    for c in rows:
        print(f"  {c['method']:6} {c['path']:44} [{c['auth']:10}] {c['controller']}\n         {c['note']}")
        for s in c["prov"]:
            if s.get("loc"): print(f"           · {s['step']:18} {s['detail'][:70]}  @ {s['loc']}")
    print()
print("Caveats: 'no ownership guard' = no alias-aware comparison of a caller tenant-attr found in the handler file; a")
print("query-level scope (filter.tpOrganisation) or cross-file check can still exist — the gate must confirm. Predicate")
print("names are proposals from the ORM type-join, not proof. This is a CANDIDATE list, not a verdict.")
