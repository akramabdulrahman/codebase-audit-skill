# codebase-audit — v1 (original baseline)

> **This is the `v1` branch: the original, LLM-heavy, human-gated audit skill, preserved for comparison.**
> The current/maintained skill lives on **`main`** (the deterministic v2 engine — probes + analyzers +
> facet contract + tests). This branch is a frozen snapshot; it is not maintained.

## What v1 is
The first design: a **decision aid** for auditing unfamiliar/legacy code without reading the whole repo.
Method = deterministic grep pre-filters + **LLM judgment** + **git archaeology** + a **human gate** + a
**live regression net**, organized as bug-class lenses and staged human-gated passes.

- `SKILL.md` — the pipeline and the four commitments (don't overwhelm, don't waste time, prevent mistakes, evidence always).
- `reference/bug-classes.md` — the C1–C8 lenses (BOLA/IDOR, async-error, query-boundary, divergent-state, injection, lifecycle, PII, pattern-conformance) with signature + deterministic pre-filter + LLM check + fix pattern.
- `stages/1-5` — actor-model → consistency-scan → dep-triage → regression-net → validate (human gate between).
- `auditctl` — the deterministic evidence layer (index/profile/entrypoints/scan/history/trace/report); `history`/`trace` = the git archaeology.

## Why it was superseded
v1's accuracy ceiling was the **regex route parser** and the **LLM-inferred actor model** (noise + nondeterminism).
`main` (v2) replaces the extraction with deterministic AST/ORM probes and a set-algebra analysis layer, keeps
v1's human gate + evidence ladder + regression net, and adds a coverage signal and a 35-test harness.

**Use `main`.** This branch documents where the design started.
