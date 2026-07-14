#!/usr/bin/env python3
"""run.py — the v2 deterministic actor-model pipeline in one command.
  detect stack  ->  extract facets (6 probes)  ->  analyze (intersect / IDOR / role-paths)  ->  report (HTML+JSON)
Everything here is deterministic (AST/ORM/file reads, no LLM). Output feeds the human/LLM gate — it is a
CANDIDATE GENERATOR, not a verdict. Usage: run.py <repoRoot> [--out <repoRoot>/.audit]"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
repo = os.path.abspath(sys.argv[1])
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(repo, ".audit")
os.makedirs(os.path.join(out, "facts"), exist_ok=True)
os.makedirs(os.path.join(out, "analysis"), exist_ok=True)

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)
def save(path, text):
    open(path, "w").write(text)

# ---- 1. project config: reuse if present (preserves human knob edits), else detect + create ----
stack_json = os.path.join(out, "stack.json")
redetect = "--redetect" in sys.argv
if os.path.exists(stack_json) and not redetect:
    cfg = json.load(open(stack_json))
    print(f"# using existing project config: {stack_json}")
    print(f"  detected: {cfg.get('detected', {})}")
    print(f"  (edit its `knobs` to correct auto-detection, or re-run with --redetect to rebuild)")
else:
    r = run(["python3", os.path.join(HERE, "detect.py"), repo, "--out", stack_json])
    print(r.stdout.strip())
    cfg = json.load(open(stack_json))
    if not cfg.get("detected"):
        print("\n! No stack matched. Add a SIGNATURES entry in detect.py + a stacks/<id>.yaml extract block,")
        print("  or hand-write the knobs in", stack_json, "then re-run.")
    else:
        print(f"\n# created project config: {stack_json} — review/tune `knobs` here; it is reused on the next run.")
env = {**os.environ, "AUDIT_CONFIG": stack_json}
tgt = cfg.get("target", {})
server = os.path.join(repo, tgt.get("backend", "")) if "backend" in tgt else None
client = os.path.join(repo, tgt.get("frontend", "")) if "frontend" in tgt else None
print(f"\nbackend={server}  frontend={client}\n")

# ---- 2. extract facets (each probe -> .audit/facts/<facet>.json) ----
FACET_PROBES = [
    ("server_routes", "backend-actors.js", server), ("entities", "mongoose-schema.js", server),
    ("enforcement", "guards.js", server), ("db_ops", "db-layer.js", server),
    ("ui_flows", "client-flows.js", client), ("ui_state", "redux-state.js", client),
]
print("== extract facets ==")
for facet, probe, target in FACET_PROBES:
    if not target: print(f"  {facet:14} SKIP (no target)"); continue
    r = run(["node", os.path.join(HERE, probe), target], env=env)
    ok = r.returncode == 0 and r.stdout.strip()
    if ok: save(os.path.join(out, "facts", facet + ".json"), r.stdout)
    print(f"  {facet:14} {'ok' if ok else 'FAIL: ' + r.stderr[:120]}")

# ---- 2b. coverage: distinguish "clean" from "extractor didn't match this stack" ----
print("\n== coverage ==")
cov = json.loads(run(["python3", os.path.join(HERE, "coverage.py"), repo, os.path.join(out, "facts")], env=env).stdout or "{}")
for name, f in cov.get("facets", {}).items():
    mark = {"ok": "ok", "LOW": "⚠ LOW", "ZERO": "✗ ZERO", "n/a": "· n/a"}.get(f["status"], f["status"])
    print(f"  {name:14} {mark:8} {f['resolved']}/~{f['raw_candidates']} candidate sites resolved")
if not cov.get("coverage_ok", True):
    print("\n  ⚠️  LOW/ZERO COVERAGE — the extractor did not fully understand this stack.")
    print("     Findings below are NOT a clean bill of health; unresolved facets are UNKNOWN, not safe.")
    for w in cov.get("warnings", []): print("     - " + w)

# ---- 3. analyzers (stack-agnostic; consume facets) ----
print("\n== analyze ==")
ANALYZERS = [("intersect", "intersect.py"), ("idor-candidates", "actor-model.py"), ("role-paths", "role-paths.py")]
for name, tool in ANALYZERS:
    if not (server and client): break
    r = run(["python3", os.path.join(HERE, tool), server, client], env=env)
    save(os.path.join(out, "analysis", name + ".txt"), r.stdout)
    head = r.stdout.splitlines()
    summary = next((l for l in head if "classes:" in l or "candidates" in l or "capabilities" in l.lower()), head[2] if len(head) > 2 else "")
    print(f"  {name:16} -> analysis/{name}.txt   {summary.strip()[:90]}")

# ---- 3b. dependencies: audit every manifest (auditctl scan) -> exploitability triage ----
print("\n== dependencies ==")
auditctl = os.path.join(HERE, "..", "auditctl")
run(["python3", auditctl, "scan", "--target", repo, "--out", out], env=env)   # writes raw/npm-audit-*.json
r = run(["python3", os.path.join(HERE, "deps-triage.py"), repo, out], env=env)
save(os.path.join(out, "analysis", "deps-triage.txt"), r.stdout)
inurgent = False
for l in r.stdout.splitlines():
    if l.startswith("manifests audited"): print("  " + l.strip())
    if l.startswith("## URGENT"): inurgent = True; print("  " + l.strip()); continue
    if l.startswith("## ") and not l.startswith("## URGENT"): inurgent = False
    if inurgent and l.strip().startswith("["): print("   " + l.strip())

# ---- 4. report ----
if server and client:
    rep = os.path.join(out, "report.html")
    r = run(["python3", os.path.join(HERE, "report.py"), server, client, "--out", rep], env=env)
    print("\n== report ==")
    for l in r.stdout.splitlines():
        if l.startswith(("report:", "stats:", "intersect:")): print("  " + l)

# ---- 5. actor model (the deliverable): render the per-role handbook from the report payload ----
payload = os.path.splitext(os.path.join(out, "report.html"))[0] + ".json"
if server and client and os.path.exists(payload):
    ent = os.path.join(out, "facts", "entities.json")
    am = os.path.join(out, "actor-model.md")
    r = run(["python3", os.path.join(HERE, "handbook.py"), payload] + (["--entities", ent] if os.path.exists(ent) else []) + ["--out", am], env=env)
    print("\n== actor model ==")
    for l in r.stdout.splitlines():
        if "->" in l: print("  " + l.strip())

# ---- 6. fragile flows (git-history mining): the flows the team keeps fixing, joined to the actor model ----
if server:
    r = run(["python3", os.path.join(HERE, "fragile-flows.py"), server] + ([client] if client else []), env=env)
    if r.stdout.strip():
        save(os.path.join(out, "analysis", "fragile-flows.txt"), r.stdout)
        print("\n== fragile flows (from git history) ==")
        for l in r.stdout.splitlines():
            if "⚠ IDOR" in l: print("  " + l.strip()[:150])   # surface the fragile-AND-insecure flows
        n = sum(1 for l in r.stdout.splitlines() if l.strip().startswith("[") and "fixed" in l)
        print(f"  {n} fragile flows -> analysis/fragile-flows.txt")

print(f"\nDone. Facts: {out}/facts/  Analysis: {out}/analysis/  Report: {out}/report.html  Actor model: {out}/actor-model.md")
print("These are CANDIDATES for the human/LLM gate (Stage 1 actor-model verify + Stage 2 triage). Not a verdict.")
