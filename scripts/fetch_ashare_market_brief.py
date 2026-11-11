#!/usr/bin/env python3
"""A股盘面硬数字日度抓取 (2026-11-10, GVRM 晨会看板 T-0 盘面速览).

东吴游资复盘发布滞后 1-2 交易日, 晨会"昨日盘面"不能跟着滞后——涨停/跌停/炸板/
连板梯队/指数涨跌/成交额改由 tushare 本地落地, 当日收盘后即得:
  market/ashare_limit_list.csv   tushare limit_list_d (U涨停/D跌停/Z炸板, 含 limit_times 连板数)
  market/ashare_index_brief.csv  tushare index_daily (上证/深成/创业板/深综, 涨跌幅+成交额)
幂等: 按 trade_date+limit_type / trade_date+ts_code 去重, 首写冻结。
用法: python3 fetch_ashare_market_brief.py [--date YYYYMMDD]
cron : 17:05 daily (收盘后数据落地) + 07:45 自愈; 失败飞书告警。
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import tushare as ts

CST = timezone(timedelta(hours=8))
ENV_PATH = Path("/data/fmdata/.env")
STORE = Path("/data/fmdata/store/data/market")
INDEX_CODES = {"000001.SH": "上证指数", "399001.SZ": "深证成指",
               "399006.SZ": "创业板指", "399106.SZ": "深证综指"}


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cst()}] {msg}", flush=True)


def load_env():
    env = {}
    for line in open(ENV_PATH):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def append_csv(df: pd.DataFrame, rel: str):
    p = STORE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    header = not p.exists()
    df.to_csv(p, mode="a", header=header, index=False, encoding="utf-8")
    return len(df)


def existing_keys(rel: str, cols):
    p = STORE / rel
    if not p.exists():
        return set()
    df = pd.read_csv(p, dtype=str)
    return set(map(tuple, df[cols].astype(str).values.tolist()))



def compute_money_effect(pro, td, lim: pd.DataFrame) -> float | None:
    """赚钱效应(与东吴同口径): 前一交易日涨停股在今日的昨收涨幅算术均(%)。
    涨幅源=tushare pro.daily(标准 pct_chg, P0-2 核验口径); ≥5 只才出数。
    落盘 market/ashare_money_effect.csv (幂等按日期)。"""
    dates = sorted(x for x in lim["trade_date"].dropna().unique() if x and x < td)
    if not dates:
        return None
    prev = dates[-1]
    prev_u = set(lim[(lim["trade_date"] == prev) & (lim["limit_type"] == "U")]["ts_code"].dropna())
    prev_u = {x for x in prev_u if x}
    if not prev_u:
        return None
    daily = pro.daily(trade_date=td)
    if daily is None or daily.empty:
        return None
    m = daily[daily["ts_code"].isin(prev_u)]["pct_chg"].dropna()
    if len(m) < 5:
        return None
    return round(float(m.mean()), 2)


def append_money_effect(td: str, value: float):
    p = STORE / "ashare_money_effect.csv"
    df = pd.read_csv(p, dtype=str) if p.exists() else pd.DataFrame(columns=["date", "money_effect"])
    df = df[df["date"] != td]
    df = pd.concat([df, pd.DataFrame([{"date": td, "money_effect": value}])], ignore_index=True)
    df.to_csv(p, index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    env = load_env()
    pro = ts.pro_api(env["TUSHARE_TOKEN"])
    today = datetime.now(CST).strftime("%Y%m%d")

    if args.date:
        td = args.date
    else:
        cal = pro.trade_cal(exchange="SSE", is_open="1",
                            start=(datetime.now(CST) - timedelta(days=15)).strftime("%Y%m%d"),
                            end=today)
        # trade_cal 的 end 过滤不可靠(会返回未来年份日历), 必须显式截断
        td = str(cal[cal["cal_date"] <= today]["cal_date"].max())
    log(f"目标交易日: {td}")

    # ── 涨跌停/炸板 ──
    # 🔴2026-11-12 修复: have 只认非空数据行——空标记行(ts_code为空)不算"已有",
    # 否则 07:45 晨班写的标记会挡住 17:05 晚班的正式填充(11-11 即因此缺数)。
    _lim_all = pd.read_csv(STORE / "ashare_limit_list.csv", dtype=str) if (STORE / "ashare_limit_list.csv").exists() else pd.DataFrame(columns=["trade_date", "limit_type", "ts_code"])
    _real = _lim_all[_lim_all["ts_code"].notna() & (_lim_all["ts_code"] != "")]
    have = set(zip(_real["trade_date"], _real["limit_type"]))
    n = 0
    for lt in ("U", "D", "Z"):
        if (td, lt) in have:
            continue
        df = pro.limit_list_d(trade_date=td, limit_type=lt)
        time.sleep(0.35)
        if df is None or df.empty:
            log(f"  limit {lt}: 0 行 (正常可能, 如跌停0只) — 记空标记防反复重试")
            mark = pd.DataFrame([{"trade_date": td, "limit_type": lt, "ts_code": "",
                                  "name": "", "industry": "", "close": "", "pct_chg": "",
                                  "amount": "", "limit_amount": "", "limit_times": "",
                                  "fetched_at": now_cst()}])
            n += append_csv(mark, "ashare_limit_list.csv")
            continue
        df = df.assign(trade_date=td, limit_type=lt, fetched_at=now_cst())
        keep = [c for c in ("trade_date", "limit_type", "ts_code", "name", "industry",
                            "close", "pct_chg", "amount", "limit_amount", "limit_times",
                            "fetched_at") if c in df.columns]
        _mask = (_lim_all["trade_date"] == td) & (_lim_all["limit_type"] == lt) & (_lim_all["ts_code"].isna() | (_lim_all["ts_code"] == "") | _lim_all["ts_code"].isnull())
        if _mask.any():
            _lim_all = _lim_all[~_mask]
            _lim_all.to_csv(STORE / "market/ashare_limit_list.csv", index=False)
            log(f"  limit {lt}: 清除旧空标记 {_mask.sum()} 行")
        n += append_csv(df[keep], "ashare_limit_list.csv")
        log(f"  limit {lt}: +{len(df)} 行")
    # 空标记行不参与统计(消费侧过滤 ts_code 非空)

    # ── 指数 ──
    have_i = existing_keys("ashare_index_brief.csv", ["trade_date", "ts_code"])
    rows = []
    for code, name in INDEX_CODES.items():
        if (td, code) in have_i:
            continue
        df = pro.index_daily(trade_date=td, ts_code=code)
        time.sleep(0.35)
        if df is None or df.empty:
            log(f"  index {code}: 空")
            continue
        r = df.iloc[0]
        rows.append({"trade_date": td, "ts_code": code, "name": name,
                     "close": r["close"], "pct_chg": r["pct_chg"],
                     "amount_qian": r["amount"], "fetched_at": now_cst()})
    if rows:
        n += append_csv(pd.DataFrame(rows), "ashare_index_brief.csv")
        log(f"  index: +{len(rows)} 行")
    # 赚钱效应: fetch 时算好落盘(build_cn_board_pack 只读本地)
    try:
        _lim = pd.read_csv(STORE / "ashare_limit_list.csv", dtype=str)
        _me = compute_money_effect(pro, td, _lim)
        if _me is not None:
            append_money_effect(td, _me)
            log(f"  money_effect[{td}] = {_me}%")
        else:
            log(f"  money_effect[{td}]: 样本不足/无前日涨停池, 跳过")
    except Exception as e:
        log(f"  money_effect 计算失败(不影响其余): {type(e).__name__}: {e}")
    log(f"done +{n} 行")


if __name__ == "__main__":
    main()
