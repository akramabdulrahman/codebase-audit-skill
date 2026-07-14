#!/usr/bin/env python3
"""handbook — turn the audit skill's OUTPUT (report payload + entities facet) into a living PROJECT HANDBOOK,
and, given a previous snapshot, DETECT actor-model changes and write a changelog. Consumes ONLY skill output
(no source access) — so a context-less agent can run it. Application: the actor model IS the spec; keep the
handbook in sync with it automatically.
Usage: handbook.py <payload.json> [--entities <entities.json>] [--prev <old_payload.json>] [--out <handbook.md>]"""
import json, os, sys
from collections import defaultdict

def arg(flag, dflt=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else dflt

pay = json.load(open(sys.argv[1]))
ent = json.load(open(arg("--entities"))) if arg("--entities") else {}
prev = json.load(open(arg("--prev"))) if arg("--prev") else None
out = arg("--out", os.path.splitext(sys.argv[1])[0] + "-handbook.md")

roles = pay.get("roles", {})
idor = pay.get("idor", [])
inter = pay.get("intersect", {})
L = []
def w(s=""): L.append(s)

# ---------- header ----------
w(f"# {pay['project']} — project handbook")
w("\n_Generated deterministically from the audit skill's actor model (no manual authoring). "
  "Each statement is derived from code; regenerate to keep it current._\n")
s = pay["stats"]
w(f"**At a glance:** {s['roles']} roles · {s['backend_routes']} API routes · {s['ui_routes']} UI routes · "
  f"{s['db_query_fns']} DB query fns · {s['state_slices']} state slices.")
am = pay.get("auth_mix", {})
gated = am.get("roles", 0); tot = sum(am.values()) or 1
w(f"**Authorization posture:** {gated}/{tot} routes are role-gated server-side; "
  f"{am.get('authed-any',0)} any-authenticated, {am.get('public',0)+am.get('open',0)} public/open. "
  f"Auth mix: {am}.\n")

# ---------- roles & capabilities ----------
w("## Roles & what they can do\n")
for role in sorted(roles):
    caps = roles[role]; writes = [c for c in caps if c["writes"]]
    entities = sorted({d.split(".")[0] for c in caps for d in c["db"]})
    w(f"### {role}")
    w(f"{len(caps)} capabilities ({len(writes)} write data). Touches: {', '.join(entities) or '—'}.\n")
    if writes:
        w("Can modify:")
        for c in sorted(writes, key=lambda c: c["url"])[:12]:
            dbs = ", ".join(c["db"]) or "—"
            w(f"- **{c['method']} {c['url']}** (`{c['page']}` → `{c['thunk']}` → `{c['controller']}`) → writes {dbs}")
        w("")

# ---------- authorization model ----------
w("## Authorization model\n")
labels = {"CLIENT-SIDE-ONLY-AUTHZ": "⚠️ Role gate is client-only (server = any-authenticated) — direct-API bypass risk",
          "FRONTEND-BROADER": "UI exposes to roles the server rejects (drift)",
          "BACKEND-BROADER": "Server allows roles no UI reaches (over-granted)",
          "BACKEND-ONLY": "API-only surface (no UI reaches it)", "AGREEMENT": "Server and UI agree (intended)"}
for cls, rows in inter.items():
    if not rows: continue
    w(f"**{labels.get(cls, cls)}** — {len(rows)} routes.")
    if cls in ("CLIENT-SIDE-ONLY-AUTHZ", "FRONTEND-BROADER", "BACKEND-BROADER"):
        for r in rows[:10]:
            w(f"- `{r['method']} {r['path']}` — server `{r['auth']}`, UI roles `{', '.join(r['frontend']) or '—'}`" +
              ("  ✍" if r.get("writes") else ""))
    w("")

# ---------- security findings ----------
w("## Security findings — ownership/IDOR candidates\n")
w(f"{pay.get('idor_counts', {})}. HIGH = a sibling handler proves the ownership predicate this one omits.\n")
for tier in ("HIGH", "MEDIUM", "REVIEW"):
    items = [c for c in idor if c["tier"] == tier]
    if not items: continue
    w(f"### {tier} ({len(items)})")
    for c in items:
        w(f"- **{c['method']} {c['path']}** (`{c['controller']}`) — missing: `{c['predicate'] or 'ownership check'}`")
        for st in c.get("prov", []):
            if st.get("loc"): w(f"  - {st['step']}: `{st['loc']}` — {st['detail']}")
    w("")

# ---------- data model ----------
if ent.get("owner_links"):
    w("## Data model (ownership links)\n")
    by = defaultdict(list)
    for l in ent["owner_links"]:
        by[l["model"]].append(f"{l['field']}{'[]' if l.get('isArray') else ''} → {l['ref']}")
    for m in sorted(by):
        w(f"- **{m}**: {', '.join(sorted(set(by[m])))}")
    w("")

# ---------- changelog (diff vs previous snapshot) ----------
def cap_keys(p):
    return {role: {(c["method"], c["url"], c["thunk"]) for c in cs} for role, cs in p.get("roles", {}).items()}
def idor_keys(p):
    return {(c["method"], c["path"]): c["tier"] for c in p.get("idor", [])}
if prev is not None:
    w("## Changelog — actor-model changes since the last handbook\n")
    ch = []
    # roles
    now_r, old_r = set(roles), set(prev.get("roles", {}))
    for r in sorted(now_r - old_r): ch.append(f"➕ **New role `{r}`** — {len(roles[r])} capabilities.")
    for r in sorted(old_r - now_r): ch.append(f"➖ **Role `{r}` removed.**")
    # idor
    ni, oi = idor_keys(pay), idor_keys(prev)
    for k in sorted(set(oi) - set(ni)):
        ch.append(f"🔒 **RESOLVED IDOR**: `{k[0]} {k[1]}` is no longer a candidate (ownership now enforced or route removed).")
    for k in sorted(set(ni) - set(oi)):
        ch.append(f"🚨 **NEW IDOR ({ni[k]})**: `{k[0]} {k[1]}` — a mutating route lost/never had its ownership guard.")
    for k in sorted(set(ni) & set(oi)):
        if ni[k] != oi[k]: ch.append(f"↕ **IDOR severity {oi[k]}→{ni[k]}**: `{k[0]} {k[1]}`.")
    # capabilities
    nc, oc = cap_keys(pay), cap_keys(prev)
    for role in sorted(set(nc) & set(oc)):
        added, removed = nc[role] - oc[role], oc[role] - nc[role]
        for a in sorted(added): ch.append(f"➕ **`{role}` gained** `{a[0]} {a[1]}` (via {a[2]}).")
        for d in sorted(removed): ch.append(f"➖ **`{role}` lost** `{d[0]} {d[1]}`.")
    # authz posture
    for cls in inter:
        n, o = len(inter.get(cls, [])), len(prev.get("intersect", {}).get(cls, []))
        if n != o: ch.append(f"• Authz class **{cls}**: {o} → {n} routes.")
    if not ch: ch = ["No actor-model changes detected."]
    for c in ch: w(c)
    w("")

open(out, "w").write("\n".join(L) + "\n")
print(f"handbook -> {out}  ({len(L)} lines)")
if prev is not None:
    print("changelog entries:", sum(1 for x in L if x.startswith(("➕", "➖", "🔒", "🚨", "↕", "•"))))
