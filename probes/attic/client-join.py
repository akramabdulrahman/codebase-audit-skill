#!/usr/bin/env python3
"""Join CLIENT thunk API calls (client-flows.js) to the BACKEND route table, reconstructing
sub-router mount prefixes so the match is on FULL paths. Emits the frontend-vs-backend diff:
  MATCHED       client thunk -> backend handler (the intended, UI-exercised surface)
  CLIENT-ONLY   client calls a path with no backend route (proxy/typo/dynamic/removed)
  BACKEND-ONLY  backend route no thunk calls  ->  API-only-reachable surface (over-exposed / forgotten / non-UI)
Also attributes ROLE to matched routes via route.component -> componentThunks -> thunk urls.
Usage: client-join.py <clientDir> <serverDir>"""
import json, os, re, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
clientDir, serverDir = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
flows = json.loads(subprocess.run(["node", os.path.join(HERE, "client-flows.js"), clientDir],
                                  capture_output=True, text=True).stdout or "{}")

def norm(p):
    p = (p or "").split("?")[0].split("&")[0]     # drop query string (?type=..&x=..)
    p = re.sub(r":\w+", ":x", p)                  # :id / :sessionId -> :x
    p = re.sub(r"/+", "/", p).rstrip("/")
    return p.lower() or "/"
MTH = r"(get|post|put|patch|delete)"

# ---- backend route table with reconstructed prefixes ----
idx = os.path.join(serverDir, "router", "index.js")
src = open(idx, encoding="utf-8", errors="ignore").read()
# const NAME = require('PATH')
req = dict(re.findall(r"const\s+(\w+)\s*=\s*require\('([^']+)'\)", src))
# router.use('/prefix', NAME)  and  router.use(NAME)
mounts = []  # (prefix, name)
for m in re.finditer(r"\.use\(\s*(?:'([^']+)'\s*,\s*)?(\w+)\s*\)", src):
    mounts.append((m.group(1) or "", m.group(2)))

backend = {}  # (method, normpath) -> source
def add(method, path, source):
    backend[(method.upper(), norm(path))] = source
# direct routes registered in index.js itself
for m in re.finditer(r"router\.%s\(\s*['\"`]([^'\"`]+)" % MTH, src):
    add(m.group(1), "/api" + m.group(2), "router/index.js")
# mounted sub-routers: read each file, prefix its route defs
for prefix, name in mounts:
    rel = req.get(name)
    if not rel: continue
    f = os.path.normpath(os.path.join(serverDir, "router", rel))
    for cand in (f, f + ".js", os.path.join(f, "index.js")):
        if os.path.isfile(cand):
            sub = open(cand, encoding="utf-8", errors="ignore").read()
            for mm in re.finditer(r"\.%s\(\s*['\"`]([^'\"`]+)" % MTH, sub):
                add(mm.group(1), "/api" + prefix + mm.group(2), os.path.relpath(cand, serverDir))
            break

# ---- client call set (thunk -> calls) ----
thunk_calls = {}   # thunkName -> [(method, normurl, rawurl)]
client_set = {}    # (method, normurl) -> [thunkNames]
for t in flows.get("thunks", []):
    for c in t["calls"]:
        key = (c["method"], norm(c["url"]))
        thunk_calls.setdefault(t["name"], []).append((c["method"], norm(c["url"]), c["url"]))
        client_set.setdefault(key, []).append(t["name"])

# ---- role attribution: route.component -> bound thunks -> their calls ----
comp_thunks = defaultdict(set)
for b in flows.get("componentThunks", []):
    if b.get("component"):
        comp_thunks[b["component"]].update(b["thunks"])
route_of_call = defaultdict(set)   # (method,normurl) -> roles that can reach it
for r in flows.get("routes", []):
    roles = r.get("roles") or (["<any-authenticated>"] if not r.get("flags") else [])
    for th in comp_thunks.get(r.get("component"), []):
        for (m, nu, _raw) in thunk_calls.get(th, []):
            route_of_call[(m, nu)].update(roles)

matched = sorted(k for k in client_set if k in backend)
client_only = sorted(k for k in client_set if k not in backend)
backend_only = sorted(k for k in backend if k not in client_set)

print("# CLIENT ↔ BACKEND route join (deterministic)\n")
print(f"backend routes reconstructed: {len(backend)} (index.js direct + {len(mounts)} mounts, prefixes resolved)")
print(f"client thunk call-targets: {len(client_set)}  ({len(flows.get('thunks',[]))} thunks)\n")

print(f"## MATCHED ({len(matched)}) — UI-exercised backend surface (thunk → handler), with reachable roles")
for m, p in matched:
    roles = sorted(route_of_call.get((m, p), []))
    who = ",".join(roles) if roles else "?"
    print(f"  {m:6} {p:45} via {','.join(client_set[(m,p)][:3]):30} roles: {who}")

print(f"\n## BACKEND-ONLY ({len(backend_only)}) — NO thunk calls these → API-only-reachable (over-exposed / forgotten / non-UI). Audit these.")
for m, p in backend_only:
    print(f"  {m:6} {p:45} [{backend[(m,p)]}]")

print(f"\n## CLIENT-ONLY ({len(client_only)}) — thunk calls a path with no matched backend route (dynamic url / proxy / typo / removed)")
for m, p in client_only:
    print(f"  {m:6} {p:45} via {','.join(client_set[(m,p)][:3])}")

print("\nCaveats: backend paths reconstructed by static mount-prefix join (nested/regex mounts, param-name mismatch, and dynamically-built client URLs can mis-match — verify before acting). Role attribution covers connect()-bound thunks only.")
