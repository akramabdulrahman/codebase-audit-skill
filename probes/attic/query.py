#!/usr/bin/env python3
"""v2 authorization CANDIDATE GENERATOR (not an authoritative verdict).
Joins routes (reachability) + guards (alias/flow-aware ownership checks) + ORM graph (ownership links).
Emits confidence-tiered candidates for the LLM/human gate to confirm — it does NOT decide.

Confidence:
  HIGH   = entity has an ownership link for the role AND a sibling handler proves the field
           (comparison-confirmed) — the role reaches a mutating route and never applies it.
  MEDIUM = ownership link exists in the schema but no handler confirms the field (likely the bug,
           or a check this analysis can't see: query-level scoping / cross-function).
  REVIEW = bare-auth mutation of an object by id (verify self-scoping), or role reaches but has no
           clean ownership link (possibly over-granted role).
Usage: query.py <serverDir>"""
import json, os, re, subprocess, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
server = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
def node(js): return json.loads(subprocess.run(["node", os.path.join(HERE, js), server], cwd=server, capture_output=True, text=True).stdout or "{}")

routes = node("routes.js").get("routes", [])
guards = {g["file"]: g for g in (node("guards.js") or [])}
orm = node("mongoose-schema.js")

SCOPED = {"programmeManager": "programmesCanDeliver", "tpOrgAdmin": "tpOrganisation",
          "employerOrgAdmin": "employerOrganisation", "trainer": "_id"}
DIR_ENTITY = {"sessionDetails": "sessions", "session": "sessions", "addSession": "sessions",
              "users": "users", "userInvitation": "userInvitations", "employerOrganisation": "employerOrganisation",
              "tpOrganisation": "TPOrganisation", "contracts": "TrainingContracts"}
MUT = {"POST", "PUT", "PATCH", "DELETE"}
OBJ_PARAM = re.compile(r":(id|credId|sessionId|token|[A-Za-z]*Id)\b")

# ---- ORM graph: ownership-link path from entity -> the tenant type of a role's attr ----
def norm(name, names):
    n = name.lower().rstrip("s")
    for m in names:
        if m.lower().rstrip("s") == n: return m
    return name
names = orm.get("model_names", [])
graph = defaultdict(list)
for l in orm.get("owner_links", []):
    graph[l["model"]].append((l["field"], norm(l["ref"], names), l["isArray"]))
USER = norm("users", names)
attr_target = {"_id": USER, "userId": USER, "tpOrganisation": None, "employerOrganisation": None, "programmesCanDeliver": None}
for f, ref, arr in graph.get(USER, []):
    if f in attr_target: attr_target[f] = ref
def has_path(entity, target, maxh=3):
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

def ctrl_file(c):
    if not c: return None
    c = c.replace("../", "")
    return c if c.endswith(".js") else c + ".js"
def entity_of(cf):
    for seg in (cf or "").split("/"):
        if seg.replace(".js", "") in DIR_ENTITY: return DIR_ENTITY[seg.replace(".js", "")]
    return None

# confirmed field per (entity, attr) — from any handler that actually compares it
confirmed = {}
for cf, g in guards.items():
    ent = entity_of(cf)
    for c in g.get("comparedFields", []):
        if ent and c["field"] != "*": confirmed.setdefault((ent, c["attr"]), c["field"])

HIGH, MED, REVIEW = [], [], []
for r in routes:
    if r["method"] not in MUT or r.get("public"): continue
    obj = OBJ_PARAM.search(r["path"] or "")
    cf = ctrl_file(r["controllerFile"]); g = guards.get(cf)
    if not g: continue
    ent = entity_of(cf); guarded = {c["attr"] for c in g.get("comparedFields", [])}
    # --- bare-auth (roles=[]) mutation of an object by id: recover the FNs the skip-rule hid ---
    if not r["roles"]:
        if obj and not g.get("selfId"):   # acts on a param id, never on caller identity -> suspicious
            REVIEW.append((r["method"], r["path"], "any-authenticated mutation of :id, handler never references caller identity — verify authz"))
        continue
    if not obj: continue
    for role in set(r["roles"]) & set(SCOPED):
        attr = SCOPED[role]
        if attr in guarded: continue                      # ownership enforced for this role
        tgt = attr_target.get(attr)
        field = confirmed.get((ent, attr))
        path_named = has_path(ent, tgt) if not field else None
        if field:
            HIGH.append((r["method"], r["path"], role, f"{ent}.{field} ~ req.user.{attr}"))
        elif path_named:
            MED.append((r["method"], r["path"], role, f"{ent}.{path_named} ~ req.user.{attr}  (schema link, unconfirmed)"))
        else:
            REVIEW.append((r["method"], r["path"], f"{role} reaches but no ownership link to `{ent}` — over-granted role or global-by-design?"))

print("# v2 authorization candidates — a GENERATOR for the human/LLM gate, NOT a verdict\n")
print(f"routes parsed: {len(routes)} (2 top-level routers only — sub-router mounts not yet walked; treat absence as UNKNOWN, not safe)\n")
print(f"## HIGH confidence ({len(HIGH)}) — role reaches a mutating route, ownership predicate confirmed elsewhere but not here")
for m, p, role, pred in HIGH: print(f"  {m:6} {p:38} {role:16} missing: {pred}")
print(f"\n## MEDIUM ({len(MED)}) — schema ownership link exists, no handler confirms the field (likely bug, or query-level/cross-fn check this pass can't see — the LLM should verify)")
for m, p, role, pred in MED: print(f"  {m:6} {p:38} {role:16} missing: {pred}")
print(f"\n## REVIEW ({len(REVIEW)}) — needs human/LLM judgement (bare-auth self-scoping, or over-granted role)")
for row in REVIEW: print("  " + " ".join(str(x) for x in row))
print("\nCaveats fed to the gate: query-level scoping (filter.tpOrganisation) and cross-function checks may exist beyond this analysis; sub-routers unwalked; predicates are proposals to confirm, not facts.")
