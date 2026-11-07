#!/usr/bin/env bash
# Alpha Vantage 美股盈利轮转 cron 包装: group=$1, 成功写日志, 失败飞书告警. 2026-11-07
set -u
KEY="fmd_admin_7e3a9c1b5d48f0a2e8c4d6f1b3a7e9c0"
g="${1:-0}"
exec 9>/tmp/av-earnings-cron.lock
flock -n 9 || exit 0
resp=$(curl -s -X POST http://127.0.0.1:1934/fetch/av_us_earnings \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d "{\"params\":{\"group\":$g}}")
echo "$(date '+%F %T') group=$g ${resp:0:120}" >> /tmp/fmdata-av-earnings-cron.log
if ! echo "$resp" | grep -q '"status":"ok"'; then
  ~/.openclaw/scripts/send_feishu_alert.sh "av_us_earnings group=$g 刷新失败" "$resp" warning
  exit 1
fi
