#!/bin/bash
# fetch_us_equity_kline cron 包装器 — 2026-11-13 降噪; 2026-11-16 +gfclaw 备选链
# 背景: 主脚本 exit 2 = 部分标的失败(增量+尾窗自校验设计, 单标的失败次日/下场自动补洞),
#       但 cron `||` 把它当全失败告警 → 代理偶发日(如 11-13 晨 6 只失败)误报。
# 策略(v3): rc=2 先立即重跑一次(增量只补缺失标的,新代理池); 重跑成功吞告警;
#       重跑仍 rc=2 → 从输出抓 FAILED_SYMBOLS 交 gfclaw 备选脚本填充
#       (fetch_us_equity_kline_fallback.py, 收益重定基桥接口径, 2026-11-16 全池实测中位2.4bp);
#       fallback 有实际填充(appended>0)或全部 noop → 吞告警; fallback 也失败 → 透传告警。
# cron(06:15/15:25): flock -n /tmp/fetch_us_equity_kline.lock bash 本脚本 >> 日志 || 飞书告警
# 测试钩子: FUEK_CMD 可覆盖为 stub(勿用于生产)。
export TZ=Asia/Shanghai
FUEK_CMD=${FUEK_CMD:-python3 /home/ubuntu/fmdata/scripts/fetch_us_equity_kline_westock.py}
FALLBACK_CMD="python3 /home/ubuntu/fmdata/scripts/fetch_us_equity_kline_fallback.py"
LASTLOG=/tmp/fuek_last_run.log

$FUEK_CMD 2>&1 | tee "$LASTLOG"; rc=${PIPESTATUS[0]}
if [ "$rc" -eq 2 ]; then
    echo "[$(date '+%F %T')] [cron-wrap] rc=2 部分标的失败, 5s 后重跑一次补洞(增量设计只补缺失)"
    sleep 5
    $FUEK_CMD 2>&1 | tee "$LASTLOG"; rc=${PIPESTATUS[0]}
    if [ "$rc" -eq 0 ]; then
        echo "[$(date '+%F %T')] [cron-wrap] 重跑补洞成功, 不告警"
        exit 0
    fi
    if [ "$rc" -eq 2 ]; then
        SYMS=$(grep -o 'FAILED_SYMBOLS:.*' "$LASTLOG" | head -1 | cut -d: -f2 | tr -d ' ')
        if [ -n "$SYMS" ]; then
            echo "[$(date '+%F %T')] [cron-wrap] EM 仍失败标的[$SYMS] → gfclaw 备选接手"
            if $FALLBACK_CMD --symbols "$SYMS"; then
                echo "[$(date '+%F %T')] [cron-wrap] gfclaw 备选处理完成, 吞告警(明细见上; 最新棒T+1可能仍缺属预期)"
                exit 0
            fi
            echo "[$(date '+%F %T')] [cron-wrap] gfclaw 备选也失败, 透传告警"
        fi
    fi
fi
exit "$rc"
