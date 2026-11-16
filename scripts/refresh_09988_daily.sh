#!/bin/bash
# 09988.HK daily refresh — Tencent(akshare via HK43, zero tushare quota) first,
# 🔴【已退役 2026-11-09】统一由 fetch_hk_daily_incremental.py(ifzq+westock)接管,cron 已摘除。
# 本脚本保留仅作参考/应急;重启前先看 docs/data-sources-inventory.md 急救卡。
# tushare hk_daily fallback. Maintains overseas/tencent_hk_daily_09988.csv in the
# tushare schema GVRM sync consumes. 2026-11-07: created after the quota-free
# backfill (1709 rows) made the pure-tushare 07:10 refresh a single point of
# quota failure. Idempotent; atomic write; logs to /tmp/fmdata-09988-refresh.log.
set -u
KEY="fmd_admin_7e3a9c1b5d48f0a2e8c4d6f1b3a7e9c0"
AK_CSV="$HOME/fmdata/store/overseas/tencent_hk_daily_09988_ak.csv"
DST_CSV="$HOME/fmdata/store/overseas/tencent_hk_daily_09988.csv"
TMP=$(mktemp)

ak_refresh() {
  curl -s -X POST -H "X-API-Key: $KEY" --max-time 280 http://127.0.0.1:1934/fetch/tencent_hk_daily_09988_ak | grep -q '"status": *"ok"'
}

ak_transform() {
  python3 - "$AK_CSV" "$TMP" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding='utf-8')))
if len(rows) < 100:
    sys.exit(f"suspiciously few rows: {len(rows)}")
cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
        'pre_close', 'change', 'pct_chg', 'vol', 'amount']
with open(sys.argv[2], 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in reversed(rows):
        close = float(r['收盘']); chg = float(r['涨跌额'] or 0)
        w.writerow({'ts_code': '09988.HK', 'trade_date': r['日期'].replace('-', ''),
                    'open': r['开盘'], 'high': r['最高'], 'low': r['最低'],
                    'close': r['收盘'], 'pre_close': round(close - chg, 3),
                    'change': r['涨跌额'], 'pct_chg': r['涨跌幅'],
                    'vol': r['成交量'], 'amount': r['成交额']})
print(f"transformed {len(rows)} rows")
PY
}

tushare_fallback() {
  curl -s -X POST -H "X-API-Key: $KEY" --max-time 300 http://127.0.0.1:1934/fetch/tencent_hk_daily_09988 | grep -q '"status": *"ok"'
}

if ak_refresh && ak_transform; then
  mv "$TMP" "$DST_CSV"
  echo "$(date '+%F %T') 09988 refreshed via Tencent(ak), $(wc -l < "$DST_CSV") lines"
else
  rm -f "$TMP"
  if tushare_fallback; then
    echo "$(date '+%F %T') 09988 refreshed via tushare fallback"
  else
    echo "$(date '+%F %T') 09988 refresh FAILED on both paths"
    exit 1
  fi
fi
