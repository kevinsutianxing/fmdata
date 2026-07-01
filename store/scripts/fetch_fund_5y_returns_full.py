#!/usr/bin/env python3
"""Fetch full 5Y cumulative return curves for target fund universe.

Inputs are fmdata fund rank tables plus fund_basic_open. Outputs:
- market/fund_5y_nav_full/{code}.csv: raw cumulative return curve per fund
- market/fund_5y_return_full.csv: one-row summary per fund
- market/fund_5y_return_failures.csv: failed funds for reruns
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import akshare as ak
import pandas as pd


BASE = Path("/home/ubuntu/fmdata/store/market")
BASIC_PATH = BASE / "fund_basic_open.csv"
NAV_DIR = BASE / "fund_5y_nav_full"
SUMMARY_PATH = BASE / "fund_5y_return_full.csv"
FAIL_PATH = BASE / "fund_5y_return_failures.csv"
UNIVERSE_PATH = BASE / "fund_5y_universe.csv"
PROGRESS_PATH = BASE / "fund_5y_return_progress.json"

EXCLUDE_RE = re.compile("货币|现金|短债|中短债|超短债|同业存单|存款|FOF|养老|目标日期|目标风险", re.I)


def code6(value) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip()
    if "." in s:
        s = s.split(".")[0]
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def load_basic() -> pd.DataFrame:
    df = pd.read_csv(BASIC_PATH, dtype=str)
    df["code6"] = df["ts_code"].map(code6)
    return df


def load_rank(name: str) -> pd.DataFrame:
    df = pd.read_csv(BASE / f"{name}.csv", dtype={"基金代码": str})
    df["code6"] = df["基金代码"].map(code6)
    return df


def build_universe() -> pd.DataFrame:
    basic = load_basic()
    basic = basic[basic["status"].fillna("") == "L"].copy()
    basic["name_for_filter"] = basic["name"].fillna("") + " " + basic.get("invest_type", "").fillna("")

    frames = []
    spec = [
        ("纯债", "fund_rank_bond", "债券型"),
        ("股票", "fund_rank_equity", "股票型"),
        ("偏股混", "fund_rank_hybrid", "混合型"),
    ]
    keep_cols = ["code6", "ts_code", "name", "management", "fund_type", "invest_type", "type", "status", "found_date", "name_for_filter"]
    for category, rank_name, fund_type in spec:
        rank = load_rank(rank_name)[["code6", "基金简称", "日期", "近3年"]].copy()
        merged = rank.merge(basic[keep_cols], on="code6", how="left", suffixes=("_rank", ""))
        merged = merged[merged["fund_type"].fillna("") == fund_type].copy()
        merged = merged[~merged["name_for_filter"].fillna(merged["基金简称"].fillna("")).str.contains(EXCLUDE_RE, na=False)]
        merged["category"] = category
        frames.append(merged)
    uni = pd.concat(frames, ignore_index=True)
    uni = uni.drop_duplicates(["category", "code6"])
    NAV_DIR.mkdir(parents=True, exist_ok=True)
    uni.to_csv(UNIVERSE_PATH, index=False)
    return uni


def raw_path(code: str) -> Path:
    return NAV_DIR / f"{code}.csv"


def summarize_curve(code: str, category: str, df: pd.DataFrame, source: str) -> dict:
    date_col = "日期" if "日期" in df.columns else "净值日期"
    ret_col = "累计收益率" if "累计收益率" in df.columns else None
    if ret_col is None:
        raise ValueError(f"missing cumulative return column: {df.columns.tolist()}")
    x = df.copy()
    x[date_col] = pd.to_datetime(x[date_col], errors="coerce")
    x[ret_col] = pd.to_numeric(x[ret_col], errors="coerce")
    x = x.dropna(subset=[date_col, ret_col]).sort_values(date_col)
    if x.empty:
        raise ValueError("empty curve after parsing")
    first = x.iloc[0]
    last = x.iloc[-1]
    span_days = int((last[date_col] - first[date_col]).days)
    complete = span_days >= 365 * 5 - 10
    return {
        "category": category,
        "code6": code,
        "start_date": first[date_col].strftime("%Y-%m-%d"),
        "end_date": last[date_col].strftime("%Y-%m-%d"),
        "obs": len(x),
        "span_days": span_days,
        "five_year_return_pct": float(last[ret_col]),
        "five_year_complete": complete,
        "source": source,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_one(item: tuple[str, str], force: bool = False, sleep: float = 0.0) -> tuple[dict | None, dict | None]:
    category, code = item
    path = raw_path(code)
    try:
        if path.exists() and not force:
            df = pd.read_csv(path)
            return summarize_curve(code, category, df, "cache"), None
        if sleep:
            time.sleep(sleep)
        df = ak.fund_open_fund_info_em(symbol=code, indicator="累计收益率走势", period="5年")
        if df is None or df.empty:
            raise ValueError("akshare returned empty data")
        df.to_csv(path, index=False)
        return summarize_curve(code, category, df, "akshare"), None
    except Exception as exc:
        return None, {
            "category": category,
            "code6": code,
            "error": type(exc).__name__,
            "message": str(exc)[:500],
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }


def write_progress(done: int, total: int, ok: int, fail: int) -> None:
    PROGRESS_PATH.write_text(json.dumps({
        "done": done,
        "total": total,
        "ok": ok,
        "fail": fail,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    uni = build_universe()
    items = sorted(set((r.category, r.code6) for r in uni.itertuples() if r.code6))
    total = len(items)
    print(f"universe rows={len(uni)} unique category-code pairs={total}")

    results: list[dict] = []
    failures: list[dict] = []
    done = ok = fail = 0

    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch_one, item, args.force, args.sleep) for item in items]
        for fut in futures.as_completed(futs):
            res, err = fut.result()
            done += 1
            if res:
                ok += 1
                results.append(res)
            if err:
                fail += 1
                failures.append(err)
            if done % 50 == 0 or done == total:
                pd.DataFrame(results).to_csv(SUMMARY_PATH, index=False)
                pd.DataFrame(failures).to_csv(FAIL_PATH, index=False)
                write_progress(done, total, ok, fail)
                print(f"progress {done}/{total} ok={ok} fail={fail}", flush=True)

    pd.DataFrame(results).to_csv(SUMMARY_PATH, index=False)
    pd.DataFrame(failures).to_csv(FAIL_PATH, index=False)
    write_progress(done, total, ok, fail)
    print(f"saved {len(results)} rows to {SUMMARY_PATH}; failures={len(failures)}")


if __name__ == "__main__":
    main()
