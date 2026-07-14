#!/usr/bin/env python3
"""v2 core: deduce ownership predicates per (role, entity) by JOINING the ORM owner-link graph
with the caller's tenant attributes (auth_attrs) — by ref-target type. No LLM, no guessing.

Input: JSON from probes/mongoose-schema.js on stdin (or a file arg).
Output: for each entity, the ownership predicate per role (incl. multi-hop paths like
session -> contract -> attendingOrganisationsRestrictedTo == req.user.employerOrganisation).
"""
import json, sys, re
from collections import defaultdict

data = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
links = data["owner_links"]
model_names = data["model_names"]

# normalize ref strings to registered model names (case + singular/plural mismatch)
def norm(name):
    n = name.lower().rstrip("s")
    for m in model_names:
        if m.lower().rstrip("s") == n:
            return m
    return name
# graph: model -> [(field, ref_model, isArray)]
graph = defaultdict(list)
for l in links:
    graph[l["model"]].append((l["field"], norm(l["ref"]), l["isArray"]))

# tenant roots = models that ARE an owner/tenant (orgs + users). Detect: referenced but themselves org/user-like.
TENANT_ROOTS = {norm(m) for m in model_names if re.search(r"organisation|users?\b", m, re.I)}

# auth_attrs: the caller's tenant attributes = the User model's owner-link fields + self(_id -> users)
USER_MODEL = norm("users")
auth_attrs = {"_id": USER_MODEL, "userId": USER_MODEL}
for f, ref, arr in graph.get(USER_MODEL, []):
    auth_attrs[f] = ref  # e.g. tpOrganisation -> tpOrganisations, employerOrganisation -> employerOrganisations, programmesCanDeliver -> programmes

# role -> which auth attr scopes it (from the model/domain; this is the one piece a human confirms at Gate 1)
ROLE_ATTR = {
    "admin": None,  # global
    "programmeManager": "programmesCanDeliver",
    "tpOrgAdmin": "tpOrganisation",
    "employerOrgAdmin": "employerOrganisation",
    "trainer": "_id",
    "participant": "_id",
}

def paths_to_target(entity, target_model, max_hops=3):
    """all field-paths from entity reaching a ref whose target == target_model."""
    results = []
    def dfs(model, trail, hops):
        if hops > max_hops: return
        for field, ref, arr in graph.get(model, []):
            step = f"{field}[]" if arr else field
            newtrail = trail + [step]
            if ref == target_model:
                results.append((".".join(newtrail), arr))
            if ref in graph and ref != model and hops + 1 <= max_hops:
                dfs(ref, newtrail, hops + 1)
    dfs(entity, [], 1)
    return results

def derive(entity):
    print(f"\n=== ownership predicates for entity `{entity}` ===")
    for role, attr in ROLE_ATTR.items():
        if attr is None:
            print(f"  {role:18} GLOBAL (no ownership check required)")
            continue
        target = auth_attrs.get(attr)
        if not target:
            continue
        found = paths_to_target(entity, target)
        if not found:
            print(f"  {role:18} (no path from {entity} to {target}) — cannot own; route should not be reachable")
            continue
        # shortest path wins
        found.sort(key=lambda x: x[0].count("."))
        p, arr = found[0]
        op = "includes" if arr else "=="
        print(f"  {role:18} {entity}.{p} {op} req.user.{attr}    (target {target}, {p.count('.')+1}-hop)")

for e in ("sessions", "users", "TrainingContracts"):
    if e in graph or e in model_names:
        derive(e)
print(f"\nauth_attrs (caller tenant attributes, derived from User schema): {auth_attrs}")
