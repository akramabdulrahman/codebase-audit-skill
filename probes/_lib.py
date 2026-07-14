"""Shared helpers for the Python analyzers — one definition each, so join keys can't silently drift
(the duplicated `norm` across 5 files caused a real controller/URL join bug; see V2-STATUS)."""
import json, os, re, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

def spawn(js, arg, env=None):
    """Run a Node probe and parse its JSON stdout (inherits env, incl. AUDIT_CONFIG, by default)."""
    return json.loads(subprocess.run(["node", os.path.join(HERE, js), arg], capture_output=True, text=True, env=env).stdout or "null")

def url_norm(p):
    """Canonical route/URL normalization used on BOTH sides of every join. :param -> :x, drop query, lowercase."""
    p = (p or "").split("?")[0].split("&")[0]
    return re.sub(r"/+", "/", re.sub(r":\w+", ":x", p)).rstrip("/").lower() or "/"

def ctrl_key(ctrl):
    """Normalize a controller ref (../controllers/x/y or y) to the guards/db-layer key controllers/x/y.js."""
    if not ctrl: return None
    c = re.sub(r"^\.\.?/", "", ctrl).lstrip("/")
    if not c.startswith("controllers"): c = "controllers/" + c
    return c if c.endswith(".js") else c + ".js"

def load_knobs():
    try: return json.load(open(os.environ["AUDIT_CONFIG"])).get("knobs", {})
    except Exception: return {}
