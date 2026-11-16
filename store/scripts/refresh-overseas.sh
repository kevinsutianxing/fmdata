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
  | grep -vE '_(ak|bak|alt)$' | sort -u   | grep -vE '^us_stock_spot$' \
  | grep -vE '^cboe_iv_surface$' \
  | grep -vE '^us_equity_kline_em$' \
  | grep -vE '^moomoo_iv_history$')  # 20260929 摘出 us_stock_spot(东财指纹敏感改独立时段单跑); 20261107 摘出 cboe_iv_surface(weekly 已有周六06:07专用cron, 日批重复全量下载且周六与其并发读写竞争); 20261112 摘出 us_equity_kline_em(GVRM美股主源, 增量+尾窗自校验/除权日单标的重锚, 平常日~4-5min,失败面隔离, 专用cron 06:15/15:25 直跑脚本); 20261115 摘出 moomoo_iv_history(生产链在HK43+scp同步, SZ81 /fetch allowlist 拒绝, 治理跳过名单6735386已收而此处漏同步→每日假告警)

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
# 2026-11-13: 单源失败多为 QG 代理/FRED 瞬态(近12天5次单源挂、无一连续,次日自愈)
# → 失败项歇 20s 单次重试再定生死,避免单源瞬态触发整批"refresh-overseas 失败"告警
if [ "$fail" -gt 0 ]; then
  # 2026-11-15: 06:00 批 ~70s 内密集打满 QG 代理池, 瞬态抖动加剧(index_global_monitor_em 连3日 06 挂)
  # 单次 20s 重试不够(实测 06:15:33 失败→06:16:19 成功, 仅隔 46s) → 两轮 20s+45s
  for wait_s in 20 45; do
    [ "$fail" -eq 0 ] && break
    echo "[$(ts)] retry pass (wait ${wait_s}s):${failed}" >> "$LOG"
    still=""
    for ds in $failed; do
      sleep "$wait_s"
    resp=$(curl -s -X POST -H "X-API-Key: $KEY" --max-time 90 "${FMDATA}/fetch/${ds}" 2>&1)
    status=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "parse_fail")
    if [ "$status" = "ok" ]; then
      ok=$((ok+1)); fail=$((fail-1))
      echo "[$(ts)] RETRY-OK $ds" >> "$LOG"
    else
      still="${still} ${ds}"
      echo "[$(ts)] RETRY-FAIL $ds: $status" >> "$LOG"
    fi
    done
    failed="$still"
  done
fi
echo "[$(ts)] === overseas refresh DONE: ok=$ok fail=$fail ===" >> "$LOG"
# 2026-10-01 修退出码双向 bug:fail=0 时 && 短路返回 1 → 全成功误报;fail>0 时 echo 返回 0 → 真失败从不告警
if [ "$fail" -gt 0 ]; then
  echo "[$(ts)] failed:${failed}" >> "$LOG"
  exit 1
fi
exit 0
