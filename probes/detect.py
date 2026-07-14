#!/usr/bin/env python3
"""STACK DETECTOR — sniff a repo, pick the backend + frontend stack profiles, merge their extract knobs
into .audit/stack.json (the AUDIT_CONFIG the probes read). Makes the probes stack-driven, not ICS-coupled.
Analyzers spawn the probes with this env set; each probe reads only the knobs it knows (names are disjoint
across backend/frontend). Usage: detect.py <repoRoot> [--out .audit/stack.json]"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
root = os.path.abspath(sys.argv[1])
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(root, ".audit", "stack.json")

# detection signatures: stack_id -> (layer, dep predicate)
def deps_of(d):
    p = os.path.join(d, "package.json")
    if not os.path.isfile(p): return {}
    try: j = json.load(open(p)); return {**j.get("dependencies", {}), **j.get("devDependencies", {})}
    except Exception: return {}
SIGNATURES = [
    ("node-express-mongoose", "backend", lambda dp: "express" in dp and "mongoose" in dp),
    ("web-react-redux",       "frontend", lambda dp: "react" in dp and ("redux" in dp or "react-redux" in dp)),
]

# minimal YAML reader for the `extract:` block of a stack profile (target_subdir, knobs)
def read_extract(stack_id):
    f = os.path.join(SKILL, "stacks", stack_id + ".yaml")
    txt = open(f, encoding="utf-8").read() if os.path.isfile(f) else ""
    subdir, knobs, in_extract, in_knobs, knob_indent = "", {}, False, False, None
    for line in txt.splitlines():
        if re.match(r"^extract:\s*$", line): in_extract = True; continue
        if in_extract and re.match(r"^\S", line): break               # left the block
        if not in_extract: continue
        m = re.match(r"^\s+target_subdir:\s*(\S+)", line)
        if m: subdir = m.group(1).strip().strip('"'); continue
        if re.match(r"^\s+knobs:\s*(#.*)?$", line): in_knobs = True; knob_indent = None; continue
        if in_knobs:
            m = re.match(r"^(\s+)([\w]+):\s*(.*)$", line)
            if not m: continue
            indent = len(m.group(1))
            if knob_indent is None: knob_indent = indent
            if indent != knob_indent:                                  # deeper/other -> stop knobs
                if indent < knob_indent: in_knobs = False
                continue
            key, rest = m.group(2), re.sub(r"\s+#.*$", "", m.group(3)).strip()   # strip trailing comment
            if rest.startswith("["):
                try: knobs[key] = json.loads(rest)
                except Exception: knobs[key] = [x.strip().strip('"') for x in rest[1:-1].split(",") if x.strip()]
            elif rest.startswith("{"):
                inner = rest[1:-1]
                knobs[key] = {k.strip(): v.strip() for k, v in (p.split(":", 1) for p in inner.split(",") if ":" in p)}
            else:
                knobs[key] = rest.strip('"')
    return subdir, knobs

# scan root + one-level subdirs for the two layers
detected = {}
scan_dirs = [root] + [os.path.join(root, d) for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
for d in scan_dirs:
    dp = deps_of(d)
    if not dp: continue
    for sid, layer, pred in SIGNATURES:
        if layer in detected: continue
        if pred(dp):
            subdir, knobs = read_extract(sid)
            detected[layer] = {"stack": sid, "dir": os.path.relpath(d, root), "target_subdir": subdir, "knobs": knobs}

merged = {}
for layer in ("backend", "frontend"):
    if layer in detected: merged.update(detected[layer]["knobs"])

# auto-refine knobs that vary per-project within a stack family (grep, cheap):
if "backend" in detected:
    bdir = os.path.join(root, detected["backend"]["dir"])
    def greppable(pat, where):
        for dp, _, fs_ in os.walk(where):
            if "node_modules" in dp: continue
            for fn in fs_:
                if fn.endswith(".js"):
                    try:
                        if pat in open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read(): return True
                    except Exception: pass
        return False
    mw = os.path.join(bdir, "middlewares")
    if os.path.isdir(mw):
        if greppable("allowForPublic", mw): merged["public_key"] = "allowForPublic"
        elif greppable("allowPublic", mw): merged["public_key"] = "allowPublic"
def _resolve_target(d):
    # the detected dir; if it's root ("."), use the profile's subdir ONLY if it exists, else "." (root monolith)
    if d["dir"] != ".": return d["dir"]
    sub = d.get("target_subdir")
    return sub if (sub and os.path.isdir(os.path.join(root, sub))) else "."
config = {
    "_help": "Per-project audit config. Auto-detected; SAFE TO HAND-EDIT. `knobs` override stacks/capabilities.yaml "
             "defaults for THIS project. Fix a wrong value here and it persists (run.py reuses this file). "
             "`scoped_roles` maps role -> the caller tenant attr (for consumer-scope naming) — fill it from the "
             "ORM owner-links if auto-detection left it generic. Delete this file (or run.py --redetect) to rebuild.",
    "detected": {l: {k: v for k, v in detected[l].items() if k != "knobs"} for l in detected},
    "target": {l: _resolve_target(detected[l]) for l in detected},
    "knobs": merged,
}
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(config, open(out, "w"), indent=2)
os.makedirs(os.path.dirname(out), exist_ok=True)

print(f"# stack detection -> {out}\n")
for layer in ("backend", "frontend"):
    if layer in detected:
        d = detected[layer]
        print(f"{layer:9} {d['stack']:24} dir={d['dir'] or '.'}  knobs={len(d['knobs'])}")
    else:
        print(f"{layer:9} (none matched — add a signature in detect.py + a stacks/<id>.yaml extract block)")
print(f"\nmerged knobs: {list(merged)}")
print(f"\nRun analyzers with:  AUDIT_CONFIG={out} python3 probes/role-paths.py <server> <client>")
