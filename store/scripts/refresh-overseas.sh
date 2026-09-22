#!/usr/bin/env bash
# 每日刷新所有 overseas recipe 数据集（VIX/原油/美债/美元指数/标普/腾讯港股/RSS）
# 起因：fmdata 无内部 recipe 调度器，update_freq:daily 只是元数据；
#       历史上只靠手动 fetch + 09:00 mtime-watchdog 告警，没人拉就 stale 3 天。
# cron: 每日 06:00 CST（盘前，09:00 watchdog 检查前先刷一遍）
set -u
KEY="fmd_admin_7e3a9c1b5d48f0a2e8c4d6f1b3a7e9c0"
FMDATA="http://127.0.0.1:1934"
RECIPES="${HOME}/fmdata/store/recipes"
LOG="/tmp/fmdata-refresh-overseas.log"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 收集所有 overseas recipe（排除 _ak/_bak/_alt 备份）
mapfile -t DATASETS < <(grep -lE '^category:\s*overseas' "${RECIPES}"/*.yaml 2>/dev/null \
  | xargs -I{} basename {} .yaml 2>/dev/null \
  | grep -vE '_(ak|bak|alt)$' | sort -u)

echo "[$(ts)] === overseas refresh START (${#DATASETS[@]} datasets) ===" >> "$LOG"
ok=0; fail=0; failed=""
for ds in "${DATASETS[@]}"; do
  resp=$(curl -s -X POST -H "X-API-Key: $KEY" --max-time 90 "${FMDATA}/fetch/${ds}" 2>&1)
  status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "parse_fail")
  if [ "$status" = "ok" ]; then
    ok=$((ok+1))
  else
    fail=$((fail+1))
    failed="${failed} ${ds}"
    echo "[$(ts)] FAIL $ds: $status" >> "$LOG"
  fi
done
echo "[$(ts)] === overseas refresh DONE: ok=$ok fail=$fail ===" >> "$LOG"
[ $fail -gt 0 ] && echo "[$(ts)] failed:${failed}" >> "$LOG"
