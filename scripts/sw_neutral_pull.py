#!/usr/bin/env python3
"""SW 新因子库 10 张风格中性化表(*_Neutral)月末采样 → sw_neutral_monthend.csv(宽表, 列=估值_中性 等)。

中性化版=合成因子剔除共线风格后的残差强度, 对指增 TE 控制直接有用。日频表取月末(全月最后交易日)。
用法: setsid nohup python3 sw_neutral_pull.py >> ~/fmdata/logs/sw_neutral_pull.log 2>&1 &
断点续跑: 已有 (date,ts_code) 跳过; codes 对半规避 5000 行上限。
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

OUT = STORE_DIR / "factors/sw_neutral_monthend.csv"
UNI = STORE_DIR / "factors/sw_universe.csv"
SLEEP = 0.25

TABLES = {  # 英文表名 -> 中文列名(标准术语+首现注释: _中性=该风格剔除市值/行业等共线暴露后的残差强度)
    "CN_ASHARE_STYLE/Value_Neutral": "估值_中性",
    "CN_ASHARE_STYLE/Volatility_Neutral": "低波_中性",
    "CN_ASHARE_STYLE/Liquidity_Neutral": "低流动性_中性",
    "CN_ASHARE_STYLE/Analyst_Neutral": "分析师_中性",
    "CN_ASHARE_STYLE/Momentum_Neutral": "动量_中性",
    "CN_ASHARE_STYLE/Reverse_Neutral": "反转_中性",
    "CN_ASHARE_STYLE/Size_Neutral": "市值_中性",
    "CN_ASHARE_STYLE/Growth_Neutral": "成长_中性",
    "CN_ASHARE_STYLE/Profitable_Neutral": "盈利_中性",
    "CN_ASHARE_STYLE/Dividend_Neutral": "红利_中性",
}


def log(msg):
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def fetch(cli, table, start, end, codes):
    for attempt in range(3):
        try:
            d = cli.call("factorlib_query_factor",
                         {"name": table, "start": start, "end": end, "codes": codes}, 120)
            return d.get("data", [])
        except Exception as e:
            log(f"ERROR {table} {start} try{attempt+1}: {str(e)[:120]}")
            time.sleep(5 * (attempt + 1))
    return None


def main():
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    codes = pd.read_csv(UNI)["ts_code"].tolist()
    half = (len(codes) + 1) // 2
    halves = [codes[:half], codes[half:]]
    have = set()
    if OUT.exists():
        try:
            for chunk in pd.read_csv(OUT, usecols=["date", "ts_code"], chunksize=200000):
                have |= set(zip(chunk["date"], chunk["ts_code"]))
        except Exception:
            pass
    log(f"universe={len(codes)} 已有对={len(have)} tables={len(TABLES)}")

    months = pd.period_range("2016-01", pd.Period.now(freq="M") - 1, freq="M")
    have_months = {d[:7] for d, _ in have}
    for m in months:
        if f"{m.year:04d}-{m.month:02d}" in have_months:
            continue  # 月级跳过: 已有月不重查
        start = (m.end_time - pd.Timedelta(days=12)).strftime("%Y%m%d")
        end = m.end_time.strftime("%Y%m%d")
        month_rows = {}
        for table in TABLES:
            rows = []
            for h in halves:
                r = fetch(cli, table, start, end, h)
                if r is None:
                    rows = None
                    break
                rows += r
            if rows is None:
                log(f"SKIP {m} {table}: 拉取失败留待续跑")
                continue
            if rows:
                last = max(x["TRADE_DATE"] for x in rows)
                for x in rows:
                    if x["TRADE_DATE"] == last:
                        key = x["CODE"]
                        if key not in month_rows:
                            month_rows[key] = {}
                        month_rows[key][TABLES[table]] = x.get(f"{table.split('/')[-1]}", None) or next(iter([v for k, v in x.items() if k not in ("TRADE_DATE", "CODE")]), None)
        if not month_rows:
            log(f"{m}: 0 行")
            continue
        date_str = str(pd.Period(m).end_time.date())
        recs = [{"date": date_str, "ts_code": k, **v} for k, v in month_rows.items()
                if (date_str, k) not in have]
        if recs:
            df = pd.DataFrame(recs)
            header = not OUT.exists()
            df.to_csv(OUT, mode="a", header=header, index=False)
            have |= set(zip(df["date"], df["ts_code"]))
            log(f"{m}: +{len(df)} 行 x {len(TABLES)} 列")
        else:
            log(f"{m}: 全部已有")
        time.sleep(SLEEP)

    if OUT.exists():
        df = pd.read_csv(OUT)
        df = df.drop_duplicates(subset=["date", "ts_code"], keep="last")
        df = df.sort_values(["date", "ts_code"]).reset_index(drop=True)
        tmp = OUT.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        tmp.rename(OUT)
        log(f"consolidate: {len(df)} rows -> {OUT}")
    log("NEUTRAL PULL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
