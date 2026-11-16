#!/usr/bin/env python3
"""SW 新因子库原始值月度宽表: 24 张表 → sw_raw_monthend_v2.csv (43 列, 原始值非z)。

背景: get_factor_value 临时停用且替代 get_factor_series 与其口径又不一致(一天两换) —
放弃服务端z口径, 统一原始值(研究层 RankIC/池内z 都不依赖服务端z; R26 实战即值上再z)。
筹码 7 列已有 sw_chip_monthend_v2.csv; 行业轮动走行业层测试, 不在本表。
用法: setsid nohup python3 sw_raw_monthend_pull.py >> ~/fmdata/logs/sw_raw_monthend_pull.log 2>&1 &
逐日回走取月末实际交易日; codes 对半; 断点续跑按 (date,ts_code)。
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

import os
OUT = STORE_DIR / os.environ.get("SW_OUT", "factors/sw_raw_monthend_v2.csv")
UNI = STORE_DIR / "factors/sw_universe.csv"
SLEEP = 0.15

# 表名 -> {原始列名后缀: 中文短列名}
TABLES = {
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_VALUE": {"__EP": "EP", "__BP": "BP", "__SP": "SP", "__CFP": "CFP"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_VOLATILITY": {"__IVOL20": "IVOL20", "__IVOL60": "IVOL60", "__IRSQ20": "IRSQ20", "__IRSQ60": "IRSQ60"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_LIQUIDITY": {"__TURN20": "TURN20", "__TURN60": "TURN60", "__AMIHUD20": "AMIHUD20"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_MOMENTUM": {"": "MOMENTUM"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_REVERSE": {"__REV1M": "REV1M", "__REV3M": "REV3M"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_SIZE": {"": "SIZE"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_GROWTH": {"__INCOME_GROWTH": "INCOME_GROWTH", "__PROFIT_GROWTH": "PROFIT_GROWTH"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_ROE": {"__ROE": "ROE", "__ROA": "ROA", "__DELTA_ROE": "DELTA_ROE", "__DELTA_ROA": "DELTA_ROA"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_SUE": {"__SUE": "SUE", "__SUR": "SUR"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_ANALYST": {"__UP_RATIO": "UP_RATIO", "__CHG_CONSENSUS": "CHG_CONSENSUS"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_DIVIDEND": {"": "DIVIDEND"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Value": {"": "估值"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Volatility": {"": "低波"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Liquidity": {"": "低流动性"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Analyst": {"": "分析师"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Momentum": {"": "动量"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Reverse": {"": "反转"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Size": {"": "市值"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Growth": {"": "成长"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Profitable": {"": "盈利"},
    "CN_ASHARE_STYLE/CN_ASHARE_STYLE_COMPOSITE_Dividend": {"": "红利"},
    "CN_ASHARE_ML/CN_ASHARE_ML_LGBM": {"": "GBM量价"},
    "CN_ASHARE_ML/CN_ASHARE_ML_RF": {"": "RF量价"},
    "CN_ASHARE_ML/CN_ASHARE_ML_XGB": {"": "XGB量价"},
}


FIXED_COLS = ["date", "ts_code"] + [short for m in TABLES.values() for short in m.values()]


def log(msg):
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def fetch(cli, table, d, codes):
    for attempt in range(3):
        try:
            r = cli.call("factorlib_query_factor", {"name": table, "start": d, "end": d, "codes": codes}, 120)
            return r.get("data", [])
        except Exception as e:
            log(f"ERROR {table} {d} try{attempt+1}: {str(e)[:100]}")
            time.sleep(4 * (attempt + 1))
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

    months = pd.period_range(os.environ.get("SW_START", "2016-01"),
                         os.environ.get("SW_END", str(pd.Period.now(freq="M") - 1)), freq="M")
    for m in months:
        date_str = str(m.end_time.date())
        if sum(1 for c in codes[:50] if (date_str, c) in have) >= 30:
            log(f"{m}: 已有,跳过")
            continue
        # 逐日回走找月末实际交易日
        month_day = None
        for day_off in range(10):
            d = (m.end_time - pd.Timedelta(days=day_off)).strftime("%Y%m%d")
            probe = fetch(cli, "CN_ASHARE_STYLE/CN_ASHARE_STYLE_SIZE", d, codes[:50])
            if probe:
                month_day = d
                break
            if probe is None:
                break
        if month_day is None:
            log(f"{m}: 找不到月末交易日,跳过")
            continue
        cells = {}  # ts_code -> {col: val}
        for table, colmap in TABLES.items():
            rows = []
            for h in halves:
                r = fetch(cli, table, month_day, h)
                if r is None:
                    rows = None
                    break
                rows += r
            if rows is None:
                log(f"WARN {m} {table}: 失败留待续跑")
                continue
            tname = table.split("/")[-1]
            for x in rows:
                key = x["CODE"]
                if key not in cells:
                    cells[key] = {}
                for suf, short in colmap.items():
                    cells[key][short] = x.get(tname + suf)
        recs = [{"date": date_str, "ts_code": k, **v} for k, v in cells.items()
                if (date_str, k) not in have]
        if recs:
            df = pd.DataFrame(recs).reindex(columns=FIXED_COLS)
            header = not OUT.exists()
            df.to_csv(OUT, mode="a", header=header, index=False)
            have |= set(zip(df["date"], df["ts_code"]))
            log(f"{m} (day {month_day}): +{len(df)} 行")
        else:
            log(f"{m}: 0 新行")
        time.sleep(SLEEP)

    # 2026-11-04: 排序合并步骤移除(3.6G 小机全文件排序 OOM; 固定 schema 后乱序无碍分析层)
    log("RAW MONTHEND DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
