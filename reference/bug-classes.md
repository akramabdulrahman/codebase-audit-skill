# Recurring bug-class lenses (the reusable "training set")

Mined from the ICS `issues.md` + git bug-fix history + the audit run. These are the **classes to scan for** instead of scanning whole code. Each has: a signature, a deterministic pre-filter (cheap, narrows the surface), an LLM check (the judgment), and the known **fix pattern** (so the scan can tell a fixed site from a vulnerable one). Generalize the specifics per project; the shapes transfer.

Seed `.audit/bug-classes.yaml` from this; tune per project during actor-model.

---

## C1 — Broken Object-Level Authorization (BOLA / IDOR)  *(dominant class)*
Maps to **OWASP WSTG 4.5.2** (bypass authz schema), **4.5.3** (privilege escalation), **4.5.4** (IDOR); OWASP API #1. This is the class generic diff-based scanners provably MISS — the empirical study of 100+ bug-bounty disclosures (arxiv 2605.25865) shows the two biggest families are *state-changing* and *cross-tenant*, which read-only testing never catches.

- **Core signature:** a role gate lets a NON-global actor act on an object selected by id/param, and the handler/query does not bind that object to the caller's ownership (session-derived, not request-derived). A role check is NOT object authorization.
- **Oracle:** the human-verified `actors.yaml` ownership predicate per (actor, resource). The scan flags every route whose handler omits it.

**Six BOLA families to check (with real-world frequency — prioritize by it):**
1. **Action-Level / state-changing (41.7% — LARGEST, often missed).** DELETE/PATCH/POST/approve/transfer on another's object. *Prioritize mutating routes* — read-only test protocols miss the dominant family. (ICS: `deleteSession`/`editSession`/`reopenEvent`.)
2. **Direct object read (36.9%).** GET another's object by id (sequential int, UUID, or encoded).
3. **Tenant / workspace isolation (8.3%).** Cross-org/workspace: server checks the session is valid but not tenant membership. (ICS: `employerOrgAdmin`/`tpOrgAdmin` acting across orgs — the exact class.)
4. **Workflow-context / stale authz (6.0%).** Authz that passes only because of object lifecycle state (draft/archived/deleted/finalised); the check isn't re-evaluated after a state transition. *Ties to C6.*
5. **Chained disclosure (4.8%).** Id harvested from endpoint A, replayed at endpoint B which validates the token but not ownership. → **map which endpoints return ids**, then test them elsewhere.
6. **Object rebinding (2.4%, insidious).** Client-supplied ownership field in the request **body** (`owner_id`, `account_id`, `user_id`, `req.body.status`) trusted over the session. (ICS issues.md: `joinWaitingList` trusts `req.body.status`.) → grep handlers for ownership/state fields read from `req.body`/`req.query` instead of `req.user`.

**Direction:** horizontal (peer↔peer, 85.7%) AND **vertical** (user→admin, 11.9% but higher severity — don't skip). WSTG 4.5.3.

- **Pre-filter (deterministic):** `auditctl` IDOR-surface = authed routes with an object param that are not admin-only; split P1 (mutating) first. Also grep handlers for `req.body`/`req.query` fields named like owners/ids/status (family 6).
- **LLM check:** open handler+query; confirm the ownership predicate binds the object to `req.user`'s scope, session-derived. Check families 4 (re-check after state change) and 5 (does this endpoint leak ids used elsewhere).
- **Fix pattern (ICS reference impl):** `finaliseSession.js:50-79` correctly binds session→caller scope; the 10 broken routes just omit it. Port it (or a shared ownership-guard middleware) → whole class closes. Derive ownership from session, never request body.
- **Verify (dynamic, = the regression net's red repro):** provision two accounts/orgs A & B; A acts on B's object → expect 403. This is the OWASP **Autorize / AuthMatrix** method in test form — an access-control matrix (role × endpoint → allow/deny) replayed per role.
- **Evidence:** arxiv 2605.25865; issues.md#4; git `tpOrgAdmin export scope`, `trainer auth bypass`, `prog man org admin`, `orphaned org admins`.

## C2 — Express-4 async error handling
- **Signature (a):** `await` outside a `try/catch` in an async handler → unhandled rejection → **request hangs** (no `next`, no response). **(b):** a fire-and-forget promise (`send…()` without `await`) inside a try → rejection escapes → **silently swallowed** (no Sentry).
- **Pre-filter:** grep controllers/handlers for `await` before the first `try`, and for un-`await`ed calls to known async helpers (email/db) inside `try`.
- **LLM check:** confirm the await/site can reject on reachable input (bad ObjectId → CastError; null lookup → TypeError).
- **Fix pattern:** wrap handler body in top-level `try/catch` → `next(err)`; add `await` to fire-and-forget sends. (An async-wrapper middleware fixes the class globally.)
- **Evidence:** issues.md#6,#7.

## C3 — Mongo empty-array / boundary in queries
- **Signature:** an operator handed an empty or malformed value — `$and:[]`/`$or:[]` (rejected), `$skip:(page-1)*limit` with unvalidated `page` (negative/NaN), `$lt`/`$gt` against an **optional** field (missing field never matches → silently excluded).
- **Pre-filter:** grep for `$and`/`$or`/`$nor` built from a variable array; `$skip`/`$limit`/`page`/`offset` from `req.query`; range matches on schema-optional fields.
- **LLM check:** trace whether the array can be empty / the number can go <0 / the field can be absent, on a reachable request.
- **Fix pattern:** guard the stage when the array is empty; `parseInt`+clamp pagination; seed range queries with an existence check or match-all.
- **Evidence:** issues.md#8; BUG-5,6.

## C4 — Divergent domain state (dual source / dual writer / mirror)
- **Signature:** one fact in ≥2 stores (dual-writer) or ≥2 definitions (client/server mirror) that can drift. Also "in-scope" counting logic implemented twice.
- **Pre-filter:** grep the same field/concept across `models/`, `queries/`, `taskScheduler/`, and client+server `constants/`.
- **LLM check:** find a sequence where one site updates and the other doesn't → wrong read downstream.
- **Fix pattern:** single source of truth (derive the second from the first), or a shared constants module, or write both in one transaction.
- **Evidence:** DUP-1..6; git `custom orgs weren't ever in scope for ommt`, `response rates`.

## C5 — Input trust / injection / data exposure
- **Signature:** DB/user value interpolated into raw HTML email without escaping; `req.body` field trusted verbatim into a write; secrets returned by default (no `select:false`); plaintext single-use tokens.
- **Pre-filter:** grep email helpers for `${…}` in HTML bodies; `req.body.<x>` passed into upserts; schema fields `password|token|secret` without `select:false`.
- **LLM check:** is the value attacker-influenced and does it reach a sink (email client / DB / response)?
- **Fix pattern:** escape at interpolation or use provider dynamic templates; hardcode server-controlled enums; `select:false` + explicit projection; hash single-use tokens.
- **Evidence:** issues.md#5,#10,#11.

## C6 — Lifecycle / finalisation idempotency
- **Signature:** a state-transition endpoint (finalise/reopen/withdraw/register) runs without checking current state → re-running duplicates side effects (emails, appended arrays, upgrades); reopen doesn't reverse prior effects.
- **Pre-filter:** grep for state-transition controllers; check for a guard on the state flag (`finalisedAt`, `status`) before side effects.
- **LLM check:** can the endpoint be called twice / out of order (double-click, retry, reopen→finalise)? Are side effects idempotent?
- **Fix pattern:** guard on current state before mutating; make writes idempotent (`$set`/`$addToSet` not `$push`/`$concatArrays`).
- **Evidence:** BUG-2; git `prevent-signups-post-finalisation`.

## C7 — Data / PII & seed hygiene
- **Signature:** real PII/special-category data in git-tracked seed/fixtures; secrets committed; weak crypto cost; broken `ref` strings that make `populate` throw.
- **Pre-filter:** scan seed/fixture JSON for real-looking emails/domains, free-text health terms, meeting links, token hashes; grep schemas for `ref:` vs registered model names; bcrypt cost.
- **LLM check:** confirm the data is real (not `user-N@test.com`) and the ref mismatch is reachable via `populate`.
- **Fix pattern:** in-repo anonymiser + history purge (BFG/filter-repo); align `ref` to registered names; raise bcrypt cost ≥10.
- **Evidence:** issues.md#9,#10.

## C8 — Pattern-conformance / canonical deviation  *(the "it looks like X but isn't done how X is done" lens)*
The highest-leverage, hardest-to-see class: code that is clearly *reaching for* a well-known pattern (an access model, a data relationship, a state machine, pagination…) but implements it in a **non-standard way that causes the defect** — inconsistency, drift, a security gap, un-maintainability. Generic scanners can't see this; it needs recognizing the *intended abstraction* and comparing it to how that abstraction is **canonically** done.

**Method (per subsystem / suspicious construct):**
1. **Name the pattern it's attempting.** "This is trying to be RBAC / an M:N relationship / a state machine / offset pagination…". State it explicitly.
2. **If the intended pattern is ambiguous, RECOVER INTENT FROM GIT HISTORY before asking.** `git blame` the lines and `git log -S"<symbol>"` (pickaxe) / `git log -L` the affected code to reconstruct the *progression of decisions*. The commit sequence + messages usually reveal whether the current shape is (a) a deliberate design, (b) accreted patchwork, or (c) an **incomplete migration** — e.g. an original denormalized field later patched, one read-path at a time, to derive from a relation (each a separate "it's stale here too" fix) shows the direction of travel *is* the intended end-state. That often ANSWERS the question and, better, changes the fix (from "reconcile two sources" to "finish the migration"). **Only ask the human (`open_question` for Gate 1) if the history is inconclusive.** Worked example: ICS `programmesCanDeliver` — blame showed the stored array (#782) predated a chain of derive-from-`Programme.managers` fixes (#713 auth, #991 login, #2004 search) → an unfinished consolidation, not a cache-vs-concept choice.
3. **Compare to the CANONICAL form from a NAMED reference framework** (table below) — *not* to your own stylistic preference or an invented "best practice." Cite the framework.
4. **Flag only deviations that cause a real consequence** (drift/inconsistency, an authz gap, a correctness bug, a scaling failure). A non-canonical-but-intentional-and-safe choice is NOT a finding — say so and move on. Distinguish "non-standard AND broken" from "non-standard but fine."
5. **State the deviation + the canonical fix**, referencing the framework.

**Reference frameworks by context** (pick the one that fits — the situation dictates the yardstick):
| Context | Compare against | Common deviations to catch |
|---|---|---|
| Authorization | **RBAC** (roles→permissions), **ABAC** (attribute/policy), **ReBAC** (relationship, e.g. Zanzibar), OAuth2 scopes, OWASP Authz Cheat Sheet | role-string checks doing an ABAC job; ownership derived from request not subject; permission set treated as a role (ICS `sessionManagementRoles`) |
| Data modeling | **Relational normalization** (1NF–3NF, single source of truth, **associative/junction entity** for M:N), **DDD aggregates**, CQRS/event-sourcing for dual read/write | a fact stored on both sides of a relation with no reconciliation; denormalized array where a join belongs; **dual-sourced by role** (ICS DUP-7) |
| State / workflow | explicit **state machine**, **idempotency keys**, saga, ACID transaction boundaries | side-effecting transition with no current-state guard; partial multi-step update w/o transaction |
| Concurrency / distributed | **CAP**, idempotency, at-least-once vs exactly-once, outbox/inbox | non-idempotent retryable op; read-modify-write race |
| API design | **REST** method semantics (safe/idempotent), **cursor vs offset pagination**, JSON:API | mutating GET; offset pagination on mutating data; unvalidated `page` |
| Auth(N) / secrets | OWASP **ASVS**, session-vs-JWT, secure-cookie flags, token rotation | plaintext single-use tokens; secrets returned by default (no `select:false`) |
| Money / numeric | minor-units/decimal (never float), double-entry | float currency; single-entry ledger |

- **Pre-filter (deterministic):** none — this is an LLM-judgment lens applied to the core subsystems (auth model, data relationships, lifecycle, pagination). `auditctl history` + the actor-model tell you which subsystems matter.
- **Discipline:** the finding must name (a) the pattern attempted, (b) the canonical reference, (c) the specific deviation, (d) the real consequence. No "I'd prefer" — measure against the standard, or ask.
- **Evidence:** DUP-7 (dual-sourced relation vs normalization/single-source); ICS `sessionManagementRoles` = permission-set-as-role (RBAC vs ABAC ownership); OWASP for the authz cases.
