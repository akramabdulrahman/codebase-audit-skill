# Stack adapters — how the skill brings a project to life

`auditctl profile` classifies the project and records **`stack`** + a `lifecycle` block in `.audit/project-profile.json`. Stage 4 (live proof) reads `stack` and loads the matching **adapter** here — `stacks/<stack>.yaml` — which declares the *generic* lifecycle for that stack. The project-specific values (script names, DB URIs, ports) come from the profile; the adapter says *what steps exist and in what order*.

**Teaching a new stack = adding one file here.** Copy `_template.yaml`, fill the five phases, done. The skill degrades to `unknown` (manual) if no adapter matches.

## The contract every adapter provides (5 phases, in order)
1. **install** — bring deps in; **capture the audit/vulnerability report** (npm audit / pip-audit / bundle audit / govulncheck) to `.audit/live/deps-warnings.*` — this is a first-class output, not a side effect.
2. **db_setup** — discover project setup scripts (build:db / seed / migrate / docker-compose) and the **test-DB safety gate** (never reset a DB whose URI == dev/prod).
3. **run** — start the app; a **health check** confirms it's up.
4. **test** — the LIVE runner (verified by running, not declared), by level (unit/integration/e2e).
5. **teardown** — stop the app / container.

## Current adapters
- `node-express-mongoose.yaml` — MERN / Express + Mongoose (covers ICS & gwent).
- `_template.yaml` — copy this to add a stack (python/rails/go/node-sql/…).
