#!/usr/bin/env python3
"""deps-triage — rank npm-audit advisories by EXPLOITABILITY, not just severity.
An advisory != an exploitable bug. Deterministic urgency gates (no LLM):
  severity(critical|high)  ×  isDirect(not transitive)  ×  runtime(not devDependency)  ×  imported-in-source  ×  fixAvailable
URGENT = a high/critical vuln in a package you DIRECTLY depend on at runtime AND actually import → attacker-reachable, fix now.
Everything else drops to REVIEW (transitive / not-imported) or LOW (dev-only / moderate-low). Final exploitability
(does attacker input reach the vulnerable call) still needs the human/LLM gate — this is the shortlist for it.
Reads auditctl-scan raws (.audit/raw/npm-audit-*.json) + each manifest's package.json + source.
Usage: deps-triage.py <repo> <auditOutDir>"""
import json, os, re, sys, glob

repo = os.path.abspath(sys.argv[1])
out = os.path.abspath(sys.argv[2])
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock")
SRC_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

def pkg_of(spec):                       # 'lodash/get' -> 'lodash' ; '@babel/core/x' -> '@babel/core'
    if spec.startswith("@"):
        parts = spec.split("/"); return "/".join(parts[:2])
    return spec.split("/")[0]

def imported_pkgs(srcdir):              # walk source ONCE -> set of imported top-level packages
    got, imp = set(), re.compile(r"""(?:require\(|(?:import|export)[^'"]*from\s+)['"]([^'".][^'"]*)['"]""")
    for dp, dn, fns in os.walk(srcdir):
        dn[:] = [d for d in dn if d != "node_modules" and not d.startswith(".")]
        for fn in fns:
            if fn.endswith(SRC_EXT):
                try: txt = open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read()
                except Exception: continue
                for m in imp.finditer(txt):
                    if not m.group(1).startswith("."): got.add(pkg_of(m.group(1)))
    return got

def advisory_title(via):
    for v in via or []:
        if isinstance(v, dict) and v.get("title"): return v["title"]
    return ""

rows = []                               # (tier, manifest, pkg, severity, direct, dev, imported, fix, title)
manifest_dirs = sorted({os.path.dirname(pj) for pj in glob.glob(os.path.join(repo, "**", "package.json"), recursive=True)
                        if "node_modules" not in pj and any(os.path.isfile(os.path.join(os.path.dirname(pj), lf)) for lf in LOCKFILES)})
for d in manifest_dirs:
    rel = os.path.relpath(d, repo)
    safe = "root" if rel == "." else rel.replace(os.sep, "_")
    raw = os.path.join(out, "raw", f"npm-audit-{safe}.json")
    if not os.path.isfile(raw): continue
    try: vulns = json.load(open(raw)).get("vulnerabilities", {})
    except Exception: continue
    manifest = json.load(open(os.path.join(d, "package.json")))
    deps, devs = manifest.get("dependencies", {}), manifest.get("devDependencies", {})
    used = imported_pkgs(d)
    for name, v in vulns.items():
        sev = v.get("severity", "low"); direct = bool(v.get("isDirect"))
        dev = (name in devs) and (name not in deps)
        imp = pkg_of(name) in used
        fix = v.get("fixAvailable"); fix = True if fix is True or isinstance(fix, dict) else False
        hi = sev in ("critical", "high")
        if hi and direct and not dev and imp: tier = "URGENT"
        elif hi and not dev: tier = "REVIEW"
        else: tier = "LOW"
        rows.append((tier, rel, name, sev, direct, dev, imp, fix, advisory_title(v.get("via"))))

ORDER = {"URGENT": 0, "REVIEW": 1, "LOW": 2}
SEV = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
rows.sort(key=lambda r: (ORDER[r[0]], SEV.get(r[3], 9), r[1], r[2]))
counts = {t: sum(1 for r in rows if r[0] == t) for t in ("URGENT", "REVIEW", "LOW")}

print("# dependency exploitability triage (deterministic gates; advisory != exploitable)\n")
print(f"manifests audited: {len(manifest_dirs)}  |  advisories: {len(rows)}  |  {counts}\n")
for tier in ("URGENT", "REVIEW", "LOW"):
    sub = [r for r in rows if r[0] == tier]
    if not sub: continue
    desc = {"URGENT": "high/critical · DIRECT runtime dep · imported in source → attacker-reachable, fix now",
            "REVIEW": "high/critical but transitive or not-directly-imported → confirm the vulnerable path is reached",
            "LOW": "dev-only or moderate/low → schedule, not urgent"}[tier]
    print(f"## {tier} ({len(sub)}) — {desc}")
    show = sub if tier != "LOW" else sub[:8]
    for _, man, pkg, sev, direct, dev, imp, fix, title in show:
        tags = " ".join(t for t, on in [("direct", direct), ("dev", dev), ("imported", imp), ("fix-available", fix)] if on) or "transitive"
        print(f"  [{sev:8}] {man}/{pkg:28} {tags}")
        if title: print(f"             {title[:88]}")
    if tier == "LOW" and len(sub) > 8: print(f"  … +{len(sub)-8} more low/dev")
    print()
print("Gate note: URGENT = deterministically reachable (direct + runtime + imported). Whether ATTACKER INPUT reaches the")
print("vulnerable call is the human/LLM's final check — cross with the actor model's public/open routes (server_routes facet).")
