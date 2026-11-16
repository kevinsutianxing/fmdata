#!/usr/bin/env python3
"""CFFEX IO/MO ATM IV(Black-76, 平价远期) → store/market/cffex_atm_iv.csv

数据源: tushare opt_daily(trade_date, exchange='CFFEX') — A股积分档接口(2000积分
可用, 与 hk_daily 的"单独开权限"不同), 每交易日 1 次调用返回当日全部合约(含结算价)。
行权价/到期月内嵌于合约代码(IO2611-C-3550.CFX), 到期日=到期月第三个周五。

方法(每个 产品 × 到期月 × 交易日, 产品∈{IO→000300.SH, MO→000852.SH}):
  1. ATM 行权价 K = 最接近标的指数收盘价 S 的 Call 行权价, 该档 Put 须同时 settle>0
  2. 隐含远期 F = K + (C - P)·e^{rT}  ← put-call 平价, 规避股息率/持有成本假设
     (实测 CFFEX 结算价跟随期货远期而非现货: 深度实值 Call 结算价可低于现货内在价值)
  3. Black-76 反推 Call 的 IV(二分法)。平价构造的 F 下 Put 的隐含波动率与 Call 恒等
     (C-P = e^{-rT}(F-K) 对任意 σ 成立), 再反推 Put 是循环论证 — 只用 Call, Put 仅参与定 F
  4. delta=0.5(ATM 桶标签, GVRM iv_structure 以 |Δ-0.5|≤0.1 归 ATM)
     tenor_days = 距到期自然日; iv_type=settlement_derived_iv
  r = macro/shibor.csv 银行报价 1m/3m 中间价按日均值(T≤45d 用 1m, 否则 3m), 前向填充
守门: |F/S-1|>10% 丢弃; |K/S-1|>5% 丢弃(季月宽档距防翼部冒充 ATM); settle≤0/NaN 丢弃; 距到期<2 丢弃; 取最近 4 档。
用途: GVRM CFFEX_IO / CFFEX_MO 的期权 IV 期限结构。是结算价推导值, 非交易所官方波动率指数。
用法: python3 fetch_cffex_atm_iv.py [--days 90]   # 断点续跑: 已有 CSV 从最后日期增量
"""
import argparse
import csv
import math
import sys
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR  # noqa: E402
from fmdata.fetcher import TushareFetcher  # noqa: E402

OUT = STORE_DIR / "market/cffex_atm_iv.csv"
PRODUCTS = {"IO": "000300.SH", "MO": "000852.SH"}
IV_TYPE = "settlement_derived_iv"
COLS = ["date", "symbol", "market", "iv", "expiry", "tenor_days", "delta",
        "iv_type", "provider", "underlying_close", "quality"]


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def third_friday(year, month):
    first = date(year, month, 1)
    return date(year, month, 1 + (4 - first.weekday()) % 7 + 14)


def expiry_of(yymm):
    yy, mm = int(yymm[:2]), int(yymm[2:])
    year = 2000 + yy if yy <= 80 else 1900 + yy
    return third_friday(year, mm)


def b76_call(forward, strike, years, rate, sigma):
    """Black-76 欧式 Call(期货/远价期权) 价格。"""
    if sigma <= 0 or years <= 0:
        return max(0.0, math.exp(-rate * years) * (forward - strike))
    srt = sigma * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * years) / srt
    d2 = d1 - srt
    return math.exp(-rate * years) * (forward * norm_cdf(d1) - strike * norm_cdf(d2))


def implied_sigma_b76(price, forward, strike, years, rate):
    """二分反推 Black-76 隐含波动率; 括号失败(价格越过无漂移上下界)返回 None。"""
    lo, hi = 0.005, 3.0
    intrinsic = math.exp(-rate * years) * max(0.0, forward - strike)
    if price <= intrinsic * 1.0001 or price >= forward * math.exp(-rate * years) * 0.9999:
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if b76_call(forward, strike, years, rate, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def load_shibor():
    """date(YYYYMMDD) -> {1m,3m} 年化小数(多银行报价取中间价均值)。"""
    rates = {}
    path = STORE_DIR / "macro/shibor.csv"
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = str(r["date"]).replace("-", "")
            try:
                m1 = (float(r["1m_b"]) + float(r["1m_a"])) / 200.0
                m3 = (float(r["3m_b"]) + float(r["3m_a"])) / 200.0
            except (ValueError, KeyError, TypeError):
                continue
            acc = rates.setdefault(d, [[], []])
            acc[0].append(m1)
            acc[1].append(m3)
    out = {}
    for d, (a, b) in rates.items():
        if a and b:
            out[d] = (sum(a) / len(a), sum(b) / len(b))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="首次回填的自然日窗口")
    args = ap.parse_args()

    ts = TushareFetcher()
    index_close = {}
    for ts_code in PRODUCTS.values():
        year = datetime.now().year
        df = ts._call("index_daily", None, ts_code=ts_code,
                      start_date=f"{year}0101", end_date=f"{year}1231")
        if df is None or df.empty:
            raise RuntimeError(f"index_daily {ts_code} empty")
        index_close[ts_code] = {str(r.trade_date): float(r.close) for r in df.itertuples()}
        print(f"index {ts_code}: {len(index_close[ts_code])} days")
    shibor = load_shibor()
    shibor_days = sorted(shibor)

    def rate_for(d, days):
        prior = [x for x in shibor_days if x <= d]
        if not prior:
            return None
        m1, m3 = shibor[prior[-1]]
        return m1 if days <= 45 else m3

    existing = {}
    if OUT.exists():
        with open(OUT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[(r["date"], r["symbol"], int(r["tenor_days"]))] = r
        print(f"existing rows: {len(existing)}")

    all_days = sorted(index_close["000300.SH"])
    today = datetime.now().date()
    if existing:
        start = max(r["date"] for r in existing.values())
        targets = [d for d in all_days if d > start and d <= today.strftime("%Y%m%d")]
    else:
        cutoff = (today - timedelta(days=args.days)).strftime("%Y%m%d")
        targets = [d for d in all_days if d >= cutoff]
    print(f"days to fetch: {len(targets)}")

    fetched = 0
    for d in targets:
        opt = ts._call("opt_daily", None, trade_date=d, exchange="CFFEX")
        fetched += 1
        time.sleep(0.35)
        if opt is None or opt.empty:
            print(f"{d}: no option data (holiday?) skipped")
            continue
        for product, underlying in PRODUCTS.items():
            s = index_close.get(underlying, {}).get(d)
            if not s:
                continue
            # (yymm, K) -> {'C': settle, 'P': settle}
            board = {}
            for row in opt.itertuples():
                code = str(row.ts_code)
                if not code.startswith(product):
                    continue
                try:
                    head, side, k = code.split("-")
                    k = float(k.split(".")[0])
                except ValueError:
                    continue
                settle = getattr(row, "settle", None)
                if settle is None or not math.isfinite(float(settle)) or float(settle) <= 0:
                    continue
                board.setdefault((head[2:], k), {})[side] = float(settle)
            # 按到期日聚合档位, 取距到期最近 4 档
            by_expiry = {}
            for (yymm, k), quotes in board.items():
                try:
                    exp = expiry_of(yymm)
                except ValueError:
                    continue
                days = (exp - datetime.strptime(d, "%Y%m%d").date()).days
                if days < 2:
                    continue
                by_expiry.setdefault((days, exp), []).append((k, quotes))
            for (days, exp), strikes in sorted(by_expiry.items())[:4]:
                atm_k, _ = min(strikes, key=lambda kv: abs(kv[0] - s))
                quotes = next(q for k, q in strikes if k == atm_k)
                c, p = quotes.get("C"), quotes.get("P")
                if not c or not p:
                    continue
                r = rate_for(d, days)
                if r is None:
                    continue
                T = days / 365.0
                forward = atm_k + (c - p) * math.exp(r * T)
                if abs(forward / s - 1) > 0.10:
                    continue
                if abs(atm_k / s - 1) > 0.05:
                    continue  # 季月宽档距下最近档也可能是翼部, 不冒充 ATM
                iv = implied_sigma_b76(c, forward, atm_k, T, r)
                if iv is None:
                    continue
                key = (d, f"CFFEX_{product}", days)
                existing[key] = {
                    "date": d, "symbol": f"CFFEX_{product}", "market": "A_SHARE",
                    "iv": f"{iv:.6f}", "expiry": exp.strftime("%Y%m%d"),
                    "tenor_days": str(days), "delta": "0.5", "iv_type": IV_TYPE,
                    "provider": "fmdata", "underlying_close": f"{s:.4f}",
                    "quality": "bs76_atm_cffex_settle"}

    rows = sorted(existing.values(), key=lambda r: (r["date"], r["symbol"], int(r["tenor_days"])))
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(OUT)
    print(f"[done] {OUT} rows={len(rows)} (opt_daily calls: {fetched})")


if __name__ == "__main__":
    main()
