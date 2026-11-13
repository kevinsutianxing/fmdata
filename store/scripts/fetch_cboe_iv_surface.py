#!/usr/bin/env python3
"""Cboe 延迟期权链快照 → 个股/ETF IV surface 数据集 (cboe_iv_surface).

数据源: https://cdn.cboe.com/api/global/delayed_quotes/options/<TICKER>.json
  - 免费无授权 (延迟报价, 15min 延迟档)
  - 🔴 延迟报价无历史 → 本脚本每次取数即冻结快照归档 (PIT 由归档构造)

产出 (STORE/market/cboe_iv_surface/):
  raw/<TICKER>_<YYYYMMDD>.json.gz   原始链归档 (observed_at 纪律的正本)
  summary_<YYYYMMDD>.csv            当次快照 surface
  iv_surface_summary.csv            累计长表 (recipe file, 追加+按 key 去重)

标的清单: global-volatility-risk-monitor 的 Cboe universe (2026-11-07 sync 报告):
  30 美股 + RUT + 6 ETF (SPY/QQQ/IWM/SOXQ/SMH/XLK); IV 指数 VIX/VXN/SKEW/VVIX 无链不含。
  经 FMDATA_PARAMS {"tickers":[...]} 可覆盖 (子集/扩列)。

聚合口径 (每标的每快照):
  到期桶 tenor_bucket ∈ {30,60,90,180,365} = 不重叠窗口 (0,30]/(30,60]/(60,90]/(90,180]/(180,365]
  天数, 桶内取最近到期日 (term structure 点)。
  delta 桶: |delta| ∈ {0.25,0.5,0.75} × {C,P}, 带 ±0.125 取 iv 均值;
  带内空 (深度虚值/流动性差) → 退化为带外最近单合约, iv_type 标 _nearest_fallback。
  iv 为 Cboe 报价侧计算的年化 IV (小数), 🔴 非 RV 冒充; 筛 iv>0 且 0.05≤|delta|≤0.95。

observed_at: Cboe payload timestamp 原样保留 (UTC, 快照生成时刻);
snapshot_date: 归属美股交易日 (UTC<12 点归前一天), 周六收盘后抓档=周五, 见 us_trade_date()。

退出码: 失败标的 >20% → exit 1 (cron 飞书告警); 单标的失败不影响其余归档。
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

STORE = Path("/home/ubuntu/fmdata/store/market/cboe_iv_surface")
BASE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
UA = "Mozilla/5.0 fmdata-cboe-iv-surface/1.0"
DEFAULT_TICKERS = [
    # 6 ETF
    "SPY", "QQQ", "IWM", "SOXQ", "SMH", "XLK",
    # 30 US stocks (monitor Cboe universe)
    "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD",
    "PLTR", "MU", "TSM", "AMAT", "LRCX", "KLAC", "ASML", "ANET", "CDNS",
    "CEG", "CRDO", "CRM", "DDOG", "ETN", "MRVL", "NOW", "ORCL", "QCOM",
    "SNPS", "VRT", "VST",
    # Russell 2000 指数期权
    "RUT",
]
TENOR_BUCKETS = [(0, 30), (30, 60), (60, 90), (90, 180), (180, 365)]
DELTA_TARGETS = [0.25, 0.50, 0.75]
DELTA_BAND = 0.125

FIELDS = [
    "snapshot_date", "observed_at", "ticker", "underlying_close", "iv30",
    "tenor_bucket", "tenor_days", "expiry", "option_type",
    "delta_target", "delta_mean", "n_contracts", "strike_min", "strike_max",
    "iv_mean", "iv_min", "iv_max", "open_interest_sum", "volume_sum",
    "iv_type", "unit", "source",
]


def fetch_chain(ticker: str, timeout: int = 60, retries: int = 2) -> dict:
    """抓单标的延迟链 JSON; 指数类 (RUT) 自动试 '_' 前缀。"""
    for sym in (ticker, f"_{ticker}" if not ticker.startswith("_") else ticker[1:]):
        last_err = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(BASE_URL.format(sym), headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                opts = payload.get("data", {}).get("options", [])
                if opts:
                    return payload
                last_err = f"empty options for {sym}"
            except Exception as exc:  # noqa: BLE001 - 网络错误逐标的隔离
                last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(2 * (attempt + 1))
        print(f"  [warn] {sym}: {last_err}", file=sys.stderr)
    raise RuntimeError(f"all variants failed for {ticker}: {last_err}")


def parse_option_symbol(sym: str):
    """AAPL270219P00035000 → (root, date(2027,2,19), 'P', 350.0)。自右向左: 8位行权/1000 + C/P + YYMMDD。"""
    strike = int(sym[-8:]) / 1000.0
    cp = sym[-9].upper()
    ymd = sym[-15:-9]
    expiry = dt.date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6]))
    return sym[:-15], expiry, cp, strike


def us_trade_date(observed_at: str) -> str:
    """observed_at (UTC) → 归属的美股交易日 YYYYMMDD: UTC<12 点归前一天
    (美股收盘后晚间抓档 22:xx UTC 保留当日; 深夜/次日凌晨 01:xx UTC 归前一交易日)。
    固定周六 06:07 CST cron 恒为收盘后, 手动补抓也基本覆盖; observed_at 原样保留为权威。"""
    d = dt.datetime.strptime(observed_at[:10], "%Y-%m-%d")
    if dt.time.fromisoformat(observed_at[11:19]) < dt.time(12, 0):
        d -= dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def aggregate(payload: dict, ticker: str, observed_at: str, snapshot_date: str) -> list[dict]:
    data = payload.get("data", {})
    close = data.get("close")
    # payload 顶层 iv30 为百分数点 (如 25.386 = 25.386%), 统一为 annualized_decimal
    iv30_raw = data.get("iv30")
    iv30 = round(iv30_raw / 100, 6) if isinstance(iv30_raw, (int, float)) else None
    rows = []
    by_expiry: dict[dt.date, list] = {}
    for o in data.get("options", []):
        try:
            root, expiry, cp, strike = parse_option_symbol(o["option"])
        except (ValueError, KeyError):
            continue
        iv = o.get("iv") or 0
        delta = o.get("delta")
        if iv <= 0 or delta is None:
            continue
        adelta = abs(delta)
        if adelta < 0.05 or adelta > 0.95:
            continue
        by_expiry.setdefault(expiry, []).append((cp, strike, iv, delta, adelta,
                                                o.get("open_interest") or 0,
                                                o.get("volume") or 0))
    today = dt.datetime.strptime(snapshot_date, "%Y%m%d").date()
    for lo, hi in TENOR_BUCKETS:
        # 桶内最近到期 = term structure 点
        expiries = sorted(e for e in by_expiry if lo < (e - today).days <= hi)
        if not expiries:
            continue
        expiry = expiries[0]
        tenor_days = (expiry - today).days
        for cp in ("C", "P"):
            pool = [c for c in by_expiry[expiry] if c[0] == cp]
            if not pool:
                continue
            for target in DELTA_TARGETS:
                band = [c for c in pool if abs(c[4] - target) <= DELTA_BAND]
                iv_type = "cboe_delayed_iv_bandmean"
                if band:
                    use = band
                else:  # 带内空 → 最近 |delta| 单合约退化
                    use = [min(pool, key=lambda c: abs(c[4] - target))]
                    iv_type += "_nearest_fallback"
                ivs = [c[2] for c in use]
                rows.append({
                    "snapshot_date": snapshot_date,
                    "observed_at": observed_at,
                    "ticker": ticker,
                    "underlying_close": close,
                    "iv30": iv30,
                    "tenor_bucket": hi,
                    "tenor_days": tenor_days,
                    "expiry": expiry.isoformat(),
                    "option_type": cp,
                    "delta_target": target,
                    "delta_mean": round(sum(c[3] for c in use) / len(use), 4),
                    "n_contracts": len(use),
                    "strike_min": min(c[1] for c in use),
                    "strike_max": max(c[1] for c in use),
                    "iv_mean": round(sum(ivs) / len(ivs), 6),
                    "iv_min": round(min(ivs), 6),
                    "iv_max": round(max(ivs), 6),
                    "open_interest_sum": sum(c[5] for c in use),
                    "volume_sum": sum(c[6] for c in use),
                    "iv_type": iv_type,
                    "unit": "annualized_decimal",
                    "source": "cboe_global_delayed_quotes",
                })
    return rows


def append_cumulative(rows: list[dict], path: Path) -> None:
    """追加累计长表, 按 (snapshot_date,ticker,tenor_bucket,option_type,delta_target) 去重 (幂等重跑)。"""
    key_cols = ("snapshot_date", "ticker", "tenor_bucket", "option_type", "delta_target")
    existing: dict[tuple, dict] = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                existing[tuple(r[k] for k in key_cols)] = r
    for r in rows:
        existing[tuple(str(r[k]) for k in key_cols)] = {k: r[k] for k in FIELDS}
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for k in sorted(existing, key=lambda t: (t[0], t[1], int(t[2]), t[3], float(t[4]))):
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

    raw_dir = STORE / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    failed: list[str] = []
    for i, ticker in enumerate(tickers, 1):
        try:
            payload = fetch_chain(ticker)
        except RuntimeError as exc:
            print(f"[{i}/{len(tickers)}] {ticker} FAIL: {exc}", file=sys.stderr)
            failed.append(ticker)
            continue
        observed_at = payload.get("timestamp", "")
        snapshot_date = us_trade_date(observed_at)
        raw_path = raw_dir / f"{ticker}_{snapshot_date}.json.gz"
        with gzip.open(raw_path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        rows = aggregate(payload, ticker, observed_at, snapshot_date)
        all_rows.extend(rows)
        n_exp = len({r["expiry"] for r in rows})
        print(f"[{i}/{len(tickers)}] {ticker}: close={payload['data'].get('close')} "
              f"iv30={payload['data'].get('iv30')} expiries_used={n_exp} rows={len(rows)} "
              f"raw={raw_path.name} ({raw_path.stat().st_size // 1024}KB)")
        time.sleep(0.6)

    if not all_rows:
        print("ERROR: zero rows aggregated", file=sys.stderr)
        return 1
    observed_dates = {r["snapshot_date"] for r in all_rows}
    for od in sorted(observed_dates):
        day_rows = [r for r in all_rows if r["snapshot_date"] == od]
        out = STORE / f"summary_{od}.csv"
        with open(out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(day_rows)
        print(f"summary: {out} rows={len(day_rows)}")
    append_cumulative(all_rows, STORE / "iv_surface_summary.csv")
    print(f"cumulative: {STORE / 'iv_surface_summary.csv'} "
          f"(+{len(all_rows)} this run) tickers_ok={len(tickers) - len(failed)}/{len(tickers)}")
    if failed:
        print(f"failed tickers: {', '.join(failed)}", file=sys.stderr)
        if len(failed) / len(tickers) > 0.2:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
