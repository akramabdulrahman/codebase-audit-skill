---
name: dep-triage
description: Independent stage. For each HIGH/CRITICAL npm/pip/etc audit advisory, decides two things the raw audit does NOT: (1) is it actually EXPLOITABLE here (is the vulnerable API reached from our code with attacker-influenced input, prod vs dev-only, transitive-and-unused?), and (2) is the fix UPGRADE-BREAKING (semver-major? changelog/peerdep review?). Outputs .audit/deps.md — a short list of actually-actionable upgrades, not "76 vulns!" noise. Runs independently of the actor pipeline.
---

# Dependency triage — exploitability × upgrade-safety

`npm audit` (etc.) reports advisories, not risks. Two questions it never answers, and this stage does, per HIGH/CRITICAL:

Read `../reference/artifacts.md` first. Deterministic-first; only reason where a tool can't.

## Inputs
`.audit/raw/npm-audit.json` (from `auditctl scan`), the lockfile, and server source.

## Procedure — per high/critical advisory
1. **Placement:** `npm ls <pkg>` (or `--json`) → is it a **prod** dependency or dev/build/test-only? Dev-only ⇒ not in the runtime attack surface ⇒ downgrade.
2. **Reachability (exploitability):** identify the vulnerable API/function from the advisory; grep our code (imports + call sites) for whether it's actually called, and whether **attacker-influenced input** reaches it. Vulnerable-but-unreached ⇒ `disproved`/low. Reached with untrusted input ⇒ `probable`/high. State the call path as evidence.
3. **Upgrade-breaking analysis:** compare current vs fixed version.
   - `npm audit fix --dry-run` and `npm outdated <pkg>` → is the fix a **patch/minor** (safe) or **major** (breaking)?
   - For majors: read the changelog/release notes + check `peerDependencies` conflicts and our usage of the changed API. Classify `safe` | `needs-code-change` | `breaking`.
4. Record both axes; the action falls out: reachable + safe-upgrade = do now; reachable + breaking = plan; unreachable = defer/ignore with reason.

## Output: `.audit/deps.md`
| pkg | advisory | prod? | reachable (evidence) | fix version | upgrade | action |
|---|---|---|---|---|---|---|
| e.g. lodash | proto-pollution | prod | yes — `merge(req.body)` at x.js:44 | 4.17.21 | patch, safe | **upgrade now** |
| e.g. some-dev-tool | ReDoS | dev | n/a | 2.0 | major, breaking | ignore (dev-only) |

Plus a one-paragraph summary: "N of M high/critical are actually reachable; K are safe upgrades, J are breaking, the rest are dev-only/unreachable." No noise.
