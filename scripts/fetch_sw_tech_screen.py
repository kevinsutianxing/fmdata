#!/usr/bin/env python3
"""SW 金工 screen_technical 买点共振票池 → fmdata 落地 (2026-11-10, GVRM 晨会看板素材).

每日收盘后预计算的全 A 多维技术筛选, bullish_consensus=true 口径(V1.57: 缸论一/二/三买
≤10 根 K 线在场, 历史 20 日绝对上涨胜率 IS 52.5%/OOS 57.4%; 旧四流派交集口径已废弃):
  sw/sw_tech_screen.csv        每股一行: data_date, rank, ts_code..wave_position 等
  sw/sw_tech_screen_meta.csv   data_date, stocks_scanned, total_matched, funnel(json), note
幂等: data_date 已存在即跳过。🔴规则筛选非荐股, 页面必须带 data_date 与免责声明。
用法: python3 fetch_sw_tech_screen.py
cron : 19:05 周一~五 (SW 收盘预计算后), 失败飞书告警。
密钥 : fmdata.recipe_fetcher 自带 SW_MCP_URL / SW_MCP_CODE (.env)。
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/data/fmdata")
sys.path.insert(0, "/data/fmdata/scripts")
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE  # noqa: E402

CST = timezone(timedelta(hours=8))
STORE = Path("/data/fmdata/store/data/sw")
LIMIT = 100  # 服务端单次上限内; 票池总量历史 ~40-120 只


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cst()}] {msg}", flush=True)


def existing_dates(rel: str) -> set:
    p = STORE / rel
    if not p.exists():
        return set()
    df = pd.read_csv(p, dtype=str)
    return set(df["data_date"].dropna())


def append_csv(df: pd.DataFrame, rel: str):
    p = STORE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    header = not p.exists()
    df.to_csv(p, mode="a", header=header, index=False, encoding="utf-8")
    return len(df)


ROW_COLS = ["rank", "ts_code", "name", "industry", "close", "ma_alignment", "ret_20",
            "trend_type", "trend_strength", "vol_ratio", "chan_trend", "vs_zhongshu",
            "buy_point", "divergence", "wave_position", "school_chan", "school_pattern"]


def main():
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    r = cli.call("screen_technical", {"bullish_consensus": True, "limit": LIMIT}, timeout=120)
    data_date = str(r.get("data_date", ""))
    rows = r.get("rows") or []
    if not data_date or not rows:
        log(f"返回异常: data_date={data_date!r}, rows={len(rows)}, keys={list(r)[:8]}")
        sys.exit(1)
    d = f"{data_date[:4]}-{data_date[4:6]}-{data_date[6:8]}"

    have = existing_dates("sw_tech_screen.csv")
    if d in have:
        log(f"{d} 已存在, 跳过")
        return

    df = pd.DataFrame(rows)
    keep = [c for c in ROW_COLS if c in df.columns]
    df = df[keep].copy()
    df.insert(0, "data_date", d)
    n = append_csv(df, "sw_tech_screen.csv")

    funnel = r.get("funnel") or {}
    meta = pd.DataFrame([{
        "data_date": d,
        "generated_at": r.get("generated_at", ""),
        "stocks_scanned": r.get("stocks_scanned"),
        "total_matched": r.get("total_matched"),
        "stored_rows": n,
        "funnel": json.dumps(funnel, ensure_ascii=False),
        "note": str(r.get("filters", {}).get("bullish_consensus", ""))[:500],
        "fetched_at": now_cst(),
    }])
    append_csv(meta, "sw_tech_screen_meta.csv")
    log(f"{d}: 扫描{r.get('stocks_scanned')} 只, 共振{r.get('total_matched')} 只, 入库{n} 行")


if __name__ == "__main__":
    main()
