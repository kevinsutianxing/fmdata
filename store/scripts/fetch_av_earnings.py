#!/usr/bin/env python3
"""Alpha Vantage 美股盈利(EARNINGS)批量拉取 — 长表 symbol/period/fiscal/reported/eps_est/eps_act/surprise/surprise_pct.

配额纪律: 免费档 25 req/day 全局共享, 本脚本每 symbol 1 次调用取全史, 之后仅增量意义在最新季.
30 只核心股分 3 组轮转(cron 日/二/四 各刷一组), 单次 10 调用, 调用间隔 13s 避 5 次/分限频.
遇到配额/权限信息(Information/Note 字段)立即停, 不烧配额重试.
FMDATA_PARAMS 可传 {"group":0|1|2} 或 {"symbols":["AAPL",...]}(覆盖组, 用于小批试点).
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

API_KEY = None
for line in open(os.path.expanduser("~/fmdata/.env")):
    if line.startswith("AV_API_KEY="):
        API_KEY = line.strip().split("=", 1)[1]
if not API_KEY:
    sys.exit("AV_API_KEY missing in ~/fmdata/.env")

# 与 cboe_iv_surface 同一 30 只核心美股(排序稳定保证组划分不变)
SYMBOLS = sorted([
    "AAPL","AMAT","AMD","AMZN","ANET","ASML","AVGO","CDNS","CEG","CRDO",
    "CRM","DDOG","ETN","GOOGL","KLAC","LRCX","META","MRVL","MSFT","MU",
    "NOW","NVDA","ORCL","PLTR","QCOM","SNPS","TSLA","TSM","VRT","VST",
])
GROUPS = [SYMBOLS[i::3] for i in range(3)]  # 各10只, 词法序稳定

OUT = os.path.expanduser("~/fmdata/store/market/av_us_earnings.csv")
COLS = ["symbol","period_type","fiscal_date_end","reported_date",
        "eps_estimated","eps_reported","surprise","surprise_pct","fetched_at"]

def params_override():
    p = os.environ.get("FMDATA_PARAMS", "")
    if not p:
        return {}
    try:
        return json.loads(p)
    except Exception:
        return {}

def fetch(sym):
    url = (f"https://www.alphavantage.co/query?function=EARNINGS&symbol={sym}&apikey={API_KEY}")
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.load(r)
    if not isinstance(d, dict) or "quarterlyEarnings" not in d:
        msg = str(d)[:160]
        if "Information" in d or "Note" in d:
            raise SystemExit(f"QUOTA/PREMATURE-STOP {sym}: {msg}")
        raise RuntimeError(f"bad shape {sym}: {msg}")
    return d

def rows_of(sym, d):
    out = []
    for ptype, key in (("annual", "annualEarnings"), ("quarterly", "quarterlyEarnings")):
        for it in d.get(key, []):
            out.append({
                "symbol": sym, "period_type": ptype,
                "fiscal_date_end": it.get("fiscalDateEnding") or it.get("fiscalDateEnd") or "",
                "reported_date": it.get("reportedDate") or "",
                "eps_estimated": it.get("estimatedEPS", ""),
                "eps_reported": it.get("reportedEPS", ""),
                "surprise": it.get("surprise", ""),
                "surprise_pct": it.get("surprisePercentage", ""),
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
    return out

def main():
    o = params_override()
    if o.get("symbols"):
        syms = [s.upper() for s in o["symbols"]]
    else:
        g = o.get("group", sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0)
        syms = GROUPS[int(g) % 3]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{now}] av_us_earnings: {len(syms)} symbols: {syms}", flush=True)

    existing = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                k = (r["symbol"], r["period_type"], r["fiscal_date_end"], r["reported_date"])
                existing[k] = r

    ok, fail = 0, 0
    for i, s in enumerate(syms):
        if i:
            time.sleep(13)
        try:
            for r in rows_of(s, fetch(s)):
                existing[(r["symbol"], r["period_type"], r["fiscal_date_end"], r["reported_date"])] = r
            ok += 1
            print(f"  ok {s}", flush=True)
        except SystemExit:
            raise
        except Exception as e:
            fail += 1
            print(f"  FAIL {s}: {e}", flush=True)

    tmp = OUT + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for k in sorted(existing, key=lambda x: (x[0], x[1], x[2], x[3])):
            w.writerow(existing[k])
    os.replace(tmp, OUT)
    print(f"done ok={ok} fail={fail} total_rows={len(existing)}", flush=True)
    sys.exit(1 if ok == 0 else 0)

if __name__ == "__main__":
    main()
