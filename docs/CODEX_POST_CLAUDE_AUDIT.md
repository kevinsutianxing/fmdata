# Codex Post-Claude Audit Action Plan

## Purpose

This document records Codex's review of the current `main` branch after the Claude Code update. It is written for Claude Code / OpenClaw / Codex operators who will continue improving `fmdata`.

The short version:

```text
Keep the Claude Code additions that improve discovery and operator usability.
Do not treat the current state as production-ready financial research infrastructure.
Tighten Agent permissions, fix catalog include behavior, align deployment docs with reality,
and expand tests from snapshot unit tests into full data-plane gate tests.
```

## Current assessment

`fmdata` is now on the right trajectory. The project is no longer just a convenience cache around Tushare/Akshare; it is becoming a financial data plane for AI research agents.

Strong foundations already present:

- unified FastAPI entry point for agents;
- local cache and recipe-based acquisition;
- QG proxy support for Akshare-style traffic;
- stock-code validation endpoint;
- catalog/search additions for agent-friendly data discovery;
- immutable research snapshot API;
- snapshot manifest with raw/normalized hashes;
- explicit `validation_status=PENDING` so fmdata does not self-certify;
- initial research snapshot contract tests;
- operator-facing `AGENT_GUIDE.md`.

Main remaining risk:

```text
The system still mixes exploratory Agent convenience with production-grade financial evidence rules.
```

That must be fixed before Codex/DeerFlow/Claude Code can safely use `fmdata` for material investment research, backtesting, valuation, or report conclusions.

---

# Non-negotiable design rules

## Rule 1 — Maker and checker must remain separate

`fmdata` may create snapshots. It must never validate them for research use.

Required invariant:

```text
fmdata output: validation_status=PENDING
external controller/gate output: validation_status=VALIDATED or rejected
```

Do not allow a service response, Agent summary, API success boolean, or DataFrame preview to substitute for external validation.

## Rule 2 — `/data/*` is exploratory, not evidence

Allowed:

```text
/catalog
/search
/data/*
/validate
```

for discovery, debugging, lightweight inspection, and non-material exploration.

Not allowed:

```text
Using /data/* directly in a formal report, valuation, backtest, model, buy/sell recommendation, or material conclusion.
```

Material research data must follow:

```text
/research/snapshots
  -> manifest
  -> raw download
  -> normalized download
  -> independent SHA-256 verification
  -> quality checks
  -> deterministic acquisition gate
```

## Rule 3 — Research Agents must not hold admin authority

Research Agents may:

- browse catalog/search;
- call `/validate`;
- request PENDING snapshots;
- resolve entities;
- report missing data needs.

Research Agents must not:

- read or use `FMDATA_ADMIN_KEY`;
- create or modify recipes;
- trigger dangerous `agent` or `remote` recipes;
- add script allowlist entries;
- add remote hosts;
- write directly into `store/` or snapshot storage;
- treat successful refresh as research validation.

Admin/data-steward tasks must be separate from research-agent tasks.

---

# P0 fixes — must do first

## P0.1 Rewrite Agent permission guidance

Files to update:

```text
AGENT_GUIDE.md
README.md
docs/RESEARCH_SNAPSHOT_API.md
```

Problem:

`AGENT_GUIDE.md` currently tells all agents that if data is missing they should register a recipe, and it describes admin-key-backed refresh/recipe workflows in the general agent guide. This is too broad for financial research governance.

Required change:

Add a clear role matrix:

```text
Role: Research Agent
Can:
  - GET /catalog
  - GET /search
  - GET /validate
  - POST /research/entities/resolve
  - POST /research/snapshots
Cannot:
  - read FMDATA_ADMIN_KEY
  - POST /recipes
  - POST /fetch-stale
  - trigger agent/remote recipes
  - edit allowlists
  - write data files

Role: External Controller
Can:
  - rerun approved snapshot requests
  - download manifest/raw/normalized objects
  - verify hashes
  - write run-local manifests
  - run acquisition gate
Cannot:
  - modify provider recipes without data-steward approval

Role: Data Operator
Can:
  - refresh existing safe recipes
  - inspect failed fetches
  - maintain cache health
Cannot:
  - approve data for research use

Role: Data Steward/Admin
Can:
  - create or modify recipes
  - approve semantic metadata
  - manage admin keys
  - manage script/remote allowlists
  - review licensing and redistribution constraints
```

Acceptance criteria:

- `AGENT_GUIDE.md` no longer implies all agents can register recipes.
- `README.md` no longer says generic agents can register new sources without qualification.
- `docs/RESEARCH_SNAPSHOT_API.md` says research agents should receive only `FMDATA_RESEARCH_KEY`.
- Admin workflows are moved to a separate operator/admin section.

## P0.2 Make material research snapshot-first

Problem:

The guide says routine usage should use `/data/*` and snapshot is only advanced. That is fine for exploration, but dangerous for formal financial research.

Required wording:

```text
Use /data/* for exploration only.
Use /research/snapshots for anything entering a report, model, backtest, valuation, screen, recommendation, or claim ledger.
```

Acceptance criteria:

- Every guide distinguishes exploration from material research.
- Material research data path explicitly requires snapshot + external gate.
- No doc suggests `/data/*` is sufficient for formal research conclusions.

## P0.3 Stop treating old PR #1 as the active review vehicle

Problem:

Claude Code appears to have merged/recreated the research snapshot work on `main`, leaving the old `feat/research-snapshot-api` PR history diverged. That PR should not be used as the live review track.

Required action:

- Mark old PR #1 as superseded by current `main` + follow-up fix branch.
- Continue work from `main` on a new branch, currently:

```text
fix/post-claude-audit
```

Acceptance criteria:

- Future changes are reviewed through a new PR from `fix/post-claude-audit` to `main`.
- Old PR is not merged unless it is explicitly rebased and revalidated.

---

# P1 fixes — code and tests

## P1.1 Fix `/catalog?include=` alias behavior

File:

```text
fmdata/server.py
```

Current problem:

The public parameter examples use:

```text
include=dated
include=monthly
include=statement
include=code_split
include=all
```

The internal classifiers return:

```text
dated_snapshot
monthly_snapshot
statement_by_code
code_split
named
```

Current logic compares the raw query token directly against classifier names, so `include=dated`, `include=monthly`, and `include=statement` do not reliably do what the guide says.

Implement:

```python
INCLUDE_ALIASES = {
    "dated": "dated_snapshot",
    "dated_snapshot": "dated_snapshot",
    "monthly": "monthly_snapshot",
    "monthly_snapshot": "monthly_snapshot",
    "statement": "statement_by_code",
    "statement_by_code": "statement_by_code",
    "code_split": "code_split",
    "all": "all",
}
```

Normalize query tokens before filtering:

```python
raw_include = {part.strip() for part in (include or "").split(",") if part.strip()}
include_set = {INCLUDE_ALIASES.get(part, part) for part in raw_include}
```

Also decide and document the correct behavior for `include=code_split`:

- either allow full expansion when explicitly requested;
- or return a capped preview plus a warning;
- but do not document full expansion and silently suppress it.

Acceptance criteria:

- `/catalog?include=dated` includes dated snapshots.
- `/catalog?include=monthly` includes monthly snapshots.
- `/catalog?include=statement` includes statement-by-code datasets.
- `/catalog?include=code_split` behavior is explicit and tested.
- `/catalog?include=all` includes everything or a documented capped representation.

## P1.2 Add catalog/search tests

File suggestion:

```text
tests/test_catalog_search.py
```

Add fixture monkeypatching `list_datasets()` and recipe loading to include:

```text
named dataset: cpi
named dataset: analyst_consensus
code split: 000001
statement by code: income_000001
dated snapshot: cninfo_ratings_20210129
monthly snapshot: macro_series_202101
```

Tests:

```text
default catalog hides code/date/month/statement noise
include=dated includes dated snapshot
include=monthly includes monthly snapshot
include=statement includes statement dataset
include=code_split follows documented behavior
include=all follows documented behavior
category filter works
q filter matches name and description
/search rejects empty q
/search ranks exact name above partial description match
/search penalizes hidden noisy datasets but can still find them
```

Acceptance criteria:

- CI runs these tests.
- These tests fail on the current include-alias bug before the fix.

## P1.3 Strengthen recipe mutation permissions

Files:

```text
fmdata/server.py
fmdata/recipe_fetcher.py
AGENT_GUIDE.md
```

Current state:

- Some mutating endpoints require admin key only for dangerous recipe sources.
- Some docs imply general agents can register recipes.
- `agent` and `remote` sources exist and are powerful.

Required policy:

- `POST /recipes` must require admin/steward authorization.
- `POST /fetch-stale` must require admin/operator authorization.
- `POST /fetch/{name}` should require admin/operator authorization at least when:
  - source is `agent`;
  - source is `remote`;
  - recipe output overwrites existing data;
  - recipe lacks approved semantics and is being used for research.

Acceptance criteria:

- Missing/invalid admin key blocks recipe creation and dangerous fetches.
- Research key alone cannot mutate recipes or trigger dangerous sources.
- Tests cover no-key, research-key, and admin-key behavior.

## P1.4 Split admin and research auth in research API

File:

```text
fmdata/research_server.py
```

Current behavior:

`require_research_key()` accepts either `FMDATA_RESEARCH_KEY` or `FMDATA_ADMIN_KEY`.

This is operationally convenient, but it blurs roles. If kept, it must be explicit and audited.

Preferred change:

- Keep `FMDATA_RESEARCH_KEY` as the normal research API key.
- Permit `FMDATA_ADMIN_KEY` only when an environment flag is set, for example:

```text
FMDATA_ALLOW_ADMIN_AS_RESEARCH_KEY=true
```

Default should be `false` for production.

Acceptance criteria:

- Test proves research key works.
- Test proves admin key does not work by default for research endpoints.
- Optional compatibility flag allows admin key only when explicitly enabled.
- Docs match the behavior.

---

# P2 fixes — deployment and runtime reproducibility

## P2.1 Align systemd unit and deployment runbook

Files:

```text
fmdata.service
docs/DEPLOY_AND_VALIDATE.md
```

Current mismatch:

`fmdata.service` is currently written like a user-service unit and uses `/usr/bin/python3`, while the runbook still describes a `.venv` and system-level deployment.

Preferred structure:

```text
deployment/user-service.md
  - ~/.config/systemd/user/fmdata.service
  - systemctl --user
  - ExecStart=/home/ubuntu/fmdata/.venv/bin/python -m uvicorn ...
  - good for current SZ81-style operator deployment

deployment/system-service.md
  - /etc/systemd/system/fmdata.service
  - dedicated fmdata user
  - protected /var/lib/fmdata/snapshots
  - systemd hardening
  - good for production deployment
```

Do not use `/usr/bin/python3` for production-like service execution. Use the project venv to avoid version drift.

Acceptance criteria:

- Unit file and runbook describe the same deployment mode.
- `.venv/bin/python` is used for service execution.
- `systemctl --user` vs `sudo systemctl` is not mixed in one profile.
- Health, auth, snapshot, and hash validation commands match the selected profile.

## P2.2 Restore compatible hardening gradually

Current note says several hardening directives were removed because user services lacked required capabilities.

Do not blindly re-add all directives. Instead:

1. Keep `UMask=0077`.
2. Test whether the user service supports:
   - `NoNewPrivileges=true`
   - `PrivateTmp=true`
   - `ProtectHome=read-only` or a compatible variant
   - `ReadWritePaths=` for only fmdata store/snapshot dirs
3. Reserve stricter options for the system-service profile.

Acceptance criteria:

- `systemd-analyze --user verify` or equivalent verification is documented.
- Service starts cleanly after hardening.
- Snapshot directory and `.env` are protected.

---

# P3 fixes — data semantics and recipe governance

## P3.1 Add recipe semantic schema

New file suggestion:

```text
schemas/recipe_semantics.schema.json
```

Minimum fields by category:

Market/factors/strategy:

```text
frequency
timezone
unit
currency
adjustment
revision_policy
available_at_col or available_at_rule
license_or_usage_note
```

Fundamentals:

```text
frequency
timezone
currency
scale
accounting_scope
period_basis
published_at_col or published_at_rule
available_at_col or available_at_rule
revision_policy
license_or_usage_note
```

Macro:

```text
frequency
timezone
unit or scale
vintage_policy
revision_policy
published_at_col or published_at_rule
available_at_col or available_at_rule
license_or_usage_note
```

Acceptance criteria:

- Schema exists.
- Recipe lint can validate against it.
- Missing fields produce actionable messages.

## P3.2 Add recipe lint and readiness levels

New script suggestion:

```text
scripts/recipe_lint.py
```

Readiness levels:

```text
L0 cache-only
  Can be stored and inspected, not material for research.

L1 snapshot-capable
  Has file, category, source, date column or equivalent, and stable output.

L2 research-ready
  Has point-in-time availability, unit/scale, currency where relevant,
  adjustment/revision/accounting/vintage semantics as applicable.

L3 backtest-ready
  Has historical entity/index membership, delisting/symbol change handling,
  announcement/vintage release calendar, and survivorship-bias controls.
```

Report output:

```json
{
  "recipes_total": 0,
  "levels": {"L0": 0, "L1": 0, "L2": 0, "L3": 0},
  "blocking_missing_fields": [],
  "dangerous_sources": [],
  "research_ready_candidates": []
}
```

Acceptance criteria:

- CI runs recipe lint.
- Existing recipes are not falsely claimed as research-ready.
- At least one fixture recipe demonstrates L2 pass and L0 fail.

## P3.3 Convert first real recipes to research-ready examples

Pick a small set first; do not attempt to certify everything at once.

Suggested initial set:

```text
1 market dataset
  daily_basic or stock_daily_raw

1 fundamental dataset
  actual_financials or stock_fina

1 macro dataset
  cpi or pmi

1 reference dataset
  trade_calendar or stock_list
```

For each, document:

- source function;
- provider terms note;
- date column;
- observation date semantics;
- publication/availability timing;
- unit/scale;
- revision policy;
- survivorship limitations;
- known missing controls.

Acceptance criteria:

- `/research/catalog` reports these as `research_ready=true` only when semantics are genuinely complete.
- A snapshot for each returns `OK` in a live deployment.
- A materializer/gate test consumes at least one of them.

---

# P4 fixes — entity master and point-in-time research

## P4.1 Build a historical entity master

Current `/research/entities/resolve` correctly returns `PARTIAL` because the stock reference is based on active listings and is not a historical entity master.

Add a true entity master with:

```text
ts_code
symbol
name
exchange
list_date
delist_date
name_history_start
name_history_end
industry_classification_version
source
retrieved_at
```

Acceptance criteria:

- Delisted securities are represented.
- Symbol/name changes are represented.
- `as_of` matters in entity resolution.
- Ambiguous names return `CONFLICTED`, not best guesses.

## P4.2 Add index membership and survivorship controls

For backtests and A-share monitoring, entity and index membership matter as much as prices.

Add or plan:

```text
index_constituents_history
index_weights_history
industry_membership_history
```

Acceptance criteria:

- Backtests can request constituents as of a historical date.
- Current membership is not silently used for historical periods.

---

# P5 fixes — script and remote execution hardening

## P5.1 Move hardcoded script allowlist to config

Current allowlist is hardcoded in `recipe_fetcher.py` and includes absolute paths, including paths outside the fmdata repository.

Create:

```text
config/script_allowlist.yaml
config/remote_allowlist.yaml
```

Each script entry should include:

```yaml
alias: refresh_sw_daily_full
path: /home/ubuntu/fmdata/scripts/refresh_sw_daily_full.py
sha256: <script hash>
allowed_args_schema: {}
allowed_output_paths:
  - /home/ubuntu/fmdata/store/market/
max_runtime_seconds: 900
allowed_env: []
approval_required: true
```

Each remote entry should include:

```yaml
host: hk43
allowed_commands:
  - alias: fetch_fred
    command_prefix: python3 /path/to/fetch_fred.py
    allowed_env:
      - FRED_API_KEY
    max_runtime_seconds: 300
```

Acceptance criteria:

- `agent` source validates script hash before execution.
- Arguments are schema-checked.
- Output path is constrained.
- Remote commands are alias-based, not free-form strings.
- Tests cover disallowed path, changed hash, disallowed env, disallowed output.

---

# P6 fixes — CI expansion

Current CI runs only the snapshot contract suite. Expand it.

## Required CI jobs

```text
research-snapshot-contract
  current tests/test_research_snapshot.py

legacy-api-contract
  health/status/catalog/search/validate/data fixture tests

permission-contract
  no key / research key / admin key behavior

recipe-lint-contract
  schema + readiness level tests

security-contract
  path escape, secret redaction, script allowlist, remote allowlist
```

Acceptance criteria:

- CI fails if `/catalog?include=dated` regresses.
- CI fails if docs imply research agents can use admin workflows.
- CI fails if snapshot service ever emits `VALIDATED`.
- CI fails if missing semantics are silently treated as `OK`.

---

# Suggested PR sequence

## PR A — Governance and discovery correctness

Scope:

- rewrite `AGENT_GUIDE.md` permission model;
- update `README.md` recipe/admin language;
- clarify `/data/*` vs `/research/snapshots`;
- fix `/catalog?include=` aliases;
- add catalog/search tests;
- add research/admin auth tests.

This is the highest priority PR.

## PR B — Deployment alignment

Scope:

- choose user-service profile for current host;
- use `.venv/bin/python`;
- split user-service vs system-service docs;
- update `docs/DEPLOY_AND_VALIDATE.md` from current `main` reality;
- add service verification checklist.

## PR C — Recipe semantics and lint

Scope:

- add recipe semantic schema;
- add recipe lint;
- classify readiness L0-L3;
- certify first 3-4 recipes as examples.

## PR D — Live data-plane acceptance

Scope:

- deploy current fmdata;
- create real market/fundamental/macro snapshots;
- download raw + normalized;
- verify hashes;
- materialize into multi-agent-pipeline;
- run acquisition gate;
- keep evidence bundle.

---

# Claude Code execution prompt

Use this prompt in a clean Claude Code session if desired:

```text
You are improving kevinsutianxing/fmdata as a production-grade financial data plane for AI research agents.

Start from branch fix/post-claude-audit, based on current main.

Do not add new data sources first. Fix governance and correctness first.

Implement PR A only:
1. Rewrite AGENT_GUIDE.md so Research Agents cannot use FMDATA_ADMIN_KEY, register recipes, run agent/remote recipes, or treat /data/* as material evidence.
2. Update README.md to distinguish exploratory /data access from formal /research/snapshots + external gate.
3. Fix /catalog include alias behavior in fmdata/server.py for dated/monthly/statement/code_split/all.
4. Add tests/test_catalog_search.py covering default hiding, include aliases, category filter, q filter, and /search ranking/empty query.
5. Add auth tests proving research key cannot perform admin mutation workflows and that snapshot output remains PENDING.
6. Keep all existing legacy endpoints compatible.
7. Run the existing snapshot contract tests and all new tests.

Acceptance gates:
- python -m pytest -q tests/test_research_snapshot.py tests/test_catalog_search.py <new auth tests>
- /catalog?include=dated behavior matches docs
- no doc tells generic agents to read FMDATA_ADMIN_KEY
- no doc implies /data/* is sufficient for formal research conclusions
- fmdata never emits validation_status=VALIDATED

Do not merge or mark complete unless tests pass and the docs match the implemented permissions.
```

---

# Completion definition

The project should not be considered production-ready until all of these are true:

- Research Agents have no admin mutation path.
- Material research is snapshot-first and gate-enforced.
- Catalog/search behavior is tested.
- Deployment docs match actual service mode.
- At least one market, one fundamental, and one macro dataset have verified semantics.
- Live snapshot -> materialization -> acquisition gate passes.
- Negative controls fail as expected: wrong key, tampered snapshot, missing semantics, future availability.
