# Analyst revision alpha feature specification

The historical `report_rc` detail is a raw event table, not a ready-made consensus panel. The strategy layer should reconstruct a point-in-time panel at each rebalance date.

For stock `i`, broker `b`, forecast period `q` and as-of date `t`:

1. keep only reports with `report_date <= t`;
2. within `(i,b,q)`, keep the latest report known by `t`;
3. aggregate broker forecasts with a robust median (primary) and trimmed mean (diagnostic);
4. compare the current consensus with the consensus reconstructed at lagged as-of dates;
5. preserve broker identity so breadth/consistency are computed from matched broker-level revisions, not inferred from the consensus change.

## Primary candidates

```text
revision_20d = (cons_eps_t - cons_eps_t-20d) / max(abs(cons_eps_t-20d), floor)
revision_60d = (cons_eps_t - cons_eps_t-60d) / max(abs(cons_eps_t-60d), floor)
revision_breadth_20d = (matched_brokers_up - matched_brokers_down) / matched_brokers
revision_acceleration = revision_20d - revision_20d_lag20d
coverage_level = distinct_active_brokers
coverage_change_60d = active_brokers_t - active_brokers_t-60d
dispersion = robust_std(broker_eps) / max(abs(cons_eps), floor)
dispersion_change = dispersion_t - dispersion_t-20d
```

Use separate current-year / next-year forecasts and a horizon-weighted blend rather than mixing forecast periods mechanically. Cross-sectional preprocessing should winsorize, standardize and neutralize size; industry-neutral and controlled-cross-industry versions should both be tested.

## Higher-priority refinements once the raw history is backfilled

### 1. Revision consistency rather than revision magnitude alone

A large consensus move produced by one or two brokers is not equivalent to a broad, repeated revision. Construct:

```text
revision_consistency = sign(consensus_revision) * revision_breadth
same_sign_streak = fraction of active brokers whose last two revisions have the current consensus sign
revision_reversal_fraction = fraction of brokers whose latest revision reverses their prior revision
```

The research hypothesis is deliberately one-sided: **broad, persistent revisions should receive more confidence; large but internally reversing revisions should receive less confidence.** Do not flip a revision signal merely because consistency is low.

### 2. Abnormal analyst coverage

Recent China evidence suggests analyst attention itself may contain information beyond forecast levels. `report_rc` preserves `org_name`, `author_name` and report dates, so test both distinct-broker and distinct-author attention:

```text
coverage_60d = distinct analysts/brokers publishing in trailing 60d
coverage_surprise = coverage_60d - stock_own_trailing_12m_expected_coverage
abnormal_coverage = residual(coverage_60d ~ size + liquidity + industry + prior_coverage)
coverage_acceleration = coverage_change_60d - lagged_coverage_change_60d
```

Raw coverage is strongly related to size/liquidity, so **never admit un-neutralised coverage counts as alpha**. The preferred signal is unexpected/abnormal coverage after these structural controls.

### 3. Forecast innovation / anchoring diagnostics

`report_rc` also contains broker/author identity. Where sample depth allows, estimate whether a broker update is genuinely informative rather than a small move toward an already-obvious consensus:

```text
broker_innovation = broker_revision - consensus_revision_excluding_broker
innovation_breadth = fraction of brokers with same-sign, above-threshold innovation
```

A possible later reliability layer can use ex-ante information only: trailing historical forecast accuracy, revision consistency, and update timeliness. Do **not** use future realised earnings to weight the current forecast.

### 4. Attention and forecast information are separate sleeves

Do not mechanically merge `abnormal_coverage` and `EPS_revision` into one descriptor. Test:

```text
Revision = robust blend(revision_20d, revision_60d, breadth, acceleration)
Attention = abnormal_coverage / coverage_surprise
Disagreement = -dispersion_change conditional on revision direction
```

Then measure their mutual correlation and incremental portfolio alpha. Coverage may be useful precisely because Chinese analyst forecasts can be optimistic or sticky; it should be allowed to earn admission as an independent information-discovery signal.

## Point-in-time and bias rules

- `report_date` is the information date. `create_time` is metadata/diagnostic only unless verified to be the true public-availability timestamp.
- One broker (and, in robustness work, one author) must not receive multiple votes merely because it publishes repeated reports.
- The same fiscal-year forecast must be compared across as-of dates; December/January horizon rollover must not create a fake revision.
- Minimum matched-broker coverage is required for breadth/consistency; missing coverage is missing information, not a bearish signal.
- Broker-quality/reliability estimates must use only forecasts and realised results already known before `t`.
- Star/senior-analyst labels are not assumed available or stable in `report_rc`; no such weighting is allowed unless a separate PIT-clean source is added.

## Admission rule

Admission requires incremental portfolio alpha after costs versus the current stable baseline, not merely positive standalone IC. The research comparison must report development (2023-2024), pre-2026 robustness, and diagnostic 2025-latest alpha, IR, active drawdown, turnover, coverage and correlation to the existing core alpha.

Priority order once the historical data are available:

1. EPS revision + matched-broker breadth;
2. abnormal coverage / coverage surprise as a separate sleeve;
3. revision acceleration and consistency confidence;
4. dispersion-change conditional on revision direction;
5. only then broker/author reliability and innovation weighting.

This ordering keeps the first production test low-dimensional and auditable while preserving the richer `report_rc` event history for later research.
