#!/usr/bin/env python3
"""fmdata agent 脚本: 拉取指定基金的每日单位净值/累计净值，走 QG 代理池。

用法: python3 fetch_fund_nav.py --symbol 009244 [--symbol 009245 ...]
输出: 写入 fmdata store 的 market/fund_nav_{symbol}.csv
"""
import os
import sys
import time
import argparse
import csv
import requests
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv("/home/ubuntu/fmdata/.env")

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy

STORE = "/home/ubuntu/fmdata/store/market"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TZ = timezone(timedelta(hours=8))


def fetch_js(symbol, max_tries=8):
    """通过 QG 代理池拉取 fund.eastmoney.com 的 pingzhongdata js"""
    url = f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js"
    for i in range(max_tries):
        proxy = _get_qg_proxy()
        if not proxy:
            time.sleep(1)
            continue
        _set_requests_proxy(proxy)
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
        except Exception:
            time.sleep(0.5 + i * 0.15)
    return None


def parse_nav(js_text):
    """从 js 文本解析单位净值和累计净值"""
    from py_mini_racer import MiniRacer
    ctx = MiniRacer()
    ctx.eval(js_text)

    unit = ctx.execute("Data_netWorthTrend")   # list of dict {x,y,equityReturn,unitMoney}
    cum = ctx.execute("Data_ACWorthTrend")      # list of list [timestamp_ms, nav]

    # 单位净值
    out = []
    for row in unit:
        dt = datetime.fromtimestamp(row["x"] / 1000, tz=TZ).date().isoformat()
        out.append({
            "date": dt,
            "unit_nav": row["y"],
            "daily_return_pct": row.get("equityReturn"),
        })

    # 累计净值（按日期匹配）
    cum_dict = {}
    for row in cum:
        dt = datetime.fromtimestamp(row[0] / 1000, tz=TZ).date().isoformat()
        cum_dict[dt] = row[1]
    for row in out:
        row["cum_nav"] = cum_dict.get(row["date"])

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append", required=True)
    args = ap.parse_args()

    os.makedirs(STORE, exist_ok=True)

    for sym in args.symbol:
        js = fetch_js(sym)
        if not js:
            print(f"[{sym}] FAIL: proxy exhausted", file=sys.stderr)
            sys.exit(1)
        try:
            rows = parse_nav(js)
        except Exception as e:
            print(f"[{sym}] FAIL parse: {e}", file=sys.stderr)
            sys.exit(1)

        out = os.path.join(STORE, f"fund_nav_{sym}.csv")
        with open(out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "unit_nav", "cum_nav", "daily_return_pct"])
            for r in rows:
                w.writerow([r["date"], r["unit_nav"], r.get("cum_nav"), r["daily_return_pct"]])
        print(f"[{sym}] OK: {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
