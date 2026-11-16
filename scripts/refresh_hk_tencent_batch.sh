#!/bin/bash
# HK 腾讯直连批量(00700/02800/09618) 日刷新 — HK43 akshare 直连腾讯, 零 tushare 配额。
# 🔴【已退役 2026-11-09】统一由 fetch_hk_daily_incremental.py(ifzq+westock)接管,cron 已摘除。
# 本脚本保留仅作参考/应急;重启前先看 docs/data-sources-inventory.md 急救卡。
# 维护 overseas/tencent_hk_daily_batch.csv (tushare 同构, GVRM sync 消费)。
# 2026-11-07 建: 补齐 GVRM 页面缺口(02800/09618 全史 + 00700 摆脱 tushare hk_daily 配额)。
# 无 tushare 兜底: hk_daily 是"单独开权限"接口(实测 ~5次/天), 兜底无意义; 失败即 exit 1 由 cron 告警。
set -u
KEY="fmd_admin_7e3a9c1b5d48f0a2e8c4d6f1b3a7e9c0"
AK_CSV="$HOME/fmdata/store/overseas/tencent_hk_daily_batch_ak.csv"
DST_CSV="$HOME/fmdata/store/overseas/tencent_hk_daily_batch.csv"
TMP=$(mktemp)

if ! curl -s -X POST -H "X-API-Key: $KEY" --max-time 280 http://127.0.0.1:1934/fetch/tencent_hk_daily_batch_ak | grep -q '"status": *"ok"'; then
  echo "$(date '+%F %T') batch ak fetch FAILED"
  rm -f "$TMP"; exit 1
fi

python3 - "$AK_CSV" "$TMP" <<'PY' || { rm -f "$TMP"; exit 1; }
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], encoding='utf-8')))
cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
        'pre_close', 'change', 'pct_chg', 'vol', 'amount']
with open(sys.argv[2], 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    n = 0
    for code in ('00700.HK', '02800.HK', '09618.HK'):
        sub = [r for r in rows if r['code'] == code]
        if len(sub) < 100:
            sys.exit(f"suspiciously few rows for {code}: {len(sub)}")
        for r in reversed(sub):
            close = float(r['收盘']); chg = float(r['涨跌额'] or 0)
            w.writerow({'ts_code': code, 'trade_date': r['日期'].replace('-', ''),
                        'open': r['开盘'], 'high': r['最高'], 'low': r['最低'],
                        'close': r['收盘'], 'pre_close': round(close - chg, 3),
                        'change': r['涨跌额'], 'pct_chg': r['涨跌幅'],
                        'vol': r['成交量'], 'amount': r['成交额']})
            n += 1
print(f"transformed {n} rows")
PY

mv "$TMP" "$DST_CSV"
echo "$(date '+%F %T') batch refreshed, $(wc -l < "$DST_CSV") lines"
