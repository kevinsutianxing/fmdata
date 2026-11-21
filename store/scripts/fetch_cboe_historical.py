#!/usr/bin/env python3
"""Cboe 延迟日线全史 → cboe_historical 数据集 (GVRM 统一日线源).

数据源: https://cdn-api.cboe.com/api/global/delayed_quotes/charts/historical/<TICKER>.json
  - 免费无授权 (延迟报价档); 2026-11-21 迁移 cdn→cdn-api 后必须带
    Referer: https://www.cboe.com/ (无此头一律 403, UA 无关)
  - 全史未复权 OHLCV 原始 bar; 指数 payload 数值是字符串, 统一数值化
  - 🔴 原样保留假日幻影 bar/坏 bar (raw 忠实), NYSE 日历/坏 bar 注册表/
    拆股调整由消费端 GVRM 清洗层处理 (risk_monitor/providers/cboe.py)

产出 (STORE/market/cboe_historical/):
  cboe_historical_summary.csv  累计长表 (幂等, 按 (symbol,date) upsert,
                               后抓覆盖先抓 → CBOE 修正历史 bar 会传播)

标的清单: GVRM Cboe universe (2026-11-21 快照, config/universe.yaml):
  36 股/ETF + 指数 _RUT _SPX _VIX _VXN _SKEW _VVIX (下划线=CBOE 原生命名),
  共 42; 🔴 GVRM universe 变动时须同步本清单 (或 FMDATA_PARAMS 覆盖)。
  2026-11-21 起 GVRM sync-cboe-delayed 默认经 fmdata 本表取数, 直连降级为
  手动补数 (--source direct)。

cron: 06:09 / 22:33 (周二~六 = 美股周一~五收盘对应 CST 日):
  22:33 档喂 GVRM 22:40 槽 (当晚收盘 bar 尽力而为, 指数先落地);
  06:09 档兜底 — 股票 bar ~22:30±落地, 迟到者次日晨必齐。

退出码: 失败标的 >20% → exit 1 (cron 飞书告警); 单标的失败不影响其余。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

STORE = Path("/home/ubuntu/fmdata/store/market/cboe_historical")
BASE_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/charts/historical/{}.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 fmdata-cboe-historical/1.0",
    "Referer": "https://www.cboe.com/",  # 🔴无此头一律 403 (cdn-api Cloudflare, 2026-11-21)
    "Accept": "application/json",
}
DEFAULT_TICKERS = [
    # 6 ETF
    "SPY", "QQQ", "IWM", "SOXQ", "SMH", "XLK",
    # 30 US stocks (monitor Cboe universe)
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD",
    "PLTR", "MU", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "ANET", "CDNS",
    "CEG", "CRDO", "CRM", "DDOG", "ETN", "MRVL", "NOW", "ORCL", "QCOM",
    "SNPS", "VRT", "VST",
    # 指数 (CBOE 原生下划线命名): RUT/SPX 收盘 + VIX/VXN/SKEW/VVIX 波动率指数
    "_RUT", "_SPX", "_VIX", "_VXN", "_SKEW", "_VVIX",
]
FIELDS = ["symbol", "date", "open", "high", "low", "close", "volume", "fetched_at"]


def _num(value):
    """'17.240000'→17.24, '0.0'→0, 坏值→None (数值化, 指数 payload 是字符串)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def fetch_history(ticker: str, timeout: int = 60, retries: int = 2) -> dict:
    for attempt in range(retries):
        last_err = None
        try:
            req = urllib.request.Request(BASE_URL.format(ticker), headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            rows = payload.get("data") or []
            if rows and "date" in rows[0] and "close" in rows[0]:
                return payload
            last_err = f"empty/thin data ({len(rows)} rows)"
        except Exception as exc:  # noqa: BLE001 - 网络错误逐标的隔离
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"fetch failed for {ticker}: {last_err}")


def parse_rows(payload: dict, ticker: str, fetched_at: str) -> list[dict]:
    """payload → FIELDS 行; 去重 keep-first (防御性, CBOE 正常无重复日期)."""
    seen: set[str] = set()
    rows: list[dict] = []
    for item in payload.get("data", []):
        date = str(item.get("date", ""))
        if not date or date in seen:
            continue
        seen.add(date)
        rows.append({
            "symbol": ticker,
            "date": date,
            "open": _num(item.get("open")),
            "high": _num(item.get("high")),
            "low": _num(item.get("low")),
            "close": _num(item.get("close")),
            "volume": _num(item.get("volume")),
            "fetched_at": fetched_at,
        })
    return rows


def upsert_cumulative(new_rows: list[dict], path: Path) -> None:
    """追加累计长表, 按 (symbol,date) upsert (后抓覆盖 → 修正传播)。"""
    key_cols = ("symbol", "date")
    existing: dict[tuple, dict] = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                existing[tuple(r[k] for k in key_cols)] = r
    for r in new_rows:
        existing[tuple(str(r[k]) for k in key_cols)] = {k: r[k] for k in FIELDS}
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for k in sorted(existing):  # (symbol, date) 均可字典序
            writer.writerow(existing[k])
    tmp.rename(path)


def main() -> int:
    params = {}
    if os.environ.get("FMDATA_PARAMS"):
        try:
            params = json.loads(os.environ["FMDATA_PARAMS"]) or {}
        except json.JSONDecodeError:
            print("FMDATA_PARAMS not valid JSON, ignoring", file=sys.stderr)
    tickers = [t.upper() for t in params.get("tickers", DEFAULT_TICKERS)]
    if params.get("tickers"):
        print(f"FMDATA_PARAMS tickers override: {len(tickers)} names")

    STORE.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    failed: list[str] = []
    for i, ticker in enumerate(tickers, 1):
        try:
            payload = fetch_history(ticker)
        except RuntimeError as exc:
            print(f"[{i}/{len(tickers)}] {ticker} FAIL: {exc}", file=sys.stderr)
            failed.append(ticker)
            continue
        fetched_at = str(payload.get("timestamp", ""))
        rows = parse_rows(payload, ticker, fetched_at)
        all_rows.extend(rows)
        print(f"[{i}/{len(tickers)}] {ticker}: rows={len(rows)} "
              f"first={rows[0]['date'] if rows else '-'} "
              f"last={rows[-1]['date'] if rows else '-'} ts={fetched_at}")
        time.sleep(1.0)  # cdn-api 走 Cloudflare, 放缓节奏 (2026-11-21 迁移教训)

    if not all_rows:
        print("ERROR: zero rows fetched", file=sys.stderr)
        return 1
    upsert_cumulative(all_rows, STORE / "cboe_historical_summary.csv")
    print(f"cumulative: {STORE / 'cboe_historical_summary.csv'} "
          f"(+{len(all_rows)} this run) tickers_ok={len(tickers) - len(failed)}/{len(tickers)}")
    if failed:
        print(f"failed tickers: {', '.join(failed)}", file=sys.stderr)
        if len(failed) / len(tickers) > 0.2:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
