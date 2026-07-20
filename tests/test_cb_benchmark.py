from pathlib import Path
import tempfile

import pandas as pd
import pytest

from fmdata.cb_benchmark import OFFICIAL_SOURCE, build_market_daily, load_official_cb_index


def _history() -> pd.DataFrame:
    dates = pd.date_range("2026-06-01", periods=25, freq="B")
    return pd.DataFrame({"日期": dates.strftime("%Y-%m-%d"), "涨跌幅": [0.10] * 25, "代码": ["000832"] * 25})


def test_load_official_cb_index_normalizes_akshare_columns():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        frame = _history()
        frame.loc[0, "涨跌幅"] = 0.62
        frame.loc[1, "涨跌幅"] = -0.15
        frame.to_csv(path, index=False)
        result = load_official_cb_index(path)
        assert result.columns.tolist() == ["date", "benchmark_return", "benchmark_source"]
        assert result.loc[0, "benchmark_return"] == 0.62
        assert result.loc[1, "benchmark_return"] == -0.15
        assert set(result["benchmark_source"]) == {OFFICIAL_SOURCE}


def test_build_market_daily_never_uses_equal_weight_cb_raw():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        _history().rename(columns={"日期": "date", "涨跌幅": "benchmark_return"}).to_csv(path, index=False)
        fake_cb = pd.DataFrame({"date": ["2026-07-17"], "close": [100], "pre_close": [50]})
        result = build_market_daily(fake_cb, path)
        assert result.loc[0, "benchmark_return"] == 0.10
        assert result.loc[0, "benchmark_source"] == OFFICIAL_SOURCE


def test_rejects_wrong_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        frame = _history()
        frame["代码"] = "000300"
        frame.to_csv(path, index=False)
        with pytest.raises(ValueError, match="unexpected symbols"):
            load_official_cb_index(path)


def test_rejects_implausible_percent_return():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        frame = _history()
        frame.loc[0, "涨跌幅"] = 62.0
        frame.to_csv(path, index=False)
        with pytest.raises(ValueError, match="exceeds 30%"):
            load_official_cb_index(path)
