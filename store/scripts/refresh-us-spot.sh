#!/bin/bash
# us_stock_spot 独立刷新（20260929 摘出 06:00 批次——东财指纹识别对拥挤窗口敏感, 单跑+错峰后稳定）
export TZ=Asia/Shanghai
LOG=/tmp/fmdata-us-spot.log
echo "[$(date '+%F %T')] === us_stock_spot solo refresh START ===" >> "$LOG"
python3 /home/ubuntu/fmdata/store/scripts/fetch_us_stock_spot.py >> "$LOG" 2>&1
rc=$?
# 2026-11-13: 单日失败多为 QG 代理瞬态(近8天7成1败;fetch 失败时保留旧 CSV 自愈,且有 mtime
# 看门狗兜底) → 单次失败不再飞书(此前天天误报);连续3天才告警,之后每7天提醒一次防断供静默。
STREAK_FILE=/tmp/us-spot-fail-streak
if [ $rc -ne 0 ]; then
    streak=$(( $(cat "$STREAK_FILE" 2>/dev/null || echo 0) + 1 ))
    echo "$streak" > "$STREAK_FILE"
    if [ "$streak" -eq 3 ] || [ $((streak % 7)) -eq 0 ]; then
        ~/.openclaw/scripts/send_feishu_alert.sh "us_stock_spot 连续${streak}天刷新失败(保留旧数据)" "$(tail -5 $LOG)" warning
    fi
else
    rm -f "$STREAK_FILE"
fi
echo "[$(date '+%F %T')] === DONE rc=$rc ===" >> "$LOG"
