#!/usr/bin/env python3
"""SW 金工 get_pattern_map + get_macro_snapshot → fmdata 落地 (2026-11-11, GVRM 晨会看板素材).

块1 全市场形态温度 (get_pattern_map, 每日预计算):
  sw/sw_pattern_map.csv   每日一行: temperature(多空计数/突破确认) + by_pattern/by_industry(JSON列)
  (Top400 气泡明细不入库——看板只要聚合温度; 需要明细时 agent 直连 get_pattern_map)
块2 利率与商品快照 (get_macro_snapshot):
  sw/sw_macro_snapshot.csv  每 series 一行: SHIBOR隔夜(bp) + 黄金/原油/螺纹/铜 5/20/60日变动

幂等: data_date 已存在即跳过。🔴规则聚合非荐股, 页面必须带 data_date/generated_at 与免责声明。
用法: python3 fetch_sw_pattern_macro.py
cron : 19:10 周一~五 (SW 收盘预计算后, 在 pack 19:35 构建前), 失败飞书告警。
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


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cst()}] {msg}", flush=True)


def existing_dates(rel: str) -> set:
    p = STORE / rel
    if not p.exists():
        return set()
    return set(pd.read_csv(p, dtype=str)["data_date"].dropna())


def append_csv(df: pd.DataFrame, rel: str):
    p = STORE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    header = not p.exists()
    df.to_csv(p, mode="a", header=header, index=False, encoding="utf-8")
    return len(df)


def norm_date(s: str) -> str:
    s = str(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def fetch_pattern_map(cli):
    r = cli.call("get_pattern_map", {}, timeout=120)
    data_date = norm_date(r.get("data_date", ""))
    t = r.get("temperature") or {}
    if not data_date or not t:
        log(f"pattern_map 返回异常: data_date={data_date!r}, keys={list(r)[:8]}")
        sys.exit(1)
    if data_date in existing_dates("sw_pattern_map.csv"):
        log(f"pattern_map {data_date} 已存在, 跳过")
        return
    row = {
        "data_date": data_date,
        "generated_at": r.get("generated_at", ""),
        "stocks_scanned": r.get("stocks_scanned"),
        "bullish": t.get("bullish"),
        "bearish": t.get("bearish"),
        "bullish_confirmed": t.get("bullish_confirmed"),
        "bearish_confirmed": t.get("bearish_confirmed"),
        "by_pattern_json": json.dumps(r.get("by_pattern") or [], ensure_ascii=False),
        "by_industry_json": json.dumps(r.get("by_industry") or [], ensure_ascii=False),
        "fetched_at": now_cst(),
    }
    n = append_csv(pd.DataFrame([row]), "sw_pattern_map.csv")
    log(f"pattern_map {data_date}: 扫描{row['stocks_scanned']} 多{row['bullish']}/空{row['bearish']} 入库{n} 行")


def fetch_macro(cli):
    r = cli.call("get_macro_snapshot", {"days": 60}, timeout=120)
    series = r.get("series") or []
    if not series:
        log(f"macro_snapshot 返回异常: keys={list(r)[:8]}")
        sys.exit(1)
    data_date = max(str(s.get("latest_date", "")) for s in series)
    data_date = norm_date(data_date)
    if data_date in existing_dates("sw_macro_snapshot.csv"):
        log(f"macro {data_date} 已存在, 跳过")
        return
    df = pd.DataFrame([{
        "data_date": data_date,
        "series": s.get("series"),
        "name": s.get("name") or s.get("series"),
        "latest_date": norm_date(s.get("latest_date", "")),
        "latest": s.get("latest"),
        "unit": s.get("unit") or "",
        "chg_5d_bp": s.get("chg_5d_bp"),
        "chg_20d_bp": s.get("chg_20d_bp"),
        "chg_60d_bp": s.get("chg_60d_bp"),
        "chg_5d": s.get("chg_5d"),
        "chg_20d": s.get("chg_20d"),
        "chg_60d": s.get("chg_60d"),
        "fetched_at": now_cst(),
    } for s in series])
    n = append_csv(df, "sw_macro_snapshot.csv")
    log(f"macro {data_date}: {len(series)} 序列入库{n} 行")


def main():
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    fetch_pattern_map(cli)
    time.sleep(1.2)  # 尊重服务端节奏
    fetch_macro(cli)


if __name__ == "__main__":
    main()
