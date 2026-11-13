#!/bin/bash
# fetch_us_equity_kline cron 包装器 — 2026-11-13 降噪
# 背景: 主脚本 exit 2 = 部分标的失败(增量+尾窗自校验设计, 单标的失败次日/下场自动补洞),
# 但 cron `||` 把它当全失败告警 → 代理偶发日(如 11-13 晨 6 只失败)误报。
# 策略: rc=2 先立即重跑一次(增量只补缺失标的,新代理池);重跑成功则吞掉告警;
#       重跑仍 rc=2 或 rc=1/崩溃 → 原样透传 exit code 交给 cron 告警。
# cron(06:15/15:25): flock -n /tmp/fetch_us_equity_kline.lock bash 本脚本 >> 日志 || 飞书告警
# 测试钩子: FUEK_CMD 可覆盖为 stub(勿用于生产)。
export TZ=Asia/Shanghai
FUEK_CMD=${FUEK_CMD:-python3 /home/ubuntu/fmdata/scripts/fetch_us_equity_kline.py}

$FUEK_CMD; rc=$?
if [ "$rc" -eq 2 ]; then
    echo "[$(date '+%F %T')] [cron-wrap] rc=2 部分标的失败, 5s 后重跑一次补洞(增量设计只补缺失)"
    sleep 5
    $FUEK_CMD; rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "[$(date '+%F %T')] [cron-wrap] 重跑补洞成功, 不告警"
        exit 0
    fi
    echo "[$(date '+%F %T')] [cron-wrap] 重跑仍失败 rc=$rc, 透传告警"
fi
exit "$rc"
