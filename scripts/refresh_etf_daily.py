#!/usr/bin/env python3
"""Refresh all industry ETF daily data from tushare.

Covers 37 ETFs used in sector rotation strategy.
Outputs:
  - market/etf_daily.csv (long format: code, trade_date, OHLCV, pct_chg, etc.)
  - market/etf_data/{code}.csv (individual ETF files for strategy reuse)
"""
import tushare as ts
import pandas as pd
from pathlib import Path
import time

STORE = Path("/home/ubuntu/fmdata/store/market")
OUTPUT = STORE / "etf_daily.csv"
INDIVIDUAL_DIR = STORE / "etf_data"

# 37 ETFs for sector rotation (31 mapped + 6 extra)
ETF_CODES = [
    "512800",  # 银行ETF
    "512880",  # 证券ETF
    "512200",  # 房地产ETF
    "516950",  # 基建ETF
    "159745",  # 建材ETF
    "159996",  # 家电ETF
    "512690",  # 酒ETF
    "159871",  # 纺织ETF
    "562350",  # 轻工ETF
    "516150",  # 消费ETF
    "159766",  # 旅游ETF
    "512010",  # 医药ETF
    "159825",  # 农业ETF
    "515030",  # 新能源车ETF
    "515880",  # 通信ETF
    "512720",  # 计算机ETF
    "159997",  # 电子ETF
    "512980",  # 传媒ETF
    "159755",  # 电池ETF
    "516320",  # 高端装备ETF
    "159870",  # 化工ETF
    "512400",  # 有色金属ETF
    "515210",  # 钢铁ETF
    "515220",  # 煤炭ETF
    "159981",  # 能源化工ETF
    "159611",  # 电力ETF
    "159843",  # 仓储物流ETF
    "512660",  # 军工ETF
    "159928",  # 消费ETF汇添富
    "512580",  # 环保ETF
    "159869",  # 游戏ETF
    # Extra ETFs in existing data
    "516110",  # 汽车ETF
    "159619",  # 半导体ETF
    "159301",  # 新能源ETF
    "515970",  # 医药创新ETF
    "560280",  # 中证1000ETF
    "159662",  # 芯片ETF
]


def code_to_ts(code: str) -> str:
    """Convert 6-digit code to tushare ts_code format."""
    if code.startswith(("51", "56")):
        return f"{code}.SH"
    elif code.startswith("15"):
        return f"{code}.SZ"
    return f"{code}.SZ"


def main():
    pro = ts.pro_api()

    # Determine incremental start date
    start_date = "20200101"
    if OUTPUT.exists():
        existing = pd.read_csv(OUTPUT)
        if not existing.empty and "trade_date" in existing.columns:
            last = existing["trade_date"].max()
            # Incremental: fetch from last date
            start_date = last.replace("-", "")
            print(f"incremental from {last}")

    all_data = []
    for code in ETF_CODES:
        ts_code = code_to_ts(code)
        try:
            df = pro.fund_daily(ts_code=ts_code, start_date=start_date)
            if df is not None and not df.empty:
                df["code"] = code
                all_data.append(df)
                print(f"  {ts_code}: {len(df)} rows")
            else:
                print(f"  {ts_code}: no data")
        except Exception as e:
            print(f"  {ts_code}: error - {e}")
        time.sleep(0.3)  # tushare rate limit

    if not all_data:
        print("no data fetched")
        return

    new_df = pd.concat(all_data, ignore_index=True)
    # Standardize columns to match existing format
    new_df = new_df.rename(columns={"vol": "vol", "amount": "amount"})
    new_df["trade_date"] = pd.to_datetime(new_df["trade_date"]).dt.strftime("%Y-%m-%d")

    # Select/rename columns to match existing schema
    cols = ["code", "trade_date", "open", "close", "high", "low", "vol", "amount", "pct_chg", "change"]
    new_df = new_df[[c for c in cols if c in new_df.columns]]

    # Merge with existing if incremental
    if OUTPUT.exists() and start_date > "20200101":
        existing = pd.read_csv(OUTPUT)
        # Remove overlapping dates
        if "trade_date" in existing.columns:
            cutoff = new_df["trade_date"].min()
            existing = existing[existing["trade_date"] < cutoff]
        new_df = pd.concat([existing, new_df], ignore_index=True)

    # Normalize code to str: existing CSV reads code as int64 while the tushare
    # fetch sets str, so concat yields a mixed int/str object column. Without
    # this, .unique() returns each code twice (int + str) and the per-code write
    # loop lets the 1-row str slice overwrite the full-history int slice.
    new_df["code"] = new_df["code"].astype(str)

    # Add amplitude and turnover if missing
    if "amplitude" not in new_df.columns and "high" in new_df.columns and "low" in new_df.columns:
        new_df["amplitude"] = ((new_df["high"].astype(float) - new_df["low"].astype(float)) / new_df["close"].astype(float) * 100).round(2)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    new_df.to_csv(OUTPUT, index=False)
    print(f"saved {len(new_df)} rows ({new_df['code'].nunique()} ETFs) to {OUTPUT}")

    # Also save individual ETF CSVs for strategy reuse
    INDIVIDUAL_DIR.mkdir(parents=True, exist_ok=True)
    for code in new_df["code"].unique():
        etf_df = new_df[new_df["code"] == code].copy()
        etf_df.to_csv(INDIVIDUAL_DIR / f"{code}.csv", index=False)
    print(f"saved {new_df['code'].nunique()} individual ETF files to {INDIVIDUAL_DIR}/")


if __name__ == "__main__":
    main()
