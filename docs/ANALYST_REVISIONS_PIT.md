# Analyst revisions PIT research dataset

This branch adds a dedicated historical sell-side forecast backfill for quantitative equity research.

## Source

Tushare Pro `report_rc` (sell-side earnings forecasts), available from 2010 onward. The API exposes the research-report date, broker, analyst, forecast quarter, revenue/profit/EPS/PE/ROE forecasts, rating and target-price fields. The official endpoint is capped at 3,000 rows per response, so `store/scripts/fetch_report_rc_history.py` paginates every date window.

## Point-in-time rule

`report_date` is the information date. A strategy evaluated at `as_of_date` may use only rows where:

```text
report_date <= as_of_date
```

Do **not** backfill a current consensus snapshot into history. Historical consensus must be reconstructed from the reports that were actually observable at the time.

## Intended alpha features

For each stock and forecast fiscal period, construct broker-level forecasts first and aggregate only after de-duplicating repeated reports. Candidate signals include:

- EPS / net-profit revision over 20/60 trading days;
- revision breadth: `(n_up - n_down) / n_active_analysts`;
- revision acceleration: recent revision minus prior-window revision;
- rating change and positive/negative rating breadth;
- forecast dispersion and change in dispersion;
- analyst coverage change;
- residualized revision after controlling for industry, size and price momentum;
- revision x momentum / revision x quality interactions.

The index-enhancement project should admit these signals only after walk-forward IC, decay, turnover, transaction-cost and portfolio-level incremental-alpha tests. Current snapshot files such as `analyst_consensus.csv` are not valid substitutes for a PIT backtest.

## Backfill

```bash
export TUSHARE_TOKEN=...
python3 store/scripts/fetch_report_rc_history.py --start 20100101 --end 20260825
```

Default output:

```text
~/fmdata/store/fundamentals/report_rc_history.csv
```
