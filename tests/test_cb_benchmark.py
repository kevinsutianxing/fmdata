from pathlib import Path
import tempfile

import pandas as pd
import pytest

from fmdata.cb_benchmark import OFFICIAL_SOURCE, build_market_daily, load_official_cb_index


def _history(raw_percent: bool = True) -> pd.DataFrame:
    dates = pd.date_range("2026-06-01", periods=25, freq="B")
    value = 0.10 if raw_percent else 0.001
    return pd.DataFrame({
        "日期" if raw_percent else "date": dates.strftime("%Y-%m-%d"),
        "涨跌幅" if raw_percent else "benchmark_return": [value] * 25,
        "代码": ["000832"] * 25,
    })


def test_load_official_cb_index_normalizes_akshare_columns_to_fraction():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        frame = _history(True)
        frame.loc[0, "涨跌幅"] = 0.62
        frame.loc[1, "涨跌幅"] = -0.15
        frame.to_csv(path, index=False)
        result = load_official_cb_index(path)
        assert result.columns.tolist() == ["date", "benchmark_return", "benchmark_source", "benchmark_unit"]
        assert result.loc[0, "benchmark_return"] == 0.0062
        assert result.loc[1, "benchmark_return"] == -0.0015
        assert set(result["benchmark_source"]) == {OFFICIAL_SOURCE}
        assert set(result["benchmark_unit"]) == {"fraction"}


def test_already_normalized_benchmark_return_is_not_divided_twice():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        _history(False).to_csv(path, index=False)
        fake_cb = pd.DataFrame({"date": ["2026-07-17"], "close": [100], "pre_close": [50]})
        result = build_market_daily(fake_cb, path)
        assert result.loc[0, "benchmark_return"] == 0.001
        assert result.loc[0, "benchmark_source"] == OFFICIAL_SOURCE


def test_fraction_quality_gate_rejects_wrong_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        frame = _history(True)
        frame["代码"] = "000300"
        frame.to_csv(path, index=False)
        with pytest.raises(ValueError, match="unexpected symbols"):
            load_official_cb_index(path)


def test_fraction_quality_gate_rejects_implausible_move():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "idx.csv"
        frame = _history(False)
        frame.loc[0, "benchmark_return"] = 0.62
        frame.to_csv(path, index=False)
        with pytest.raises(ValueError, match="exceeds 30%"):
            load_official_cb_index(path)
