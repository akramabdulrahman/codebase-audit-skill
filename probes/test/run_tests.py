#!/usr/bin/env python3
"""Test harness for the audit probes. Locks determinism + behavior against a synthetic fixture repo,
so the correctness rework can't silently regress. Run: python3 probes/test/run_tests.py [--update]
  --update  (re)writes the golden JSON files from current output (bootstrap / after an intended change)
Tests: (1) golden-JSON per probe on the fixture, (2) determinism (2x identical), (3) semantic invariants,
(4) negative/robustness (missing dirs degrade, don't crash)."""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(HERE)
FIX = os.path.join(PROBES, "__fixtures__", "mini-mern")
GOLD = os.path.join(HERE, "golden")
os.makedirs(GOLD, exist_ok=True)
UPDATE = "--update" in sys.argv
ICS = os.path.abspath(os.path.join(PROBES, "..", "..", "..", "ics"))   # optional real-repo invariant check
env = {**os.environ, "AUDIT_CONFIG": os.path.join(FIX, "stack.json")}
S, C = os.path.join(FIX, "server"), os.path.join(FIX, "client")
passed, failed = 0, []

def run(cmd, e=env):
    return subprocess.run(cmd, capture_output=True, text=True, env=e)
def _isjson(s):
    try: json.loads(s); return True
    except Exception: return False
def check(name, cond, detail=""):
    global passed
    if cond: passed += 1; print(f"  ok   {name}")
    else: failed.append(name); print(f"  FAIL {name}  {detail}")

# ---- 1. golden-JSON per probe (canonicalized: sort_keys) ----
PROBE_CMDS = {
    "backend-actors": ["node", f"{PROBES}/backend-actors.js", S],
    "guards": ["node", f"{PROBES}/guards.js", S],
    "db-layer": ["node", f"{PROBES}/db-layer.js", S],
    "mongoose-schema": ["node", f"{PROBES}/mongoose-schema.js", S],
    "client-flows": ["node", f"{PROBES}/client-flows.js", C],
    "redux-state": ["node", f"{PROBES}/redux-state.js", C],
    "actor-model": ["python3", f"{PROBES}/actor-model.py", S, C, "--json"],
    "intersect-txt": ["python3", f"{PROBES}/intersect.py", S, C],
}
print("[1] golden facets")
for name, cmd in PROBE_CMDS.items():
    out = run(cmd).stdout.strip()
    try: canon = json.dumps(json.loads(out), sort_keys=True, indent=1)
    except Exception: canon = out           # intersect-txt is text
    gp = os.path.join(GOLD, name + (".json" if canon != out else ".txt"))
    if UPDATE or not os.path.exists(gp):
        open(gp, "w").write(canon); print(f"  wrote {os.path.basename(gp)}")
    else:
        check(f"golden:{name}", open(gp).read() == canon, "output drifted from golden (run --update if intended)")

# ---- 2. determinism ----
print("[2] determinism (2x identical)")
for name, cmd in PROBE_CMDS.items():
    a, b = run(cmd).stdout, run(cmd).stdout
    check(f"determinism:{name}", a == b, "non-deterministic output")

# ---- 3. semantic invariants (the product claims) ----
print("[3] semantic invariants on the fixture")
am = json.loads(run(PROBE_CMDS["actor-model"]).stdout or "{}")
cand = {c["path"]: c for c in am.get("candidates", [])}
check("fixture: deleteWidget is a HIGH IDOR",
      cand.get("/api/widgets/:id", {}).get("tier") == "HIGH" and cand["/api/widgets/:id"]["method"] == "DELETE"
      if any(c["method"] == "DELETE" for c in am.get("candidates", [])) else False,
      str([(c["method"], c["path"], c["tier"]) for c in am.get("candidates", [])]))
dele = next((c for c in am.get("candidates", []) if c["method"] == "DELETE" and c["path"] == "/api/widgets/:id"), {})
check("fixture: deleteWidget predicate names the org owner-link", "org" in (dele.get("predicate") or ""), dele.get("predicate"))
check("fixture: deleteWidget provenance cites the sibling guard",
      any("sibling" in s.get("step", "") for s in dele.get("prov", [])), "no sibling step")
guards = {g["file"]: g for g in json.loads(run(PROBE_CMDS["guards"]).stdout or "[]")}
check("fixture: updateWidget detected as guarded",
      any(k.endswith("updateWidget.js") and g["hasOwnershipGuard"] for k, g in guards.items()))
check("fixture: deleteWidget detected as UNguarded",
      any(k.endswith("deleteWidget.js") and not g["hasOwnershipGuard"] for k, g in guards.items()))

# ---- 4. negative / robustness: missing dirs must degrade, not crash ----
print("[4] robustness (missing dirs degrade, exit 0, no crash)")
noenv = {k: v for k, v in os.environ.items() if k != "AUDIT_CONFIG"}
for name in ("guards", "mongoose-schema", "db-layer"):
    r = run(["node", f"{PROBES}/{name}.js", "/tmp"], e=noenv)
    check(f"robust:{name} exit0", r.returncode == 0, f"exit={r.returncode} stderr={r.stderr[:80]}")
    check(f"robust:{name} valid-output", r.stdout.strip() in ("[]", "") or _isjson(r.stdout), r.stdout[:80])

# ---- optional: ICS real-repo invariant (the 3 live-verified IDORs stay HIGH) ----
if os.path.isdir(ICS) and os.path.exists(os.path.join(ICS, ".audit", "stack.json")):
    print("[5] ICS invariant (live-verified IDORs)")
    ie = {**os.environ, "AUDIT_CONFIG": os.path.join(ICS, ".audit", "stack.json")}
    am2 = json.loads(run(["python3", f"{PROBES}/actor-model.py", os.path.join(ICS, "server"), os.path.join(ICS, "client"), "--json"], e=ie).stdout or "{}")
    high = {c["path"] for c in am2.get("candidates", []) if c["tier"] == "HIGH"}
    for p in ["/api/session-delete/:id", "/api/session-edit/:id", "/api/sessions/:sessionId/reopen-event"]:
        check(f"ICS: {p} is HIGH", p in high)
    # role-conditional-guard FN: updateUserById guards only employerOrgAdmin; tpOrgAdmin/programmeManager bypass
    uu = next((c for c in am2.get("candidates", []) if "updateUserById" in c["controller"]), None)
    check("ICS: updateUserById role-conditional-guard FN recovered as HIGH", uu is not None and uu["tier"] == "HIGH", str(uu))
    # reference impls with a full guard stay cleared (no false positive)
    for ref in ("finaliseSession", "updateEmployerOrganisation"):
        check(f"ICS: {ref} (guarded ref impl) not flagged", not any(ref.lower() in c["controller"].lower() for c in am2.get("candidates", [])))
    # PREDICATE SOUNDNESS: every named predicate `Model.field == ...` must reference a field the model actually declares
    orm = json.loads(run(["node", f"{PROBES}/mongoose-schema.js", os.path.join(ICS, "server")], e=ie).stdout or "{}")
    mfields = {}
    for l in orm.get("owner_links", []): mfields.setdefault(l["model"].lower().rstrip("s"), set()).add(l["field"])
    def field_declared(model, field):
        return field in ("_id", "id") or field in mfields.get(model.lower().rstrip("s"), set())
    unsound = []
    for c in am2.get("candidates", []):
        for term in re.findall(r"(\w+)\.(\w+)\s*==", c.get("predicate") or ""):
            if not field_declared(*term): unsound.append(f"{c['method']} {c['path']}: {term[0]}.{term[1]}")
    check("ICS: no candidate names a field its model doesn't declare (predicate soundness)", not unsound, str(unsound[:3]))

print(f"\n{'='*50}\n{passed} passed, {len(failed)} failed" + (": " + ", ".join(failed) if failed else ""))
sys.exit(1 if failed else 0)
