#!/usr/bin/env python3
"""Refresh the frozen Core-V3 fundamental-change candidate history.

Only the pre-registered descriptors are fetched. Values are stored by the exact provider
``trade_date``. The cache is append-safe and never substitutes a later observation for a
missing historical date.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd
import tushare as ts

FACTORS = ("gross_margin_qoq", "np_ttm_qoq", "eaa")
COLUMNS = ("factor_name", "trade_date", "ts_code", "factor_value")


def _strict_date(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    return pd.to_datetime(text, format="%Y%m%d", errors="coerce")


def _month_end_open_dates(calendar_path: Path, start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    calendar = pd.read_csv(calendar_path, dtype={"cal_date": str})
    required = {"cal_date", "is_open"}
    missing = required.difference(calendar.columns)
    if missing:
        raise SystemExit(f"trade calendar missing columns: {sorted(missing)}")
    calendar["date"] = _strict_date(calendar["cal_date"])
    calendar["is_open"] = pd.to_numeric(calendar["is_open"], errors="coerce").fillna(0).astype(int)
    sample = calendar[
        (calendar["is_open"] == 1)
        & (calendar["date"] >= start)
        & (calendar["date"] <= end)
    ].dropna(subset=["date"])
    if sample.empty:
        raise SystemExit("trade calendar has no open dates in requested range")
    month_end = sample.groupby(sample["date"].dt.to_period("M"))["date"].max().sort_values()
    return [pd.Timestamp(value).strftime("%Y%m%d") for value in month_end]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20220101")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y%m%d"))
    parser.add_argument(
        "--calendar",
        default=str(Path.home() / "fmdata/store/reference/trade_calendar.csv"),
    )
    parser.add_argument(
        "--output",
        default=str(Path.home() / "fmdata/store/factors/core_v3_fundamental_change_monthly.csv"),
    )
    parser.add_argument("--pause", type=float, default=0.08)
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required")
    start = pd.to_datetime(args.start, format="%Y%m%d")
    end = pd.to_datetime(args.end, format="%Y%m%d")
    if start > end:
        raise SystemExit("start must be <= end")

    dates = _month_end_open_dates(Path(args.calendar).expanduser(), start, end)
    pro = ts.pro_api(token)
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        pd.read_csv(output, dtype={"factor_name": str, "trade_date": str, "ts_code": str})
        if output.exists()
        else pd.DataFrame(columns=COLUMNS)
    )
    if not existing.empty:
        existing["trade_date"] = (
            existing["trade_date"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
        )
    existing_keys = set(
        zip(
            existing.get("factor_name", pd.Series(dtype=str)).astype(str),
            existing.get("trade_date", pd.Series(dtype=str)).astype(str),
        )
    )

    fetched: list[pd.DataFrame] = []
    for factor in FACTORS:
        for trade_date in dates:
            if (factor, trade_date) in existing_keys:
                continue
            values = pro.factor_value(factor_name=factor, trade_date=trade_date)
            if values is None or values.empty:
                raise SystemExit(
                    f"factor_value returned no rows: factor={factor} trade_date={trade_date}"
                )
            required = {"ts_code", "trade_date", "factor_value"}
            missing = required.difference(values.columns)
            if missing:
                raise SystemExit(
                    f"factor_value missing columns {sorted(missing)} for {factor} {trade_date}"
                )
            frame = values.loc[:, ["ts_code", "trade_date", "factor_value"]].copy()
            frame["ts_code"] = frame["ts_code"].astype(str)
            frame["trade_date"] = (
                frame["trade_date"].astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
            )
            if not (frame["trade_date"] == trade_date).all():
                raise SystemExit(
                    f"factor_value date mismatch: factor={factor} requested={trade_date} "
                    f"returned={sorted(frame['trade_date'].dropna().unique().tolist())[:5]}"
                )
            frame["factor_value"] = pd.to_numeric(frame["factor_value"], errors="coerce")
            frame = frame.dropna(subset=["factor_value"])
            frame.insert(0, "factor_name", factor)
            frame = frame.drop_duplicates(["factor_name", "trade_date", "ts_code"], keep="last")
            if len(frame) < 1000:
                raise SystemExit(
                    f"implausibly sparse factor_value result: factor={factor} "
                    f"trade_date={trade_date} rows={len(frame)}"
                )
            fetched.append(frame.loc[:, COLUMNS])
            print(f"factor_value {factor} {trade_date}: {len(frame)} rows")
            time.sleep(args.pause)

    if fetched:
        new = pd.concat(fetched, ignore_index=True)
        combined = pd.concat([existing, new], ignore_index=True, sort=False)
    else:
        combined = existing.copy()
    if combined.empty:
        raise SystemExit("fundamental-change cache is empty")
    combined = combined.drop_duplicates(
        ["factor_name", "trade_date", "ts_code"], keep="last"
    ).sort_values(["trade_date", "factor_name", "ts_code"])

    temporary = output.with_suffix(output.suffix + ".tmp")
    combined.to_csv(temporary, index=False)
    temporary.replace(output)
    print(
        {
            "rows": int(len(combined)),
            "factors": sorted(combined["factor_name"].unique().tolist()),
            "first_trade_date": str(combined["trade_date"].min()),
            "last_trade_date": str(combined["trade_date"].max()),
            "output": str(output),
        }
    )


if __name__ == "__main__":
    main()
