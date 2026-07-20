# Official CSI Convertible-Bond Benchmark Integration

`benchmark_return` for convertible-bond strategy evaluation must be the official CSI Convertible Bond Index (000832), never an equal-weight return calculated from the local CB universe.

## Refresh

```bash
fmdata fetch csi_cb_idx_hist
```

The recipe uses `akshare.index_zh_a_hist(symbol="000832")`. Because this is an Eastmoney-backed source, fmdata's recipe fetcher applies the configured QG proxy pool.

## Panel builder integration

Local panel builders should replace any equal-weight `build_market_daily(cb_raw)` implementation with:

```python
from fmdata.cb_benchmark import build_market_daily

market_daily = build_market_daily(
    cb_raw,
    official_index_file=args.official_index_file,
)
```

The optional file parameter permits a reproducible frozen input such as `data/official_index.csv`; omitting it reads `store/market/csi_cb_idx_hist.csv` through `FMDATA_DIR`.

Output columns:

- `date`
- `benchmark_return`: percentage points in the current contract (`0.62` means `+0.62%`)
- `benchmark_source`: always `csi_000832`

The legacy `cb_raw` argument is ignored deliberately. This prevents an accidental return to an equal-weight universe benchmark.
