#!/usr/bin/env python3
"""FRAGILE FLOWS — mine the repo's OWN git history to find the flows the team keeps fixing, and join them to
the actor model. The most-fixed files are the fragile flows; a fragile flow that is ALSO an IDOR candidate is
the top priority. This reconnects the tool to its original goal: 'audit/test the flows that keep breaking',
not just 'here are N exploitable bugs'. Deterministic (git log + the facets). Usage: fragile-flows.py <server> [<client>]"""
import json, os, re, subprocess, sys
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
server = os.path.abspath(sys.argv[1])
client = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None
repo = server
while repo != "/" and not os.path.isdir(os.path.join(repo, ".git")): repo = os.path.dirname(repo)
def sh(args): return subprocess.run(args, cwd=repo, capture_output=True, text=True).stdout
def spawn(js, arg, env=None): return json.loads(subprocess.run(["node", os.path.join(HERE, js), arg], capture_output=True, text=True, env=env).stdout or "null")

# ---- 1. git churn: per file {commits, fixes, last_fix}; + co-change pairs within fix commits ----
FIX = re.compile(r"\b(fix|bug|hotfix|patch|revert|broken|incorrect|wrong|prevent|resolve|issue|regress)", re.I)
SRC = re.compile(r"\.(js|jsx|ts|tsx)$")
SKIP = re.compile(r"(__test__|\.test\.|\.spec\.|/data/|/seed/|node_modules|/dist/|/build/)")
log = sh(["git", "log", "--no-merges", "--since=3 years ago", "--date=short", "--format=@@@|%h|%ad|%s", "--name-only"])
commits = []
cur = None
for line in log.splitlines():
    if line.startswith("@@@|"):
        _, h, d, s = line.split("|", 3)
        cur = {"h": h, "date": d, "subj": s, "files": []}; commits.append(cur)
    elif line.strip() and cur is not None and SRC.search(line) and not SKIP.search(line):
        cur["files"].append(line.strip())
churn = defaultdict(lambda: {"commits": 0, "fixes": 0, "last_fix": ""})
cochange = Counter()
for c in commits:
    isfix = bool(FIX.search(c["subj"]))
    fs = [f for f in c["files"] if f.startswith(("server/", "client/", "src/")) or "/controllers/" in f or "/queries/" in f]
    for f in fs:
        churn[f]["commits"] += 1
        if isfix:
            churn[f]["fixes"] += 1
            if not churn[f]["last_fix"]: churn[f]["last_fix"] = c["date"]
    if isfix and 2 <= len(fs) <= 8:   # co-changed in a fix commit = coupled flow
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                cochange[tuple(sorted((fs[i], fs[j])))] += 1

# ---- 2. the actor model: route -> controller -> roles, + IDOR candidates, + query-file -> flow ----
env = {**os.environ}
routes = (spawn("backend-actors.js", server, env) or {}).get("routes", [])
dbf = spawn("db-layer.js", server, env) or {}
idor = json.loads(subprocess.run(["python3", os.path.join(HERE, "actor-model.py"), server] + ([client] if client else []) + ["--json"],
                                 capture_output=True, text=True, env=env).stdout or "{}").get("candidates", [])
idor_by_ctrl = {c["controller"]: c for c in idor}
def norm_ctrl(c):
    c = re.sub(r"^\.\.?/", "", c or "").lstrip("/")
    if not c.startswith("controllers"): c = "controllers/" + c
    return c if c.endswith(".js") else c + ".js"
srv_rel = os.path.relpath(server, repo)
ctrl_routes = defaultdict(list)                      # controller file (repo-rel) -> its routes
for r in routes:
    ctrl_routes[os.path.join(srv_rel, norm_ctrl(r.get("controller")))].append(r)
# query FILE -> controllers that use it (so a hot data-access file resolves to its flow/roles/IDOR)
qfn_file = {}                                        # queryFn -> repo-rel file (from db-layer op loc)
for fn, ops in dbf.get("queries", {}).items():
    if ops and ops[0].get("loc"): qfn_file[fn] = os.path.join(srv_rel, ops[0]["loc"].split(":")[0])
qfile_ctrls = defaultdict(set)                       # query file -> {controller files}
for cf, meta in dbf.get("controllers", {}).items():
    cf_rel = os.path.join(srv_rel, cf)
    for fn in meta.get("calls", []):
        if fn in qfn_file: qfile_ctrls[qfn_file[fn]].add(cf_rel)
def flow_of(f):                                      # a file -> (routes it powers, roles, IDOR candidate if any)
    ctrls = [f] if f in ctrl_routes else list(qfile_ctrls.get(f, []))
    rs = [rt for c in ctrls for rt in ctrl_routes.get(c, [])]
    roles = sorted({r for rt in rs for r in (rt.get("roles") or [])})
    idc = next((idor_by_ctrl[c] for c in idor_by_ctrl for cf in ctrls if cf.endswith(c)), None)
    return rs, roles, idc

# ---- 3. rank fragile flows: hot files, classified + joined to routes/roles/IDOR ----
def kind(f):
    if "/controllers/" in f: return "controller"
    if "/queries/" in f or "/query" in f: return "data-access"
    if "/models/" in f: return "model"
    if "/router" in f: return "router"
    if "/taskScheduler/" in f or "Scheduler" in f or "cron" in f.lower(): return "job"
    if "constants" in f: return "constants"
    return "other"
hot = sorted(churn.items(), key=lambda kv: (kv[1]["fixes"], kv[1]["commits"]), reverse=True)
hot = [(f, v) for f, v in hot if v["fixes"] >= 2][:25]

print("# FRAGILE FLOWS — what this codebase keeps fixing (mined from git history + the actor model)\n")
print(f"repo: {os.path.basename(repo)}  |  commits scanned: {len(commits)} (last 3y)  |  hot files (≥2 fixes): {len(hot)}\n")
print("## Ranked fragile flows (fix-count desc) — ⚠ = also an IDOR candidate (fragile AND insecure = top priority)")
for f, v in hot:
    tag = f"{v['fixes']}× fixed"
    rs, roles, idc = flow_of(f)
    detail = ""
    if rs:
        paths = ", ".join(sorted({rt["method"] + " " + rt["path"] for rt in rs})[:3])
        warn = f"  ⚠ IDOR:{idc['tier']}" if idc else ""
        detail = f" -> {paths}  roles={roles or ['?']}{warn}"
    print(f"  [{tag:11}] {kind(f):11} {f.replace(srv_rel + '/', '')}{detail}")

# ---- 4. co-changed clusters = coupled flows (files fixed together) ----
print("\n## Coupled flows — files repeatedly fixed together (change one → check the others)")
seen = set()
for (a, b), n in cochange.most_common(12):
    if n < 3: break
    sa, sb = a.replace(srv_rel + "/", ""), b.replace(srv_rel + "/", "")
    print(f"  {n}×  {sa}  ⇄  {sb}")

print("\nHow to read: the files your team fixed most are your fragile flows — prioritize regression tests + review there.")
print("A ⚠ flow is fragile AND has an authz gap. Coupled flows drift together — a change to one usually needs the other.")
print("(Deterministic: git log --since=3y + the actor-model facets. 'fix' = commit subject matches fix/bug/revert/prevent/issue/…)")
