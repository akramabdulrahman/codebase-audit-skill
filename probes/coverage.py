#!/usr/bin/env python3
"""Coverage signal — the fix for "extractor didn't match this stack" being indistinguishable from "clean".
For each facet: count RAW candidate sites in source (route-method calls, route-guard JSX, http-client calls,
model files) and compare to what the probe RESOLVED. If raw > 0 but resolved ≈ 0, the stack is unsupported/
mis-configured — NOT clean. Emits a per-facet verdict + an overall `coverage_ok` used by run.py/report.py.
Usage: coverage.py <repoRoot> <factsDir>   (reads AUDIT_CONFIG for knobs + target subdirs)"""
import json, os, re, sys

repo = os.path.abspath(sys.argv[1])
facts = os.path.abspath(sys.argv[2])
cfg = {}
try: cfg = json.load(open(os.environ["AUDIT_CONFIG"]))
except Exception: pass
knobs = cfg.get("knobs", {})
target = cfg.get("target", {})
SRC_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

def walk_count(root, pattern):
    if not root or not os.path.isdir(root): return 0
    rx = re.compile(pattern); n = 0
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d != "node_modules" and not d.startswith(".")]
        for fn in fns:
            if fn.endswith(SRC_EXT):
                try: n += len(rx.findall(open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read()))
                except Exception: pass
    return n

def fact(name, key, default=0):
    p = os.path.join(facts, name + ".json")
    if not os.path.exists(p): return None
    try:
        d = json.load(open(p))
        for k in key.split("."): d = d[k] if isinstance(d, dict) else d
        return len(d) if isinstance(d, list) else d
    except Exception: return default

sdir = os.path.join(repo, target.get("backend", "")) if "backend" in target else None
cdir = os.path.join(repo, target.get("frontend", "")) if "frontend" in target else None
guard = knobs.get("route_guard_component", "PrivateRoute")
http = knobs.get("http_clients", ["axios", "api", "http"])
model_dir = knobs.get("model_dir", "database/models")

def count_files(root, exts):
    if not root or not os.path.isdir(root): return 0
    n = 0
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d != "node_modules" and not d.startswith(".")]
        n += sum(1 for f in fns if f.endswith(exts))
    return n

FACETS = []
if sdir:
    raw_routes = walk_count(sdir, r"\b(?:router|app|route)\w*\.(?:get|post|put|patch|delete)\s*\(")
    FACETS.append(("server_routes", raw_routes, fact("server_routes", "routes")))
    md = os.path.join(sdir, model_dir)
    raw_models = len([f for f in os.listdir(md) if f.endswith((".js", ".ts"))]) if os.path.isdir(md) else 0
    FACETS.append(("entities", raw_models, fact("entities", "model_names")))
    # enforcement + db_ops: did guards / db-layer actually run over the controllers? (silent-empty here = false-cleared IDORs)
    ctrl_dir = os.path.join(sdir, knobs.get("controller_dir", "controllers"))
    raw_ctrls = count_files(ctrl_dir, (".js", ".ts"))
    FACETS.append(("enforcement", raw_ctrls, fact("enforcement", "")))            # guards.json is a bare array of controllers processed
    # db_ops denominator = the query-wrapper layer's files (not all controllers touch the DB)
    qdirs = knobs.get("query_dirs", ["database/queries"])
    raw_q = sum(count_files(os.path.join(sdir, q), (".js", ".ts")) for q in qdirs)
    FACETS.append(("db_ops", raw_q, fact("db_ops", "stats.queryFns")))
if cdir:
    raw_uiroutes = walk_count(cdir, r"<" + re.escape(guard) + r"\b|createBrowserRouter|<Route\b|useRoutes\s*\(")
    FACETS.append(("ui_routes", raw_uiroutes, fact("ui_flows", "stats.routes")))
    raw_http = walk_count(cdir, r"\b(?:" + "|".join(re.escape(h) for h in http) + r")\.(?:get|post|put|patch|delete)\s*\(")
    FACETS.append(("ui_actions", raw_http, fact("ui_flows", "stats.thunkCalls")))
    red_dir = os.path.join(cdir, knobs.get("reducer_dir", "src/reducers"))
    raw_red = count_files(red_dir, (".js", ".jsx", ".ts", ".tsx"))
    FACETS.append(("ui_state", raw_red, fact("ui_state", "stats.slices")))

out = {"facets": {}, "warnings": [], "coverage_ok": True}
for name, raw, resolved in FACETS:
    resolved = resolved or 0
    if raw == 0:
        status = "n/a"                                   # no candidate sites -> facet genuinely absent
    elif resolved == 0:
        status = "ZERO"; out["coverage_ok"] = False
        out["warnings"].append(f"{name}: 0 of ~{raw} candidate sites resolved — extractor did NOT match this stack (NOT a clean result)")
    elif resolved / max(raw, 1) < 0.30:
        status = "LOW"; out["coverage_ok"] = False
        out["warnings"].append(f"{name}: only {resolved}/~{raw} candidate sites resolved (<30%) — partial coverage, treat gaps as UNKNOWN")
    else:
        status = "ok"
    out["facets"][name] = {"raw_candidates": raw, "resolved": resolved, "status": status}

# scoped_roles sanity: the IDOR analyzer silently skips role-gated routes whose role isn't in scoped_roles.
# If scoped_roles (defaulting to the ICS map) shares ZERO roles with the repo's OBSERVED roles, IDOR analysis is blind.
if sdir:
    try:
        routes = json.load(open(os.path.join(facts, "server_routes.json"))).get("routes", [])
        observed = {r for rt in routes for r in (rt.get("roles") or []) + (rt.get("controllerRoles") or [])}
        scoped = set((knobs.get("scoped_roles") or {}).keys())
        if observed and scoped and not (observed & scoped):
            out["coverage_ok"] = False
            out["warnings"].append(f"scoped_roles {sorted(scoped)} matches 0 of the repo's {len(observed)} observed roles {sorted(observed)[:6]} — "
                                   f"IDOR analysis SKIPS every role-gated route (set knobs.scoped_roles for this repo). Result is UNKNOWN, not clean.")
            out["facets"]["authz_scope"] = {"raw_candidates": len(observed), "resolved": 0, "status": "ZERO"}
    except Exception: pass

json.dump(out, open(os.path.join(facts, "..", "coverage.json"), "w"), indent=1) if os.path.isdir(os.path.join(facts, "..")) else None
print(json.dumps(out))
