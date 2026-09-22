#!/usr/bin/env python3
"""申万宏源金工 16 因子全历史回填(2016-01 起月度截面,全 universe 5899 只含退市)。

用法: setsid nohup python3 backfill_sw_factors.py >> ~/fmdata/logs/backfill_sw_factors.log 2>&1 &
断点续跑: CSV 里已有的月份自动跳过(按 date 列 distinct)。
节奏: 59 批/月 × ~0.55s ≈ 35s/月,128 个月 ≈ 75 分钟。
坑(已内建处理): ①120 行/响应上限 → 100 只/批 ②礼貌限速 0.15s ③actual_month_end
与请求月不一致时按服务端实际月记录并打 WARN ④每 6 个月落盘一次防中途丢失。
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _SwMcpClient, SW_FACTOR_KEYS, SW_MCP_URL, SW_MCP_CODE

OUT = STORE_DIR / "factors/sw_factor_value.csv"
UNI = STORE_DIR / "factors/sw_universe.csv"
START = "2016-01"
BATCH = 100
SLEEP = 0.15
FLUSH_EVERY = 6  # 每隔多少个月落盘


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def main() -> int:
    codes = pd.read_csv(UNI)["ts_code"].tolist()
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)

    done = set()
    if OUT.exists():
        old = pd.read_csv(OUT, usecols=["date"])
        cnt = old["date"].value_counts()
        # 残月自愈:行数<1000 视为未完成(测试残影/中断月),重拉并入
        done = set(cnt[cnt >= 1000].index)
    log(f"universe={len(codes)} 已有月份={len(done)}: {sorted(done)[:3]}...{sorted(done)[-3:] if done else ''}")

    end_period = pd.Period.now(freq="M") - 1  # 上月(当月快照未出)
    months = pd.period_range(START, end_period, freq="M")
    # 最近 2 个月强制重拉:服务端分批晚发布(实测筹码先于风格数天),dedupe keep=last 幂等
    force = {str(m.end_time.date()) for m in months[-2:]}
    todo = [m for m in months if str(m.end_time.date()) not in done or str(m.end_time.date()) in force]
    log(f"待回填 {len(todo)} 个月: {todo[0]} → {todo[-1]}" if todo else "无可回填")

    buf, since_flush = [], 0
    consecutive_dead = 0
    for m in todo:
        date = str(m.end_time.date())
        rows, actuals, month_failed = [], set(), False
        for i in range(0, len(codes), BATCH):
            chunk = codes[i:i + BATCH]
            d = None
            for attempt in range(3):
                try:
                    d = cli.call("get_factor_value",
                                 {"factors": SW_FACTOR_KEYS, "ts_codes": chunk, "date": date}, 90)
                    break
                except Exception as e:
                    log(f"ERROR {date} batch@{i} try{attempt+1}: {e}")
                    time.sleep(5 * (attempt + 1))
            if d is None:
                log(f"SKIP {date} batch@{i}: 3 次失败,整月放弃留待续跑")
                month_failed = True
                consecutive_dead += 1
                break
            consecutive_dead = 0
            rows.extend(d.get("records", []))
            am = (d.get("metadata") or {}).get("actual_month_end")
            if am:
                actuals.add(am)
            time.sleep(SLEEP)
        if month_failed:
            if consecutive_dead >= 5:
                log("服务端连续 5 个月失败,疑似宕机 — 退出留待续跑")
                return 1
            continue
        if not rows:
            log(f"WARN {date}: 0 行,跳过")
            continue
        for a in actuals:
            if a != date:
                log(f"WARN {date}: 服务端返回 actual_month_end={a}(请求月无快照,按实际月入库)")
        buf.extend(rows)
        since_flush += 1
        log(f"{date}: +{len(rows)} 行 (累计 buffer {len(buf)})")

        if since_flush >= FLUSH_EVERY or m is todo[-1]:
            _flush(buf)
            buf, since_flush = [], 0
    if buf:
        _flush(buf)
    log("BACKFILL DONE")
    return 0


def _flush(rows: list) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    if OUT.exists():
        old = pd.read_csv(OUT)
        df = pd.concat([old, df], ignore_index=True)
    df = df.drop_duplicates(subset=["date", "ts_code"], keep="last")
    df = df.sort_values(["date", "ts_code"]).reset_index(drop=True)
    tmp = OUT.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.rename(OUT)
    log(f"flush: {len(df)} rows total -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
