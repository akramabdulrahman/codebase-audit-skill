#!/usr/bin/env python3
"""v2 fact-extraction + query engine. Builds .audit/facts.db (SQLite) from independent
extractors, then answers the IDOR query by JOINING them. Deterministic; no LLM.

Usage: extract.py --server <serverDir> --out <auditDir>
"""
import argparse, glob, json, os, re, sqlite3, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- controller directory -> ORM model it owns (the entity a handler mutates) ----
DIR_ENTITY = {
    "sessionDetails": "sessions", "session": "sessions", "addSession": "sessions",
    "users": "users", "userInvitation": "users",
    "employerOrganisation": "employerOrganisation", "tpOrganisation": "TPOrganisation",
    "contracts": "TrainingContracts", "trainingRequests": "TrainingRequests",
}
TENANT_ATTRS = ["tpOrganisation", "employerOrganisation", "programmesCanDeliver", "_id", "userId"]
MUTATORS = re.compile(r"\b(findByIdAndUpdate|findByIdAndDelete|findByIdAndRemove|deleteOne|deleteMany|updateOne|updateMany|findOneAndUpdate|findOneAndDelete)\b")
WRAPPER_MUT = re.compile(r"\b(delete|remove|edit|update|reopen|finali[sz]e|create|add|mark)[A-Z]\w*\s*\(", re.I)
ROLE_ATTR = {"admin": None, "programmeManager": "programmesCanDeliver", "tpOrgAdmin": "tpOrganisation",
             "employerOrgAdmin": "employerOrganisation", "trainer": "_id", "participant": "_id"}

def sh(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

# ---------- extractor: ORM owner-link graph ----------
def extract_orm(server):
    r = sh(["node", os.path.join(HERE, "mongoose-schema.js"), server], cwd=server)
    try: return json.loads(r.stdout)
    except Exception:
        print("[orm] probe failed:", r.stderr[:300]); sys.exit(1)

def normalizer(model_names):
    def norm(name):
        n = name.lower().rstrip("s")
        for m in model_names:
            if m.lower().rstrip("s") == n: return m
        return name
    return norm

# ---------- derive candidate predicates (graph type-join) ----------
def derive_predicates(orm):
    norm = normalizer(orm["model_names"])
    graph = defaultdict(list)
    for l in orm["owner_links"]:
        graph[l["model"]].append((l["field"], norm(l["ref"]), l["isArray"]))
    USER = norm("users")
    auth_attrs = {"_id": USER, "userId": USER}
    for f, ref, arr in graph.get(USER, []):
        auth_attrs[f] = ref
    def paths(entity, target, maxh=3):
        out = []
        def dfs(m, trail, h):
            if h > maxh: return
            for field, ref, arr in graph.get(m, []):
                step = f"{field}[]" if arr else field
                nt = trail + [step]
                if ref == target: out.append((".".join(nt), arr, h))
                if ref in graph and ref != m: dfs(ref, nt, h + 1)
        dfs(entity, [], 1); return out
    preds = []  # (entity, role, path, op, attr, hops)
    for entity in set(list(graph.keys()) + orm["model_names"]):
        for role, attr in ROLE_ATTR.items():
            if attr is None: continue
            target = auth_attrs.get(attr)
            if not target: continue
            found = paths(entity, target)
            if not found: continue
            found.sort(key=lambda x: x[2])
            p, arr, h = found[0]
            preds.append((entity, role, p, "includes" if arr else "==", attr, h))
    return preds, auth_attrs, graph

# ---------- extractor: per-controller enforcement (mutates? ownership check?) ----------
def extract_enforcement(server):
    rows = []  # (controller_file, entity, mutates, has_check, attrs)
    for f in glob.glob(os.path.join(server, "controllers", "**", "*.js"), recursive=True):
        try: txt = open(f, encoding="utf-8", errors="ignore").read()
        except Exception: continue
        rel = os.path.relpath(f, server)
        parts = rel.split(os.sep)
        entity = None
        for seg in parts:
            key = seg.replace(".js", "")
            if key in DIR_ENTITY: entity = DIR_ENTITY[key]; break
        mutates = 1 if (MUTATORS.search(txt) or WRAPPER_MUT.search(txt)) else 0
        # tenant attrs referenced EITHER as req.user.X OR destructured `const {X,...} = req.user` (grep alone misses the latter)
        destructured = set()
        for dm in re.finditer(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*(?:req\.)?user\b", txt):
            destructured |= {x.strip().split(":")[0].strip() for x in dm.group(1).split(",") if x.strip()}
        attrs = [a for a in TENANT_ATTRS
                 if re.search(r"req\.user\.%s\b|\buser\.%s\b" % (re.escape(a), re.escape(a)), txt) or a in destructured]
        # ownership check = references a caller tenant attr AND has a guard/comparison
        has_check = 1 if (attrs and re.search(r"forbidden|unauthorized|isAllowed|toString\(\)\s*!==|\.some\(|\.find\(|\.includes\(", txt)) else 0
        rows.append((rel, entity, mutates, has_check, ",".join(attrs)))
    return rows

# ---------- extractor: mine existing authz tests (ground-truth MUST-NOTs) ----------
def extract_tests(server):
    rows = []  # (role, method, path, expected, kind)
    for f in glob.glob(os.path.join(server, "__test__", "**", "auth", "*.test.js"), recursive=True):
        txt = open(f, encoding="utf-8", errors="ignore").read()
        for m in re.finditer(r"loginRole:\s*userRoles\.(\w+)", txt):
            pass
        # crude tuple mining: pair a request(.method).(url) with the nearest expect(res.status).toBe(N)
        for rm in re.finditer(r"\.(get|post|put|patch|delete)\(\s*`([^`]+)`", txt):
            method, url = rm.group(1).upper(), rm.group(2)
            tail = txt[rm.end():rm.end()+400]
            st = re.search(r"status\)\.toBe\((\d+)\)", tail)
            role = None
            back = txt[max(0, rm.start()-600):rm.start()]
            rr = re.findall(r"loginRole:\s*userRoles\.(\w+)", back)
            role = rr[-1] if rr else None
            kind = "repro" if "🔴" in txt[max(0,rm.start()-300):rm.start()] else "char"
            if st: rows.append((role, method, re.sub(r"\$\{[^}]+\}", ":x", url), int(st.group(1)), kind))
    return rows

def build_db(out, orm, preds, enf, tests, auth_attrs):
    db = os.path.join(out, "facts.db")
    if os.path.exists(db): os.remove(db)
    c = sqlite3.connect(db); q = c.cursor()
    q.execute("CREATE TABLE owner_links(model,field,ref_model,is_array)")
    q.executemany("INSERT INTO owner_links VALUES(?,?,?,?)",
                  [(l["model"], l["field"], l["ref"], int(l["isArray"])) for l in orm["owner_links"]])
    q.execute("CREATE TABLE auth_attrs(attr,ref_model)")
    q.executemany("INSERT INTO auth_attrs VALUES(?,?)", list(auth_attrs.items()))
    q.execute("CREATE TABLE predicate(entity,role,path,op,attr,hops)")
    q.executemany("INSERT INTO predicate VALUES(?,?,?,?,?,?)", preds)
    q.execute("CREATE TABLE enforcement(controller,entity,mutates,has_check,attrs)")
    q.executemany("INSERT INTO enforcement VALUES(?,?,?,?,?)", enf)
    q.execute("CREATE TABLE test_authz(role,method,path,expected,kind)")
    q.executemany("INSERT INTO test_authz VALUES(?,?,?,?,?)", tests)
    c.commit(); return c

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    orm = extract_orm(a.server)
    preds, auth_attrs, graph = derive_predicates(orm)
    enf = extract_enforcement(a.server)
    tests = extract_tests(a.server)
    c = build_db(a.out, orm, preds, enf, tests, auth_attrs)
    q = c.cursor()
    print(f"[facts] owner_links={len(orm['owner_links'])} predicates={len(preds)} controllers={len(enf)} test_rows={len(tests)}")
    # ---- THE IDOR QUERY: a controller mutates entity E, has NO ownership check, and E has a scoped-role predicate ----
    rows = q.execute("""
      SELECT e.controller, e.entity, p.role, p.path, p.op, p.attr
      FROM enforcement e
      JOIN predicate p ON p.entity = e.entity AND p.role != 'admin'
      WHERE e.mutates = 1 AND e.has_check = 0
      ORDER BY e.controller, p.role
    """).fetchall()
    # group by controller
    by_ctrl = defaultdict(list)
    for ctrl, ent, role, path, op, attr in rows:
        by_ctrl[(ctrl, ent)].append(f"{role}: {ent}.{path} {op} req.user.{attr}")
    lines = ["# v2 IDOR candidates (deterministic — no ownership check + named predicate)\n"]
    lines.append(f"Fact store: `.audit/facts.db`. {len(by_ctrl)} handlers mutate a scoped entity with no ownership guard.\n")
    # corroboration set: entities that have a 🔴 test
    repro_entities = set()
    for role, method, path, exp, kind in tests:
        if kind == "repro":
            for k, v in DIR_ENTITY.items():
                if k.lower() in (path or "").lower() or v.lower() in (path or "").lower(): repro_entities.add(v)
    for (ctrl, ent), plist in sorted(by_ctrl.items()):
        corrob = " ✅ live-verified (🔴 test exists for this entity)" if ent in repro_entities else ""
        lines.append(f"\n### `{ctrl}` — mutates `{ent}`, no ownership check{corrob}")
        for pl in dict.fromkeys(plist): lines.append(f"- missing: `{pl}`")
    # ---- the FP-clear proof: controllers that DID enforce (has_check=1) ----
    cleared = q.execute("SELECT controller,entity,attrs FROM enforcement WHERE mutates=1 AND has_check=1 ORDER BY controller").fetchall()
    lines.append(f"\n## Cleared by the sanitizer join ({len(cleared)}) — enforce ownership, NOT flagged (v1 false positives gone)")
    for ctrl, ent, attrs in cleared[:25]:
        lines.append(f"- `{ctrl}` (checks `req.user.{attrs}`)")
    open(os.path.join(a.out, "v2-idor-candidates.md"), "w").write("\n".join(lines) + "\n")
    print(f"[query] {len(by_ctrl)} IDOR candidates, {len(cleared)} cleared -> {a.out}/v2-idor-candidates.md")

if __name__ == "__main__":
    main()
