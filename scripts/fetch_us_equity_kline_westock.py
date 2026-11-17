#!/usr/bin/env python3
"""美股K线 westock 主源拉取（2026-11-17，替换 EM 主源止血 QG 代理池）。

背景: EM push2his 经 QG 代理拒收率 ~96%（reuses=0 占 1335/1390），日均烧
~1800 IP 占池子 82%（usage log 实证）。westock data_kline 官方认证 API、
零代理消耗，2026-11-17 实测 36/36 全覆盖（含 ETF，usSPY/usXXX.OQ 均可）、
T+1 晨间已到棒、单标的 ~0.6s。

口径（与 gfclaw fallback 同款，收益重定基）:
  westock 返回原始价（=gfclaw day_last_px 分毫不差，2026-11-17 实证）。
  EM 库存是 hfq 重定基的 qfq 序列 → 不能直接拼价格，只能续收益:
    新棒 close = 库存最后一根 close × Π(1 + c_t/c_{t-1})（westock 序列内收益）
  open/high/low 按当日 close/raw_close 同因子缩放（保留日内形状）。
  volume 原样；amount westock 有值；source=westock 可溯源。

护栏:
  - 异常棒即停（|r_t| > 25% 视为数据坏点，该标的记失败，防漂移放大）。
  - 增量只补 库存最后日期 之后的新棒；不重写历史（原始价无锚定漂移，与
    EM qfq 每日全量重拉的动机不同）。
  - 失败契约与 cron-wrap v3 对齐: exit 2 + stderr 打 "FAILED_SYMBOLS: A,B"。

用法: python3 fetch_us_equity_kline_westock.py [--dry-run] [--symbols A,B]
cron: 经 fetch_us_equity_kline_cron.sh（FUEK_CMD 默认已指本脚本）。
"""
import argparse
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR  # noqa: E402

OUT = STORE_DIR / "market" / "us_equity_kline_em.csv"
FMDATA = "http://127.0.0.1:1934"
SYMS = ["AAPL", "AMAT", "AMD", "AMZN", "ANET", "ASML", "AVGO", "CDNS", "CEG",
        "CRDO", "CRM", "DDOG", "ETN", "GOOGL", "IWM", "KLAC", "LRCX", "META",
        "MRVL", "MSFT", "MU", "NOW", "NVDA", "ORCL", "PLTR", "QCOM", "QQQ",
        "SMH", "SNPS", "SOXQ", "SPY", "TSLA", "TSM", "VRT", "VST", "XLK"]
MAX_RET = 0.25          # 单棒收益异常阈值
SLEEP_BETWEEN = 1.0     # 官方 API 礼貌间隔


def westock_kline(code: str, count: int = 20):
    """拉一个标的的日 K（返回按日期升序的 [{date,open,high,low,last,volume,amount}]）。"""
    req = urllib.request.Request(
        f"{FMDATA}/fetch/westock_query",
        data=json.dumps({"params": {"tool": "data_kline", "codes": code,
                                    "period": "day", "count": count}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        json.loads(r.read())  # 触发拉取
    req2 = urllib.request.Request(f"{FMDATA}/data/westock_query")
    with urllib.request.urlopen(req2, timeout=30) as r:
        d = json.loads(r.read())
    rows = [x for x in d.get("data", []) if x.get("date")]
    rows.sort(key=lambda x: x["date"])
    return rows


def load_inventory():
    """读现有 csv → {sym: {last_date, last_close, name}}。"""
    inv = {}
    with open(OUT) as f:
        for r in csv.reader(f):
            if len(r) < 10 or r[0] == "ts_code":
                continue
            sym, d = r[0], r[1]
            prev = inv.get(sym)
            if prev is None or d > prev["last_date"]:
                inv[sym] = {"last_date": d, "last_close": float(r[5]),
                            "name": r[8] or sym}
    return inv


def build_rows(sym: str, bars: list, anchor_date: str, anchor_close: float, name: str):
    """westock 原始棒 → 重定基后的新行列表。返回 (rows, err_or_None)。"""
    # 找锚定棒（<= anchor_date 的最后一根，用于算第一根新棒的收益）
    hist = [b for b in bars if b["date"].replace("-", "") <= anchor_date]
    if not hist:
        return None, f"westock 无 {anchor_date} 前的锚定棒"
    prev_close = float(hist[-1]["last"])
    new_bars = [b for b in bars if b["date"].replace("-", "") > anchor_date]
    if not new_bars:
        return [], None  # 已最新
    base = anchor_close
    raw_prev = prev_close
    out = []
    for b in new_bars:
        c = float(b["last"])
        if raw_prev <= 0:
            return None, "raw_prev<=0"
        r = c / raw_prev - 1.0
        if abs(r) > MAX_RET:
            return None, f"异常棒 {b['date']} r={r:.2%} 即停"
        base = base * (1.0 + r)
        factor = base / c if c else 1.0
        out.append({
            "ts_code": sym, "trade_date": b["date"].replace("-", ""),
            "open": round(float(b["open"]) * factor, 6),
            "high": round(float(b["high"]) * factor, 6),
            "low": round(float(b["low"]) * factor, 6),
            "close": round(base, 6),
            "volume": float(b["volume"]),
            "amount": float(b.get("amount") or ""),
            "name": name, "source": "westock",
        })
        raw_prev = c
    return out, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--symbols", help="只拉指定标的(逗号分隔)")
    args = ap.parse_args()

    syms = args.symbols.split(",") if args.symbols else SYMS
    inv = load_inventory()
    failed, appended = [], 0
    new_rows = []
    for i, sym in enumerate(syms):
        try:
            bars = westock_kline(f"us{sym}")
            if not bars:
                failed.append(sym)
                print(f"[fail] {sym}: westock 空数据")
                continue
            meta = inv.get(sym)
            if not meta:
                failed.append(sym)
                print(f"[fail] {sym}: EM 库存无此标的(不做 bootstrap)")
                continue
            rows, err = build_rows(sym, bars, meta["last_date"],
                                   meta["last_close"], meta["name"])
            if err:
                failed.append(sym)
                print(f"[fail] {sym}: {err}")
            elif rows:
                new_rows.extend(rows)
                print(f"[ok:{len(rows)}棒] {sym} {meta['last_date']}→{rows[-1]['trade_date']}")
            else:
                print(f"[latest] {sym} 已最新({meta['last_date']})")
        except Exception as e:
            failed.append(sym)
            print(f"[fail] {sym}: {str(e)[:80]}")
        if i < len(syms) - 1:
            time.sleep(SLEEP_BETWEEN)

    if new_rows and not args.dry_run:
        new_rows.sort(key=lambda r: (r["trade_date"], r["ts_code"]))
        with open(OUT, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ts_code", "trade_date", "open", "high",
                                              "low", "close", "volume", "amount",
                                              "name", "source"])
            w.writerows(new_rows)
        appended = len(new_rows)
    print(f"[{'dry-run' if args.dry_run else 'done'}] {OUT} 新增 {appended or len(new_rows)} 行 "
          f"失败={failed or '无'}")
    if failed:
        print(f"FAILED_SYMBOLS: {','.join(failed)}")
        sys.exit(2)


if __name__ == "__main__":
    main()
