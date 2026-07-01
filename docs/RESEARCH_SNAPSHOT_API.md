# fmdata Research Snapshot API

## Purpose

The legacy fmdata endpoints are convenient cache/query APIs. They are not, by themselves, sufficient evidence for reproducible financial research.

The research API adds an authenticated, snapshot-first boundary:

```text
registered fmdata dataset
  -> immutable raw cache snapshot
  -> bounded normalized snapshot
  -> content hashes
  -> semantic and lineage manifest
  -> validation_status=PENDING
  -> external deterministic research gate
```

fmdata never promotes its own output to `VALIDATED`.

## Start the service

Set a research-only key in the service environment:

```bash
export FMDATA_RESEARCH_KEY="replace-with-secret"
fmdata serve --port 1934
```

`FMDATA_ADMIN_KEY` is also accepted for administrative callers, but research agents should receive only `FMDATA_RESEARCH_KEY`.

The service continues to expose legacy endpoints and adds:

```text
GET  /research/health
GET  /research/catalog
POST /research/entities/resolve
POST /research/snapshots
GET  /research/snapshots/{snapshot_id}/manifest
GET  /research/snapshots/{snapshot_id}/data
GET  /research/snapshots/{snapshot_id}/raw
```

All routes except `/research/health` require `X-Research-Key` or `X-API-Key`.

## Create a snapshot

```bash
curl -s -X POST http://127.0.0.1:1934/research/snapshots \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "daily_basic",
    "as_of": "2026-06-30",
    "start_date": "2026-06-01",
    "end_date": "2026-06-30",
    "entity_ids": ["000001.SZ"],
    "fields": ["pe_ttm", "pb", "total_mv"],
    "expected_semantics": {
      "frequency": "daily",
      "currency": "CNY"
    }
  }'
```

The response includes:

- stable `snapshot_id`;
- source/evidence/dataset IDs;
- redacted request parameters and request hash;
- immutable raw and normalized paths and SHA-256 hashes;
- observation range and source timing metadata when available;
- schema fingerprint and row counts;
- unit, currency, timezone, frequency, adjustment, revision, accounting, and licensing metadata when declared;
- explicit limitations and conflicts;
- `validation_status: PENDING`.

Repeating an identical request against unchanged source bytes returns the same snapshot ID.

## Recipe semantic metadata

Existing recipes remain valid for cache refresh. Research-ready recipes should add a `semantics` section.

Example for raw A-share daily market data:

```yaml
name: stock_daily_raw
category: market
source: tushare
file: market/stock_daily_raw.csv
update_freq: daily
fetch:
  func: daily
  date_col: trade_date
semantics:
  frequency: daily
  timezone: Asia/Shanghai
  unit: CNY/share
  currency: CNY
  adjustment: raw
  revision_policy: original_provider_response
  available_at_rule: available after market close on trade_date
  license_or_usage_note: internal research use under provider terms
```

Example for a fundamental dataset:

```yaml
semantics:
  frequency: quarterly
  timezone: Asia/Shanghai
  currency: CNY
  scale: provider_field_specific
  accounting_scope: consolidated
  period_basis: cumulative_ytd
  revision_policy: latest_restated
  published_at_col: ann_date
  available_at_col: ann_date
  license_or_usage_note: internal research use under provider terms
```

`update_freq` is refresh scheduling metadata. It is not automatically treated as the economic frequency of the dataset.

## Research readiness

`GET /research/catalog` reports `research_ready` and explicit limitations per dataset.

A snapshot is returned as:

- `OK` when declared semantics are complete and no conflict is detected;
- `PARTIAL` when data is preserved but material semantic metadata is missing;
- `CONFLICTED` when requested semantics do not match declared semantics or source availability exceeds `as_of`;
- `ERROR` when the request or source artifact is invalid.

`PARTIAL` and `CONFLICTED` datasets must not be silently used in material conclusions.

## Entity resolution

`POST /research/entities/resolve` does not guess when multiple names match and supports an `expected_name` cross-check.

The current stock reference is built from active listings (`list_status=L`). Therefore current entity resolution intentionally returns `PARTIAL`: delisted securities, historical symbol changes, mergers, and historical effective-date mapping are not yet complete.

## Snapshot storage

Default layout:

```text
store/_snapshots/
├── raw/<sha256>.<suffix>
├── normalized/<sha256>.csv
└── metadata/<snapshot_id>.json
```

Override it with:

```bash
export FMDATA_SNAPSHOT_DIR=/immutable/fmdata-snapshots
```

Use an append-only or read-only protected filesystem in production. Do not let research agents write directly to the snapshot directory.

## Deliberate limitations

This first contract does not claim that all existing fmdata recipes are research-ready. In particular, many recipes still lack:

- point-in-time publication/availability rules;
- original versus revised macro vintages;
- historical security and index membership;
- price adjustment and total-return definitions;
- field-level units and scale;
- cumulative versus single-period fundamental definitions;
- provider licensing and redistribution terms.

The snapshot API makes these gaps visible instead of filling them with assumptions.
