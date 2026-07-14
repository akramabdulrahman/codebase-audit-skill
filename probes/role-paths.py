#!/usr/bin/env python3
"""FULL ROLE PATHS — stitch the 4 layers into per-role functional requirements (action => DB => back).
  client-flows.js  : role -> page -> thunk -> (method,url)      [what the role triggers]
  backend-actors.js: (method,url) -> route(auth,roles) -> controller
  db-layer.js      : controller -> Model.operation              [the DB touch, through the query wrapper]
  redux-state.js   : thunk -> state slice                       [the "and back"]
Output: for each ROLE, every capability as one end-to-end path:
  page ──dispatch(thunk)──▶ METHOD url ──▶ controller ──▶ [Model.op…] ──▶ state:[slice…]   {authz}
Usage: role-paths.py <serverDir> <clientDir> [--role <name>]"""
import json, os, re, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
server, client = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
want = sys.argv[sys.argv.index("--role") + 1] if "--role" in sys.argv else None
from _lib import spawn as node, url_norm as norm, ctrl_key   # shared helpers (one definition; no drift)

flows = node("client-flows.js", client)
backend = node("backend-actors.js", server)["routes"]
db = node("db-layer.js", server)["controllers"]
state = node("redux-state.js", client)

route_map = {(r["method"], norm(r["path"])): r for r in backend}
thunk_slices = state.get("thunkSlices", {})

# role -> list of capability paths
byrole = defaultdict(list)
for a in flows["frontendActors"]:
    if not a["roles"] or not a["backendCalls"]: continue
    for c in a["backendCalls"]:
        r = route_map.get((c["method"], norm(c["url"])))
        controller = ctrl_key(r["controller"]) if r else None
        dbo = db.get(controller, {}).get("models", []) if controller else []
        dbs = ", ".join(f"{o['model']}.{o['op']}{'*' if o['write'] else ''}" for o in dbo) or "—"
        slices = ", ".join(thunk_slices.get(c["thunk"], [])) or "—"
        auth = r["auth"] if r else "?"
        broles = ",".join(r["roles"]) if r and r["roles"] else auth
        writes = any(o["write"] for o in dbo)
        cap = {
            "page": a["component"], "thunk": c["thunk"], "method": c["method"], "url": c["url"],
            "controller": controller.replace("controllers/", "") if controller else "?",
            "db": dbs, "slices": slices, "auth": auth, "broles": broles, "writes": writes,
        }
        for role in a["roles"]:
            byrole[role].append(cap)

roles = [want] if want else sorted(byrole)
for role in roles:
    caps = byrole.get(role, [])
    # dedup by (thunk,url)
    seen = {};
    for c in caps: seen[(c["thunk"], c["method"], c["url"])] = c
    caps = sorted(seen.values(), key=lambda c: (not c["writes"], c["url"]))
    writes = [c for c in caps if c["writes"]]
    print(f"\n{'='*100}\nROLE: {role}   —   {len(caps)} capabilities ({len(writes)} write the DB)\n{'='*100}")
    for c in caps:
        w = "✍ " if c["writes"] else "  "
        print(f" {w}{c['page']} ─▶ {c['method']} {c['url']}")
        print(f"     thunk {c['thunk']}  →  {c['controller']}  [{c['broles']}]")
        print(f"     DB: {c['db']}")
        print(f"     state: {c['slices']}")
print(f"\n{'-'*100}")
print("Notes: DB touch resolved controller→query-fn→Model.op (through the wrapper layer). '*' = write, '✍' = path mutates DB.")
print("[roles] on a path = the SERVER's allowedRoles for that route ('authed-any'/'open' = server does NOT gate by role →")
print("if this role reaches it only via the UI, the gate is client-side — see intersect.py CLIENT-SIDE-ONLY-AUTHZ).")
print("Frontend role = page-level (upper bound); slice attribution is heuristic. This is the functional-requirement map, not a verdict.")
