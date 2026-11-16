#!/usr/bin/env python3
"""SW 筹码 7 列月末截面全量(含新增 ADJUSTED_HOLDING_COST/ADJUSTED_COMPOSITE_CHIP_FACTOR/INDUSTRY_MV_NEUTRAL_SCORE)。

来源: CN_ASHARE_CHIP_MONTHLY(2015-01→2026-08) + CN_ASHARE_CHIP(日频表取 2026-09 起月末, 月度表停更后补位)。
用法: setsid nohup python3 sw_chip_full.py >> ~/fmdata/logs/sw_chip_full.log 2>&1 &
断点续跑: 已有 (date,code) 跳过。codes 对半分批规避 5000 行上限。
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

OUT = STORE_DIR / "factors/sw_chip_monthend_v2.csv"
UNI = STORE_DIR / "factors/sw_universe.csv"
SLEEP = 0.25


def log(msg):
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def fetch(cli, table, start, end, codes):
    for attempt in range(3):
        try:
            d = cli.call("factorlib_query_factor",
                         {"name": table, "start": start, "end": end, "codes": codes}, 120)
            return d.get("data", [])
        except Exception as e:
            log(f"ERROR {table} {start}-{end} try{attempt+1}: {str(e)[:120]}")
            time.sleep(5 * (attempt + 1))
    return None


def append(df):
    if df.empty:
        return 0
    header = not OUT.exists()
    df.to_csv(OUT, mode="a", header=header, index=False)
    return len(df)


def main():
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    codes = pd.read_csv(UNI)["ts_code"].tolist()
    half = (len(codes) + 1) // 2
    halves = [codes[:half], codes[half:]]
    have = set()
    if OUT.exists():
        try:
            for chunk in pd.read_csv(OUT, chunksize=200000):
                have |= set(zip(chunk["date"], chunk["ts_code"]))
        except Exception:
            pass
    log(f"universe={len(codes)} 已有对={len(have)}")

    # 1) 全部走日频表 CN_ASHARE_CHIP(2016-01 起月度采样; 月度表 CHIP_MONTHLY 已实测为空壳, 弃用)
    #    逐日回走: 从日历月末往前逐日单日查询, 首个非空日=月末实际交易日(单日×半宇宙≈2950行<5000)
    for m in pd.period_range("2016-01", pd.Period.now(freq="M") - 1, freq="M"):
        got = None
        for day_off in range(7):
            d = (m.end_time - pd.Timedelta(days=day_off)).strftime("%Y%m%d")
            rows = []
            fail = False
            for h in halves:
                r = fetch(cli, "CN_ASHARE_CHIP/CN_ASHARE_CHIP", d, d, h)
                if r is None:
                    fail = True
                    break
                rows += r
            if fail:
                log(f"SKIP {m} day{d}: 拉取失败留待续跑")
                break
            if rows:
                got = rows
                break
        if got is None:
            continue
        df = pd.DataFrame(got)
        df = df.rename(columns={"TRADE_DATE": "date", "CODE": "ts_code"})
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.date.astype(str)
        df = df[~df.apply(lambda x: (x["date"], x["ts_code"]) in have, axis=1)]
        n = append(df)
        have |= set(zip(df["date"], df["ts_code"]))
        log(f"{m}: +{n} 行")
        time.sleep(SLEEP)

    # 合并去重排序(单次)
    if OUT.exists():
        df = pd.read_csv(OUT)
        df = df.drop_duplicates(subset=["date", "ts_code"], keep="last")
        df = df.sort_values(["date", "ts_code"]).reset_index(drop=True)
        tmp = OUT.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        tmp.rename(OUT)
        log(f"consolidate: {len(df)} rows -> {OUT}")
    log("CHIP V2 DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
