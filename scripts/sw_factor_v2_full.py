#!/usr/bin/env python3
"""SW 新因子库(V1.62) 44 因子分析口径全历史拉取 → sw_factor_value_v2.csv(新口径基统一文件)。

与旧 sw_factor_value.csv 的关系: 旧文件保留作"旧基历史档案"(≤2026-07 旧z基/≥2026-08 新基的拼接体,
见 sw_factor_value.provenance-20261104.md); 本文件 = 全历史统一新基, 2009-01 起风格/2016 起筹码/2019 起ML。
用法: setsid nohup python3 sw_factor_v2_full.py >> ~/fmdata/logs/sw_factor_v2_full.log 2>&1 &
断点续跑: 已有完整月份(≥1000行)自动跳过; 最近3个月强制重拉。追加式落盘, 结束时一次合并去重排序。
节奏: 59批/月×~0.55s≈35s/月, 214个月≈2.1h。
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

OUT = STORE_DIR / "factors/sw_factor_value_v2.csv"
UNI = STORE_DIR / "factors/sw_universe.csv"
START = "2016-01"  # 2009-2015 风格前史作后补批次(44因子×100股单调用~5s, 2016起128月优先)
BATCH = 100
SLEEP = 0.15
FLUSH_EVERY = 6
MIN_ROWS = 1000  # 完整月阈值(2009年最少在市~1600只, 安全)

V2_KEYS = [
    "估值", "低波", "低流动性", "分析师", "动量", "反转", "市值", "成长", "盈利", "红利",
    "筹码成本差", "筹码成本", "机构筹码集中度", "筹码合成", "行业轮动", "GBM量价",
    "EP", "BP", "SP", "CFP", "IVOL20", "IVOL60", "IRSQ20", "IRSQ60",
    "TURN20", "TURN60", "AMIHUD20", "MOMENTUM", "REV1M", "REV3M", "SIZE",
    "INCOME_GROWTH", "PROFIT_GROWTH", "ROE", "ROA", "DELTA_ROE", "DELTA_ROA",
    "SUE", "SUR", "UP_RATIO", "CHG_CONSENSUS", "DIVIDEND", "RF量价", "XGB量价",
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def done_months() -> set:
    if not OUT.exists():
        return set()
    cnt = {}
    for chunk in pd.read_csv(OUT, usecols=["date"], chunksize=200000):
        for d, n in chunk["date"].value_counts().items():
            cnt[d] = cnt.get(d, 0) + n
    return {d for d, n in cnt.items() if n >= MIN_ROWS}


def append_rows(rows: list) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["date", "ts_code"], keep="last")
    header = not OUT.exists()
    df.to_csv(OUT, mode="a", header=header, index=False)


def consolidate() -> None:
    if not OUT.exists():
        return
    log("consolidate: 读全量去重排序(单次)...")
    df = pd.read_csv(OUT)
    df = df.drop_duplicates(subset=["date", "ts_code"], keep="last")
    df = df.sort_values(["date", "ts_code"]).reset_index(drop=True)
    tmp = OUT.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.rename(OUT)
    log(f"consolidate: {len(df)} rows -> {OUT}")


def main() -> int:
    codes = pd.read_csv(UNI)["ts_code"].tolist()
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    done = done_months()
    log(f"universe={len(codes)} 已有完整月份={len(done)} factors={len(V2_KEYS)}")

    end_period = pd.Period.now(freq="M") - 1
    months = pd.period_range(START, end_period, freq="M")
    force = {str(m.end_time.date()) for m in months[-3:]}
    todo = [m for m in months if str(m.end_time.date()) not in done or str(m.end_time.date()) in force]
    if not todo:
        log("无可回填")
        consolidate()
        return 0
    log(f"待回填 {len(todo)} 个月: {todo[0]} → {todo[-1]}")

    from concurrent.futures import ThreadPoolExecutor
    import threading

    _tl = threading.local()

    def get_cli():
        """每线程独立 session(服务端按 session 串行, 共用 client 的并发无效)"""
        if not hasattr(_tl, "cli"):
            _tl.cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
        return _tl.cli

    def pull_batch(task):
        date, i, chunk = task
        for attempt in range(3):
            try:
                d = get_cli().call("get_factor_value",
                                   {"factors": V2_KEYS, "ts_codes": chunk, "date": date}, 120)
                time.sleep(SLEEP)
                return i, d.get("records", [])
            except Exception as e:
                log(f"ERROR {date} batch@{i} try{attempt+1}: {str(e)[:120]}")
                time.sleep(5 * (attempt + 1))
        return i, None

    buf, since_flush, consecutive_dead = [], 0, 0
    with ThreadPoolExecutor(max_workers=4) as pool:
      for m in todo:
        date = str(m.end_time.date())
        tasks = [(date, i, codes[i:i + BATCH]) for i in range(0, len(codes), BATCH)]
        results = list(pool.map(pull_batch, tasks))
        results.sort(key=lambda x: x[0])
        month_failed = any(r is None for _, r in results)
        rows = [row for _, r in results if r for row in r]
        if not month_failed:
            consecutive_dead = 0
        if month_failed:
            if consecutive_dead >= 5:
                log("服务端连续 5 个月失败,疑似宕机 — 退出留待续跑")
                return 1
            continue
        if not rows:
            log(f"WARN {date}: 0 行,跳过(该月或无数据)")
            continue
        buf.extend(rows)
        since_flush += 1
        log(f"{date}: +{len(rows)} 行 (buffer {len(buf)})")
        if since_flush >= FLUSH_EVERY or m is todo[-1]:
            append_rows(buf)
            log(f"flush OK -> {OUT}")
            buf, since_flush = [], 0
    if buf:
        append_rows(buf)
    consolidate()
    log("V2 BACKFILL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
