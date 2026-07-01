#!/usr/bin/env python3
"""Fetch data from cjpy (长江金工/天软 TS-OPI) into fmdata store.

General-purpose, parameterized fetcher for the fmdata `source: agent` mechanism.
Token is persisted via cjpy.set_token() to ~/.cjpy/config.json (one-time).

Usage:
  python3 fetch_cjpy.py --dataset market_daily --code SZ000001 [--start YYYYMMDD] [--end YYYYMMDD]
  python3 fetch_cjpy.py --dataset table --table "合并利润表" --codes SZ000001,SZ000002
  python3 fetch_cjpy.py --dataset factor --codes SZ000001,SZ000002 --dates 20260331 --factors 收盘价,PETTM

Incremental logic:
  - market_daily: if --start omitted, read existing CSV, start = last_date + 1 day.
  - table / factor: cjpy returns full history; we merge by key and dedup keep=last.

Output goes to recipe's `file` path (passed via --out, defaults below).
"""
import argparse
import os
import sys
import io
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

STORE = "/home/ubuntu/fmdata/store"
DEFAULT_OUT = {
    "market_daily": f"{STORE}/cjpy/market_daily.csv",
    "table": f"{STORE}/cjpy/table.csv",
    "factor": f"{STORE}/cjpy/factor.csv",
}


def parse_list(s):
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


def last_date_of_csv(path, date_col="时间"):
    """Return max date in existing CSV, as YYYYMMDD string, or None."""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or date_col not in df.columns:
            return None
        last = pd.to_datetime(df[date_col]).max()
        return (last + pd.Timedelta(days=1)).strftime("%Y%m%d")
    except Exception as e:
        print(f"WARN: could not read existing {path} for incremental: {e}", file=sys.stderr)
        return None


def fetch_market_daily(code, start, end, out_path):
    import cjpy

    # Incremental: if start not given, resume from last date + 1
    if not start:
        start = last_date_of_csv(out_path, "时间")
        if start:
            print(f"  incremental: resume from {start}")
        else:
            # No existing data: default start = 60 trading days back
            from datetime import datetime, timedelta
            start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            print(f"  no existing data, full fetch from {start}")

    # Guard: if incremental start already beyond end, data is up-to-date
    if int(start) > int(end):
        print(f"  already up-to-date (start {start} > end {end}), nothing to fetch")
        return 0

    df = cjpy.get_market_data(code=code, start=start, end=end, cycle="day")
    if df is None or df.empty:
        print(f"  no data returned for {code} {start}~{end}")
        # still return ok so recipe doesn't error on no-new-data
        return 0

    df["拉取时间"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    # Merge incremental
    if os.path.exists(out_path):
        old = pd.read_csv(out_path)
        df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["代码", "时间"], keep="last")
        df = df.sort_values(["代码", "时间"])

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"  saved {len(df)} rows to {out_path}")
    print(f"  date range: {df['时间'].min()} -> {df['时间'].max()}")
    return len(df)


def fetch_table(codes, table_name, out_path, fields=None):
    import cjpy

    df = cjpy.get_table_data(code=codes, table_name=table_name, fields=fields or "*")
    if df is None or df.empty:
        print(f"  no data for table {table_name}")
        return 0

    # Tag with table name so multiple tables can coexist in one CSV if needed
    df["_表名"] = table_name
    df["拉取时间"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    # Merge & dedup: tables return full history each pull; keep latest pull per key
    # Use a broad dedup key (code + all date-like cols). Fallback: overwrite.
    key_cols = [c for c in ["代码", "StockID", "截止日", "公布日", "数据报告期"] if c in df.columns]
    if os.path.exists(out_path):
        old = pd.read_csv(out_path)
        df = pd.concat([old, df], ignore_index=True)
        if key_cols:
            df = df.drop_duplicates(subset=key_cols + ["_表名"], keep="last")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"  saved {len(df)} rows to {out_path} (table={table_name}, key_cols={key_cols})")
    return len(df)


def fetch_factor(codes, dates, factors, out_path):
    import cjpy

    df = cjpy.get_factor_data(code=codes, date=dates, factors=factors)
    if df is None or df.empty:
        print(f"  no factor data for given inputs")
        return 0

    df["拉取时间"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    # Merge & dedup by code+date (factor panel)
    key_cols = [c for c in ["代码", "日期", "StockID", "date"] if c in df.columns]
    if os.path.exists(out_path):
        old = pd.read_csv(out_path)
        df = pd.concat([old, df], ignore_index=True)
        if key_cols:
            df = df.drop_duplicates(subset=key_cols, keep="last")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"  saved {len(df)} rows to {out_path} (key_cols={key_cols})")
    return len(df)


def main():
    ap = argparse.ArgumentParser(description="cjpy fetcher for fmdata")
    ap.add_argument("--dataset", required=True,
                    choices=["market_daily", "table", "factor"],
                    help="dataset type")
    ap.add_argument("--code", help="single code (market_daily)")
    ap.add_argument("--codes", help="comma-separated codes (table/factor)")
    ap.add_argument("--table", help="table name (table dataset)")
    ap.add_argument("--dates", help="comma-separated dates (factor)")
    ap.add_argument("--factors", help="comma-separated factor names (factor)")
    ap.add_argument("--start", help="start date YYYYMMDD (market_daily; optional=incremental)")
    ap.add_argument("--end", help="end date YYYYMMDD (market_daily; default today)")
    ap.add_argument("--out", help="output CSV path (default per dataset)")
    args = ap.parse_args()

    out_path = args.out or DEFAULT_OUT[args.dataset]

    if args.dataset == "market_daily":
        if not args.code:
            print("ERROR: --code required for market_daily", file=sys.stderr)
            sys.exit(1)
        from datetime import datetime
        end = args.end or datetime.now().strftime("%Y%m%d")
        n = fetch_market_daily(args.code, args.start, end, out_path)
    elif args.dataset == "table":
        if not args.table or not args.codes:
            print("ERROR: --table and --codes required", file=sys.stderr)
            sys.exit(1)
        n = fetch_table(parse_list(args.codes), args.table, out_path)
    elif args.dataset == "factor":
        if not args.codes or not args.dates or not args.factors:
            print("ERROR: --codes --dates --factors required", file=sys.stderr)
            sys.exit(1)
        n = fetch_factor(parse_list(args.codes), parse_list(args.dates), parse_list(args.factors), out_path)

    print(f"DONE rows={n}")
    sys.exit(0)


if __name__ == "__main__":
    main()
