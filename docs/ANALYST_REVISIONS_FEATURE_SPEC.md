# Analyst revision alpha feature specification

The historical `report_rc` detail is a raw event table, not a ready-made consensus panel. The strategy layer should reconstruct a point-in-time panel at each rebalance date.

For stock `i`, broker `b`, forecast period `q` and as-of date `t`:

1. keep only reports with `report_date <= t`;
2. within `(i,b,q)`, keep the latest report known by `t`;
3. aggregate broker forecasts with a robust median (primary) and trimmed mean (diagnostic);
4. compare the current consensus with the consensus reconstructed at lagged as-of dates.

Primary candidates:

```text
revision_20d = (cons_eps_t - cons_eps_t-20d) / max(abs(cons_eps_t-20d), floor)
revision_60d = (cons_eps_t - cons_eps_t-60d) / max(abs(cons_eps_t-60d), floor)
revision_breadth_20d = (brokers_up - brokers_down) / active_brokers
revision_acceleration = revision_20d - revision_20d_lag20d
coverage_change_60d = active_brokers_t - active_brokers_t-60d
dispersion = robust_std(broker_eps) / max(abs(cons_eps), floor)
dispersion_change = dispersion_t - dispersion_t-20d
```

Use separate current-year / next-year forecasts and a horizon-weighted blend rather than mixing forecast periods mechanically. Cross-sectional preprocessing should winsorize, standardize and neutralize size; industry-neutral and controlled-cross-industry versions should both be tested.

Admission requires incremental portfolio alpha after costs versus the current stable baseline, not merely positive standalone IC. The research comparison must report development (2023-2024) and diagnostic OOS (2025-latest) alpha, IR, active drawdown, turnover, coverage and correlation to the existing core alpha.
