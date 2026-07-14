#!/usr/bin/env python3
"""INTERSECT two independently-built actor models -> conflicts / truths (no LLM).
  BACKEND model  (backend-actors.js): route -> {auth, allowedRoles}          = who the server ENFORCES
  CLIENT  model  (client-flows.js)  : route -> {frontendRoles, pages, thunk} = who the UI EXPOSES it to
Set-algebra per backend route:
  AGREEMENT              backend allowedRoles == frontend roles                -> truth (intended surface)
  CLIENT-SIDE-ONLY AUTHZ backend authed-any/open BUT frontend restricts roles -> role gate is client-only = direct-API bypass
  BACKEND-BROADER       allowedRoles ⊋ frontend roles                         -> server allows roles no UI exposes (over-granted / hidden)
  FRONTEND-BROADER      frontend roles ⊋ allowedRoles                         -> UI offers it to roles server rejects (dead UI / drift)
  BACKEND-ONLY          no UI page reaches it                                 -> API-only surface (craft-a-request only)
Plus: CONSUMER-RESOLVES-AMBIGUITY — for a backend route whose ownership scope is ambiguous, the client
consumer roles pick the tenant dimension the backend type-join could not (the point of intersecting).
Usage: intersect.py <serverDir> <clientDir>"""
import json, os, re, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
server, client = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
from _lib import spawn as node, url_norm as norm, load_knobs   # shared helpers (one definition; no drift)

backend = node("backend-actors.js", server)["routes"]
flows = node("client-flows.js", client)
actors = flows["frontendActors"]

# CLIENT model: (method, normpath) -> {roles set, pages set}
client_map = defaultdict(lambda: {"roles": set(), "pages": set()})
for a in actors:
    for c in a["backendCalls"]:
        k = (c["method"], norm(c["url"]))
        client_map[k]["roles"].update(a["roles"] or [])
        if a.get("component"): client_map[k]["pages"].add(a["component"])

_KNOBS = load_knobs()

SCOPED = _KNOBS.get("scoped_roles") or {"programmeManager": "programmesCanDeliver", "tpOrgAdmin": "tpOrganisation",
          "employerOrgAdmin": "employerOrganisation", "trainer": "_id"}
MUT = {"POST", "PUT", "PATCH", "DELETE"}
# role universe: every role that appears anywhere (backend allowedRoles ∪ frontend PrivateRoute roles)
ALLROLES = set()
for r in backend: ALLROLES.update(r["roles"])
for v in client_map.values(): ALLROLES.update(v["roles"])
rows = []
for r in backend:
    k = (r["method"], norm(r["path"]))
    be_roles = set(r["roles"])
    cm = client_map.get(k)
    fe_roles = cm["roles"] if cm else set()
    pages = sorted(cm["pages"]) if cm else []
    auth = r["auth"]

    if not cm:
        cls = "BACKEND-ONLY"
    elif r.get("controllerGated"):
        cls = "AGREEMENT"                        # server DOES gate by role INSIDE the controller (not client-only) — kills the false bypass
    elif auth in ("authed-any", "open") and fe_roles and fe_roles < ALLROLES:
        cls = "CLIENT-SIDE-ONLY-AUTHZ"          # server = anyone; UI gate is a PROPER subset -> gate is client-only
    elif auth == "roles" and be_roles == fe_roles:
        cls = "AGREEMENT"
    elif auth == "roles" and be_roles > fe_roles:
        cls = "BACKEND-BROADER"
    elif auth == "roles" and fe_roles - be_roles:
        cls = "FRONTEND-BROADER"
    else:
        cls = "AGREEMENT"
    # consumer context resolves ownership dimension for mutations (the disambiguation)
    scope = ""
    if r["method"] in MUT and fe_roles:
        tight = sorted(fe_roles & set(SCOPED), key=lambda x: list(SCOPED).index(x))
        if tight: scope = f"  consumer-scope: {SCOPED[tight[0]]} (via {tight[0]})"
    rows.append((cls, r["method"], r["path"], auth, sorted(be_roles), sorted(fe_roles), pages, scope))

ORDER = ["CLIENT-SIDE-ONLY-AUTHZ", "FRONTEND-BROADER", "BACKEND-BROADER", "BACKEND-ONLY", "AGREEMENT"]
rows.sort(key=lambda x: (ORDER.index(x[0]), x[1], x[2]))
counts = defaultdict(int)
for x in rows: counts[x[0]] += 1

print("# ACTOR-MODEL INTERSECTION — backend(enforced) ∩ client(exposed)\n")
print(f"backend routes: {len(backend)}  |  client routes with calls: {len(client_map)}  |  classes: {dict(counts)}\n")
for cls in ORDER:
    sub = [x for x in rows if x[0] == cls]
    desc = {
        "CLIENT-SIDE-ONLY-AUTHZ": "server = any-authenticated, UI restricts to roles → role gate is CLIENT-ONLY = direct-API bypass. Highest signal.",
        "FRONTEND-BROADER": "UI exposes to roles the server rejects → dead UI or client/server drift.",
        "BACKEND-BROADER": "server allows roles no UI reaches → over-granted or non-UI consumer.",
        "BACKEND-ONLY": "no UI page reaches it → API-only (craft-a-request) surface.",
        "AGREEMENT": "backend roles == frontend roles → intended, corroborated surface.",
    }[cls]
    print(f"## {cls} ({len(sub)}) — {desc}")
    for _, m, p, auth, be, fe, pages, scope in sub:
        if cls == "AGREEMENT":
            print(f"  {m:6} {p:44} roles={be}")
        else:
            print(f"  {m:6} {p:44} [{auth}]  backend={be or '—'}  frontend={fe or '—'}  pages={pages[:3]}{scope}")
    print()
print("Caveats: client roles come from connect()-bound thunks in the page file (child-component dispatches & 4 useDispatch")
print("sites not yet followed); backend paths are static mount-tree joins. CLIENT-SIDE-ONLY-AUTHZ on a MUTATION acting on")
print("an :id is the IDOR/bypass shortlist — verify each against the handler + a live cross-role request.")
