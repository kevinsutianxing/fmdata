#!/usr/bin/env python3
"""FMP (Financial Modeling Prep) /stable API 拉取 — 三模式: calendar | estimates | statements.

用法: fetch_fmp.py calendar
      fetch_fmp.py estimates <symbols 逗号分隔>
      fetch_fmp.py statements <symbols 逗号分隔>
FMDATA_PARAMS JSON {"mode":..., "symbols":[...]} 可覆盖 argv.
免费档要点(2026-11-07 实测): v3/v4 全封; 指数/外汇/商品史/批量报价 402 premium;
  个股日线/报表约5年窗; 250次/天, 5次/秒. 调用间隔1.2s, 撞402/限频立即停.
key: ~/fmdata/.env FMP_API_KEY.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = "https://financialmodelingprep.com/stable"
KEY = None
for line in open(os.path.expanduser("~/fmdata/.env")):
    if line.startswith("FMP_API_KEY="):
        KEY = line.strip().split("=", 1)[1]
if not KEY:
    sys.exit("FMP_API_KEY missing")

STORE = os.path.expanduser("~/fmdata/store/market")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class QuotaStop(Exception):
    pass


def get(path, params):
    q = "&".join(f"{k}={v}" for k, v in params.items()) + f"&apikey={KEY}"
    url = f"{BASE}/{path}?{q}"
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode(errors="replace")
            if e.code == 402 or "not available under your current subscription" in body:
                raise QuotaStop(f"{path}: {body}")
            if attempt == 2:
                raise
            time.sleep(2)
        except json.JSONDecodeError:
            time.sleep(2)  # 偶发空体, 一次重试
    raise RuntimeError(f"exhausted {url}")


def load_csv(path, cols):
    rows = []
    if os.path.exists(path):
        with open(path) as f:
            for r in csv.DictReader(f):
                rows.append({k: r.get(k, "") for k in cols})
    return rows


def save_csv(path, cols, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def mode_calendar():
    """滚动窗 -7d..+21d, 键=(symbol,date) 新值覆盖旧值, 窗外历史行保留."""
    path = f"{STORE}/fmp_earnings_calendar.csv"
    cols = ["symbol", "date", "eps_estimated", "eps_actual", "revenue_estimated",
            "revenue_actual", "last_updated", "fetched_at"]
    frm = (date.today() - timedelta(days=7)).isoformat()
    to = (date.today() + timedelta(days=21)).isoformat()
    data = get("earnings-calendar", {"from": frm, "to": to})
    fresh = {(d["symbol"], d["date"]): {
        "symbol": d.get("symbol", ""), "date": d.get("date", ""),
        "eps_estimated": d.get("epsEstimated", ""), "eps_actual": d.get("epsActual", ""),
        "revenue_estimated": d.get("revenueEstimated", ""), "revenue_actual": d.get("revenueActual", ""),
        "last_updated": d.get("lastUpdated", ""), "fetched_at": NOW,
    } for d in (data or [])}
    kept = {((r["symbol"], r["date"])): r for r in load_csv(path, cols) if not (frm <= r["date"] <= to)}
    allr = list(kept.values()) + list(fresh.values())
    allr.sort(key=lambda r: (r["date"], r["symbol"]))
    save_csv(path, cols, allr)
    print(f"calendar window {frm}..{to} fresh={len(fresh)} total={len(allr)}")


def mode_estimates(symbols):
    """年频分析师预估(前瞻多财年), 宽表一行=一财年, 键=(symbol,date)."""
    path = f"{STORE}/fmp_analyst_estimates.csv"
    data = get("analyst-estimates", {"symbol": symbols[0], "period": "annual"})
    if not data:
        print("estimates: empty for first symbol"); return
    cols = ["symbol", "date", "fetched_at"] + sorted(
        k for k in data[0].keys() if k not in ("symbol", "date"))
    got = {}
    ok = fail = 0
    for i, s in enumerate(symbols):
        if i:
            time.sleep(1.2)
        try:
            rows = get("analyst-estimates", {"symbol": s, "period": "annual"}) or []
            for r in rows:
                r["fetched_at"] = NOW
                got[(s, r.get("date", ""))] = r
            ok += 1
        except QuotaStop:
            raise
        except Exception as e:
            fail += 1
            print(f"  FAIL {s}: {e}", file=sys.stderr)
    old = {(r["symbol"], r["date"]): r for r in load_csv(path, cols) if (r["symbol"], r["date"]) not in got}
    allr = list(old.values()) + list(got.values())
    allr.sort(key=lambda r: (r["symbol"], r["date"]))
    save_csv(path, cols, allr)
    print(f"estimates ok={ok} fail={fail} rows={len(allr)}")


def mode_statements(symbols):
    """季度三表 tidy 长表, 键=(statement,symbol,period_end,field), null 跳过."""
    path = f"{STORE}/fmp_us_statements.csv"
    cols = ["statement", "symbol", "period_end", "filing_date", "accepted_date",
            "fiscal_year", "period", "field", "value", "fetched_at"]
    stmts = [("income", "income-statement"), ("balance", "balance-sheet-statement"),
             ("cashflow", "cash-flow-statement")]
    got = {}
    ok = fail = 0
    for i, s in enumerate(symbols):
        for label, ep in stmts:
            if i or label != "income":
                time.sleep(1.2)
            try:
                rows = get(ep, {"symbol": s, "period": "quarter"}) or []
                for r in rows:
                    pe, fd, ad = r.get("date", ""), r.get("filingDate", ""), r.get("acceptedDate", "")
                    fy, pq = r.get("fiscalYear", ""), r.get("period", "")
                    for k, v in r.items():
                        if k in ("date", "filingDate", "acceptedDate", "fiscalYear", "period",
                                 "symbol", "reportedCurrency", "cik", "link", "finalLink"):
                            continue
                        if v is None or v == "":
                            continue
                        got[(label, s, pe, k)] = {
                            "statement": label, "symbol": s, "period_end": pe,
                            "filing_date": fd, "accepted_date": ad, "fiscal_year": fy,
                            "period": pq, "field": k, "value": v, "fetched_at": NOW,
                        }
                ok += 1
            except QuotaStop:
                raise
            except Exception as e:
                fail += 1
                print(f"  FAIL {s}/{label}: {e}", file=sys.stderr)
    syms = set(symbols)
    old = {}
    # 保留旧符号行(未被本次刷新覆盖)
    for r in load_csv(path, cols):
        k = (r["statement"], r["symbol"], r["period_end"], r["field"])
        if r["symbol"] not in syms and k not in got:
            old[k] = r
    allr = list(old.values()) + list(got.values())
    allr.sort(key=lambda r: (r["symbol"], r["statement"], r["period_end"], r["field"]))
    save_csv(path, cols, allr)
    print(f"statements ok={ok} fail={fail} rows={len(allr)}")


def main():
    o = json.loads(os.environ.get("FMDATA_PARAMS", "") or "{}")
    mode = o.get("mode") or (sys.argv[1] if len(sys.argv) > 1 else "")
    # 30 核心股 = av_us_earnings/cboe_iv_surface 同池; 3组词法序轮转
    symbols = sorted([
        "AAPL","AMAT","AMD","AMZN","ANET","ASML","AVGO","CDNS","CEG","CRDO",
        "CRM","DDOG","ETN","GOOGL","KLAC","LRCX","META","MRVL","MSFT","MU",
        "NOW","NVDA","ORCL","PLTR","QCOM","SNPS","TSLA","TSM","VRT","VST",
    ])
    groups = [symbols[i::3] for i in range(3)]
    if o.get("symbols"):
        syms = [s.upper() for s in o["symbols"]]
    elif len(sys.argv) > 2 and sys.argv[2].isdigit():
        syms = groups[int(sys.argv[2]) % 3]
    elif mode in ("estimates", "statements"):
        syms = groups[int(o.get("group", 0)) % 3]
    else:
        syms = []
    try:
        if mode == "calendar":
            mode_calendar()
        elif mode == "estimates":
            mode_estimates([s.upper() for s in syms])
        elif mode == "statements":
            mode_statements([s.upper() for s in syms])
        else:
            sys.exit(f"unknown mode {mode!r}")
    except QuotaStop as e:
        print(f"QUOTA-STOP {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
