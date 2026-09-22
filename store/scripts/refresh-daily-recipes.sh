#!/usr/bin/env bash
# 通用 daily recipe 刷新器
# 遍历所有 update_freq: daily 的 recipe，逐个 POST /fetch/<name>
# 起因：52 个 daily recipe 只有 3 个有专用 cron，其余全 stale（2026-07-07 半年报漏 34 只标的教训）
# cron: 每日 04:00 CST（盘前，06:00 overseas 之前先刷 fundamentals）
set -u
KEY="fmd_admin_7e3a9c1b5d48f0a2e8c4d6f1b3a7e9c0"
FMDATA="http://127.0.0.1:1934"
RECIPES="${HOME}/fmdata/store/recipes"
LOG="/tmp/fmdata-refresh-daily.log"
ts() { date '+%Y-%m-%d %H:%M:%S'; }

# 收集所有 update_freq: daily 的 recipe（排除 _ak/_bak/_alt 备份）
mapfile -t DATASETS < <(grep -lE '^update_freq:\s*daily' "${RECIPES}"/*.yaml 2>/dev/null \
  | xargs -I{} basename {} .yaml 2>/dev/null \
  | grep -vE '_(ak|bak|alt)$' | sort -u)

echo "[$(ts)] === daily recipe refresh START (${#DATASETS[@]} datasets) ===" >> "$LOG"
ok=0; fail=0; failed=""
for ds in "${DATASETS[@]}"; do
  # 限频：tushare 类数据集间隔 2 秒，避免撞 1次/分钟限额
  resp=$(curl -s -X POST -H "X-API-Key: $KEY" --max-time 120 "${FMDATA}/fetch/${ds}" 2>&1)
  status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "parse_fail")
  if [ "$status" = "ok" ]; then
    ok=$((ok+1))
  else
    fail=$((fail+1))
    failed="${failed} ${ds}"
    echo "[$(ts)] FAIL $ds: $(echo "$resp" | head -c 100)" >> "$LOG"
  fi
  sleep 2
done
echo "[$(ts)] === daily refresh DONE: ok=$ok fail=$fail ===" >> "$LOG"
[ $fail -gt 0 ] && echo "[$(ts)] failed:${failed}" >> "$LOG"
