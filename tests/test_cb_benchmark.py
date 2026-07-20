from pathlib import Path
import tempfile

import pandas as pd

from fmdata.cb_benchmark import OFFICIAL_SOURCE, build_market_daily, load_official_cb_index


def test_load_official_cb_index_normalizes_akshare_columns():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        pd.DataFrame({"日期": ["2026-07-17", "2026-07-18"], "涨跌幅": [0.62, -0.15]}).to_csv(path, index=False)
        result = load_official_cb_index(path)
        assert result.columns.tolist() == ["date", "benchmark_return", "benchmark_source"]
        assert result["benchmark_return"].tolist() == [0.62, -0.15]
        assert set(result["benchmark_source"]) == {OFFICIAL_SOURCE}


def test_build_market_daily_never_uses_equal_weight_cb_raw():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        pd.DataFrame({"date": ["2026-07-17"], "benchmark_return": [0.25]}).to_csv(path, index=False)
        fake_cb = pd.DataFrame({"date": ["2026-07-17"], "close": [100], "pre_close": [50]})
        result = build_market_daily(fake_cb, path)
        assert result.loc[0, "benchmark_return"] == 0.25
        assert result.loc[0, "benchmark_source"] == OFFICIAL_SOURCE
