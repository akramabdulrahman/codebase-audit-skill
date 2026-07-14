# The `.audit/` artifact bus — how the skills pass state (token technique)

The four skills are **separate context windows**. They never share a conversation; they communicate only through files under `<repo>/.audit/`. Each skill loads only the artifacts it needs, so no stage carries another stage's tokens. (This is the fix for "84% of cost is context" — small, scoped contexts + a file bus.)

## Artifacts (producer → consumers)

| Artifact | Written by | Read by | Human-gated? |
|---|---|---|---|
| `index.json`, `project-profile.json`, `routes.jsonl`, `grep-hits.json` | `auditctl` (deterministic) | all | no |
| `actors.yaml` | **actor-model** | consistency-scan, regression-net | **YES — gate 1** |
| `bug-classes.yaml` | seeded from `reference/bug-classes.md`, tuned per project | consistency-scan | optional |
| `findings.jsonl` + `findings.md` | **consistency-scan** | regression-net | **YES — gate 2** |
| `deps.md` | **dep-triage** | (report) | review |
| `flows.yaml` | actor-model (critical flows) → refined | regression-net | with gate 1 |
| `tests/*`, `repro/*` | **regression-net** | humans | review |
| `report.md` | `auditctl report` (renders the above) | humans | — |

## Gate protocol (driver seat)
A skill that ends at a gate writes its artifact with `status: proposed` and STOPS. It does not proceed to the next skill. The human reviews the artifact, edits/approves, and sets `status: approved`. The next skill refuses to run on a `proposed` upstream artifact (checks the flag). This keeps the human in control between every stage.

Example header the gated artifacts carry:
```yaml
_meta:
  status: proposed        # proposed -> approved (human sets)
  produced_by: actor-model
  git_sha: <sha>
  approved_by: null
  approved_at: null
```

## Invocation (packaging TBD — decide after stage 1)
- **As 4 skills:** run each skill in its own session; the file bus carries state. Max isolation, max token saving.
- **As 1 skill, 4 stages:** one session runs the stages sequentially, pausing at each gate.
Either way the artifact contract above is identical — the packaging is just how many context windows you spend.
