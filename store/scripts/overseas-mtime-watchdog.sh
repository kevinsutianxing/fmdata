#!/usr/bin/env bash
# overseas-mtime-watchdog.sh — 监控 fmdata 海外数据 CSV 的 mtime,防止 fetch 静默失败长期潜伏。
# 起因:2026-06-21 发现 fred_cli.py 在 HK43 缺失致 VIX/原油等海外数据刷新 exit 2 静默失败 3 周,
# 缓存停在 5-28/5-30 用户无感知。本脚本检查所有「带 recipe 的 overseas dataset」CSV mtime,
# 超 STALE_DAYS 天未更新则飞书告警。
# 2026-11-13: 阈值按 recipe 的 update_freq 分档 — weekly→8天 / monthly→32天 / 其余→STALE_DAYS(3)。
# 起因: cboe_iv_surface(周度快照)被一刀切 3 天阈值连误报,周更数据集每周必炸 3-4 次。
# cron: 每日 09:00 盘后查一次(避开盘前刷新窗口)
set -u
STALE_DAYS="${STALE_DAYS:-3}"
STORE="${HOME}/fmdata/store"
RECIPES="${STORE}/recipes"
OVERSEAS="${STORE}/overseas"
ALERT_SCRIPT="${ALERT_SCRIPT:-${HOME}/.openclaw/scripts/send_feishu_alert.sh}"
LOG="/tmp/fmdata-overseas-watchdog.log"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
now_epoch=$(date +%s)

# 按更新频度取告警阈值(天): 周期+1天容错; STALE_DAYS 更大时以 STALE_DAYS 为准(测试可整体收紧)
stale_limit_days() {
  case "$1" in
    weekly)  [ "$STALE_DAYS" -gt 8 ]  && echo "$STALE_DAYS" || echo 8 ;;
    monthly) [ "$STALE_DAYS" -gt 32 ] && echo "$STALE_DAYS" || echo 32 ;;
    *)       echo "$STALE_DAYS" ;;
  esac
}

# 收集「带 recipe」的 overseas dataset 名(recipe 存在 = 该数据集应定期刷新)
# 排除备份/旁路源(如 tencent_hk_daily_ak 是 tencent_hk_daily 的 akshare 备份,主数据新鲜即不报)
mapfile -t WATCHED < <(grep -lE '^category:\s*overseas' "${RECIPES}"/*.yaml 2>/dev/null \
  | xargs -I{} basename {} .yaml 2>/dev/null \
  | grep -vE '_(ak|bak|alt)$' \
  | sort -u)

[ ${#WATCHED[@]} -eq 0 ] && { echo "[$(ts)] no overseas recipes found, skip"; exit 0; }

STALE_LIST=""
for ds in "${WATCHED[@]}"; do
  # 2026-11-07: recipe 的 file: 字段是权威路径(fred_risk_series 落 market/), 旧默认 overseas/ 兼容
  rel=$(grep -m1 '^file:' "${RECIPES}/${ds}.yaml" 2>/dev/null | sed -e 's/^file:[[:space:]]*//' -e 's/^"//' -e 's/"[[:space:]]*$//' -e "s/^'//" -e "s/'[[:space:]]*$//")
  csv="${STORE}/${rel:-overseas/${ds}.csv}"
  [ -f "$csv" ] || { STALE_LIST="${STALE_LIST}${ds}(无CSV文件)\n"; continue; }
  freq=$(grep -m1 '^update_freq:' "${RECIPES}/${ds}.yaml" 2>/dev/null | tr -d '"'"'" | sed -e 's/^update_freq:[[:space:]]*//' -e 's/[[:space:]]*$//')
  limit=$(stale_limit_days "${freq:-daily}")
  mtime_epoch=$(stat -c %Y "$csv")
  age_days=$(( (now_epoch - mtime_epoch) / 86400 ))
  if [ "$mtime_epoch" -lt $((now_epoch - limit * 86400)) ]; then
    STALE_LIST="${STALE_LIST}${ds}(${age_days}天未更新,频度:${freq:-daily})\n"
  fi
done

if [ -n "$STALE_LIST" ]; then
  msg="以下海外数据集超 ${STALE_DAYS} 天未刷新(可能 fetch 静默失败):\n${STALE_LIST}\n排查:curl -X POST -H 'X-API-Key:<adminkey>' http://127.0.0.1:1934/fetch/<name> 看 status;查 ~/fmdata/fmdata.log 的 exit code;确认 HK43 远端脚本存在(如 fred_cli.py/news_cli.py)"
  echo "[$(ts)] STALE detected:\n${STALE_LIST}" >> "$LOG"
  if [ -x "$ALERT_SCRIPT" ]; then
    bash "$ALERT_SCRIPT" "fmdata海外数据陈旧" "$(echo -e "$msg")" "warning"
  fi
  exit 1
fi

echo "[$(ts)] all ${#WATCHED[@]} overseas datasets fresh (<${STALE_DAYS}d)" >> "$LOG"
exit 0
