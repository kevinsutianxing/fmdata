#!/usr/bin/env bash
# FMP 三件 cron 包装: $1=dataset $2=group(仅轮转型用), 失败飞书告警. 2026-11-07
set -u
KEY="fmd_admin_7e3a9c1b5d48f0a2e8c4d6f1b3a7e9c0"
ds="$1"; g="${2:-0}"
exec 9>/tmp/fmp-cron.lock
flock -n 9 || exit 0
body='{}'; [ "$ds" != "fmp_earnings_calendar" ] && body="{\"params\":{\"group\":$g}}"
resp=$(curl -s -X POST "http://127.0.0.1:1934/fetch/$ds" -H "Content-Type: application/json" -H "X-API-Key: $KEY" -d "$body")
echo "$(date '+%F %T') $ds g=$g ${resp:0:120}" >> /tmp/fmdata-fmp-cron.log
if ! echo "$resp" | grep -q '"status":"ok"'; then
  ~/.openclaw/scripts/send_feishu_alert.sh "$ds group=$g 刷新失败" "$resp" warning
  exit 1
fi
