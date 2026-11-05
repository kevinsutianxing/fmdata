#!/bin/bash
# SW V1.62 v2 链月度 catch-up（2026-11-05 立，补齐生产管道缺口）
# 作用: chip 7 列 + raw 41 列月末采样自动刷新，幂等（已有月按 (date,ts_code) 跳过）
# 挂 cron: 37 7 4 * *（每月 4 日；旧链 backfill_sw_factors 在 7 日 07:23，互不冲突）
# 详见 docs/sw-mcp/RUNBOOK.md V1.62 节
set -u
export TZ=Asia/Shanghai   # 🔴 CC/cron 时区坑：Period.now 按本地时区取月，必须钉死 CST

LOG="$HOME/fmdata/logs/sw_v2_monthly_refresh.log"
ALERT="$HOME/.openclaw/scripts/send_feishu_alert.sh"
echo "===== $(date '+%F %T') sw_v2_monthly_refresh start =====" >> "$LOG"

cd "$HOME/fmdata/scripts" || { echo "cd fail" >> "$LOG"; exit 1; }

fail=0
run_pull() {
  local name="$1" script="$2"
  if timeout 7200 python3 "$script" >> "$LOG" 2>&1; then
    echo "[$(date '+%T')] $name OK" >> "$LOG"
  else
    local rc=$?
    echo "[$(date '+%T')] $name FAIL rc=$rc" >> "$LOG"
    "$ALERT" "SW v2月度刷新失败" "${name} 退出码 ${rc}，日志 tail: $(tail -3 "$LOG" | tr '\n' ' ')" >> /dev/null 2>&1 || true
    fail=1
  fi
}

run_pull chip_monthend_v2 sw_chip_full.py
run_pull raw_monthend_v2  sw_raw_monthend_pull.py

# Parquet 快取副本再生成（正本=CSV 不变; 逐块校验含在内, ~7s）
if timeout 600 python3 /data/fmdata/scripts/convert_monthly_to_parquet.py >> "$LOG" 2>&1; then
  echo "[$(date '+%T')] parquet 副本再生成 OK" >> "$LOG"
else
  echo "[$(date '+%T')] parquet 副本再生成 FAIL" >> "$LOG"
  "$ALERT" "SW v2刷新parquet副本失败" "$(tail -3 "$LOG" | tr '\n' ' ')" >> /dev/null 2>&1 || true
  fail=1
fi

# 落盘验证：最新完成月两表都有数据、列数齐一
if python3 sw_v2_verify.py >> "$LOG" 2>&1; then
  echo "[$(date '+%T')] verify PASS" >> "$LOG"
else
  echo "[$(date '+%T')] verify FAIL" >> "$LOG"
  "$ALERT" "SW v2月度刷新验证失败" "$(tail -6 "$LOG" | tr '\n' ' ')" >> /dev/null 2>&1 || true
  fail=1
fi

echo "===== $(date '+%F %T') done (fail=$fail) =====" >> "$LOG"
exit $fail
