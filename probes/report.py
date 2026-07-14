#!/usr/bin/env python3
"""ASSEMBLE the audit payload — runs all extractors + analyzers, serializes EVERY stage into one JSON
(role paths, intersection classes, backend auth map, DB blast radius) and renders a self-contained HTML report.
Usage: report.py <serverDir> <clientDir> [--out <report.html>]"""
import json, os, re, subprocess, sys, html

HERE = os.path.dirname(os.path.abspath(__file__))
server, client = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(os.path.dirname(server), "audit-report.html")
from _lib import spawn as node, url_norm as norm, ctrl_key   # shared helpers (one definition; no drift)

backend = node("backend-actors.js", server)["routes"]
flows = node("client-flows.js", client)
db = node("db-layer.js", server)["controllers"]
state = node("redux-state.js", client)
actors = flows["frontendActors"]

route_map = {(r["method"], norm(r["path"])): r for r in backend}
thunk_slices = state.get("thunkSlices", {})

# ---------- per-role capabilities (role-paths logic) ----------
from collections import defaultdict
byrole = defaultdict(dict)
for a in actors:
    if not a["roles"] or not a["backendCalls"]: continue
    for c in a["backendCalls"]:
        r = route_map.get((c["method"], norm(c["url"])))
        controller = ctrl_key(r["controller"]) if r else None
        dbo = db.get(controller, {}).get("models", []) if controller else []
        prov = [
            {"step": "UI route", "loc": a.get("loc"), "detail": f"<{a['component']}> allowedRoles={a['roles']}"},
            {"step": "thunk → API", "loc": c.get("loc"), "detail": f"{c['thunk']}() → {c['method']} {c['url']}"},
            {"step": "server route", "loc": (r or {}).get("loc"), "detail": f"auth={r['auth']} roles={r['roles'] or '—'}" if r else "no matching server route"},
        ] + [{"step": "controller → DB", "loc": o["loc"], "detail": f"{o['model']}.{o['op']}" + ("*  (write)" if o["write"] else "")} for o in dbo]
        cap = {
            "page": a["component"], "thunk": c["thunk"], "method": c["method"], "url": c["url"],
            "controller": (controller or "?").replace("controllers/", ""),
            "db": [f"{o['model']}.{o['op']}" + ("*" if o["write"] else "") for o in dbo],
            "slices": thunk_slices.get(c["thunk"], []),
            "auth": r["auth"] if r else "?",
            "broles": (r["roles"] if r and r["roles"] else []),
            "writes": any(o["write"] for o in dbo),
            "prov": prov,
        }
        for role in a["roles"]:
            byrole[role][(c["thunk"], c["method"], c["url"])] = cap
roles = {role: sorted(caps.values(), key=lambda c: (not c["writes"], c["url"])) for role, caps in byrole.items()}

# ---------- intersection classes (intersect logic) ----------
client_map = defaultdict(lambda: {"roles": set(), "pages": set(), "ev": []})
for a in actors:
    for c in a["backendCalls"]:
        k = (c["method"], norm(c["url"]))
        client_map[k]["roles"].update(a["roles"] or [])
        if a.get("component"): client_map[k]["pages"].add(a["component"])
        client_map[k]["ev"].append({"page": a.get("component"), "page_loc": a.get("loc"),
                                    "thunk": c.get("thunk"), "call_loc": c.get("loc"), "roles": a["roles"]})
ALLROLES = set()
for r in backend: ALLROLES.update(r["roles"])
for v in client_map.values(): ALLROLES.update(v["roles"])
inter = defaultdict(list)
for r in backend:
    k = (r["method"], norm(r["path"])); cm = client_map.get(k)
    fe = cm["roles"] if cm else set(); be = set(r["roles"]); auth = r["auth"]
    if not cm: cls = "BACKEND-ONLY"
    elif auth in ("authed-any", "open") and fe and fe < ALLROLES: cls = "CLIENT-SIDE-ONLY-AUTHZ"
    elif auth == "roles" and be == fe: cls = "AGREEMENT"
    elif auth == "roles" and be > fe: cls = "BACKEND-BROADER"
    elif auth == "roles" and fe - be: cls = "FRONTEND-BROADER"
    else: cls = "AGREEMENT"
    controller = ctrl_key(r["controller"])
    dbo = db.get(controller, {}).get("models", []) if controller else []
    ev = (cm["ev"] if cm else [])
    seen_ev, ev_uniq = set(), []
    for e in ev:
        key = (e["page"], e["thunk"])
        if key in seen_ev: continue
        seen_ev.add(key); ev_uniq.append(e)
    prov = [{"step": "server route", "loc": r.get("loc"), "detail": f"auth={auth} roles={sorted(be) or '—'}"}]
    for e in ev_uniq[:3]:
        prov.append({"step": "UI exposes", "loc": e["page_loc"], "detail": f"<{e['page']}> allowedRoles={e['roles']}"})
        prov.append({"step": "via thunk", "loc": e["call_loc"], "detail": f"{e['thunk']}() → {r['method']} {r['path']}"})
    prov += [{"step": "controller → DB", "loc": o["loc"], "detail": f"{o['model']}.{o['op']}" + ("*  (write)" if o["write"] else "")} for o in dbo[:4]]
    inter[cls].append({"method": r["method"], "path": r["path"], "auth": auth,
                       "backend": sorted(be), "frontend": sorted(fe), "pages": sorted(cm["pages"]) if cm else [],
                       "writes": any(o["write"] for o in dbo), "controller": (controller or "?").replace("controllers/", ""),
                       "prov": prov})

auth_mix = defaultdict(int)
for r in backend: auth_mix[r["auth"]] += 1
# IDOR candidates (sibling-asymmetry) with provenance — from the actor-model analyzer
idor = json.loads(subprocess.run(["python3", os.path.join(HERE, "actor-model.py"), server, client, "--json"],
                                 capture_output=True, text=True).stdout or "{}")
coverage = {}
try: coverage = json.load(open(os.path.join(os.path.dirname(out), "coverage.json")))
except Exception: pass
payload = {
    "project": os.path.basename(os.path.dirname(server)) or "project",
    "coverage": coverage,
    "stats": {"backend_routes": len(backend), "thunks": len(flows.get("thunks", [])),
              "ui_routes": flows["stats"]["routes"], "db_query_fns": node("db-layer.js", server)["stats"]["queryFns"],
              "state_slices": state["stats"]["slices"], "roles": len(roles)},
    "auth_mix": dict(auth_mix),
    "roles": roles,
    "idor": idor.get("candidates", []),
    "idor_counts": idor.get("counts", {}),
    "intersect": {k: inter[k] for k in ["CLIENT-SIDE-ONLY-AUTHZ", "FRONTEND-BROADER", "BACKEND-BROADER", "BACKEND-ONLY", "AGREEMENT"]},
    "intersect_counts": {k: len(inter[k]) for k in inter},
}
json.dump(payload, open(os.path.splitext(out)[0] + ".json", "w"), indent=1)

# ---------- render (self-contained, theme-aware) ----------
TEMPLATE = open(os.path.join(HERE, "report.template.html")).read()
DATA = json.dumps(payload).replace("</", "<\\/")
open(out, "w").write(TEMPLATE.replace("__DATA__", DATA))
print(f"payload: {os.path.splitext(out)[0]}.json")
print(f"report:  {out}")
print(f"stats: {payload['stats']}  auth_mix: {payload['auth_mix']}")
print(f"intersect: {payload['intersect_counts']}")
