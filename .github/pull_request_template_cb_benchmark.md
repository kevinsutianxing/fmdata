## Acceptance

```bash
pytest -q tests/test_cb_benchmark.py
fmdata fetch csi_cb_idx_hist
```

Expected: output `store/market/csi_cb_idx_hist.csv` contains official 000832 daily history; `load_official_cb_index()` returns `benchmark_source=csi_000832` and percentage returns.
