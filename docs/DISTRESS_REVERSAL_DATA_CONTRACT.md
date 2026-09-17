# Distress Reversal fmdata contract

This repository is the reusable data layer for `kevinsutianxing/distress-reversal-research`. It must remain strategy-independent: recipes expose standardized facts and provenance, while the strategy repository owns frozen signals, portfolio rules, and experiment history.

## Required logical families

| dataset_id | Current status | Locator/action |
|---|---|---|
| daily_price | PARTIAL through 2026-09-15 | Existing local research parquet layer; publish schema/PIT contract before extraction |
| daily_basic | PARTIAL through 2026-08-21 | Existing market-cap recipe/local layer; extend lagged refresh only |
| stock_status | PARTIAL through 2026-08-21 | Existing local layer; document suspension/ST/limit fields and as-of rules |
| index_membership_weights | PARTIAL | CSI300/500 local through 2026-07-31; CSI1000 remote snapshot through 2026-08-31 |
| sw_industry_pit | PARTIAL | SW1 effective intervals through 2026-08-14; SW2 UNAVAILABLE |
| fundamentals_pit | PARTIAL | `ann_date` through 2026-08-24; preserve `update_flag`; no same-day assumption |
| analyst_report_raw | UNAVAILABLE | Broker-level `report_date`/org/EPS records required; consensus aggregates are not substitutes |
| corporate_actions | UNAVAILABLE | Define announcement/effective date semantics before use |
| wind_index_daily | PARTIAL | 885001.WI / 881001.WI remain local licensed payloads; publish metadata/provenance only |
| macro_pit | PARTIAL | Current revised histories are not release-vintage PIT without publication timestamps |

## Non-negotiable semantics

1. Every extraction returns immutable source/version hashes and row/date coverage.
2. Large vendor payloads remain in local fmdata storage; consumers receive bounded extraction results.
3. PIT joins happen at the provider or explicitly in the caller using the declared timestamp field.
4. Missing required fields are surfaced as missing; they are never inferred by backfill or nearby substitution.
5. `analyst_report_raw` is broker-level data. Aggregate consensus cannot silently replace it.

The compact status snapshot at 2026-09-15 is mirrored under `docs/distress_reversal_snapshot_20260915.json` in this branch. The strategy repository's `data/manifest.json` is the consumer-facing logical registry.

## 2026-09-17 validation note

This documentation-only branch passed Markdown contract assertions and `python -m json.tool`. The repository's existing pytest suite could not be collected in this HK43 checkout because its `tushare` runtime dependency is intentionally absent; no executable fmdata code was changed.
