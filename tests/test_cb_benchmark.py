from pathlib import Path
import tempfile

import pandas as pd

from fmdata.cb_benchmark import OFFICIAL_SOURCE, build_market_daily, load_official_cb_index


def test_load_official_cb_index_normalizes_akshare_columns_to_fraction():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        pd.DataFrame({"日期": ["2026-07-17", "2026-07-18"], "涨跌幅": [0.62, -0.15]}).to_csv(path, index=False)
        result = load_official_cb_index(path)
        assert result.columns.tolist() == ["date", "benchmark_return", "benchmark_source", "benchmark_unit"]
        assert result["benchmark_return"].tolist() == [0.0062, -0.0015]
        assert set(result["benchmark_source"]) == {OFFICIAL_SOURCE}
        assert set(result["benchmark_unit"]) == {"fraction"}


def test_already_normalized_benchmark_return_is_not_divided_twice():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        pd.DataFrame({"date": ["2026-07-17"], "benchmark_return": [0.0025]}).to_csv(path, index=False)
        fake_cb = pd.DataFrame({"date": ["2026-07-17"], "close": [100], "pre_close": [50]})
        result = build_market_daily(fake_cb, path)
        assert result.loc[0, "benchmark_return"] == 0.0025
        assert result.loc[0, "benchmark_source"] == OFFICIAL_SOURCE
