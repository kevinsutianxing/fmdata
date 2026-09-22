#!/usr/bin/env bash
# overseas-mtime-watchdog.sh — 监控 fmdata 海外数据 CSV 的 mtime,防止 fetch 静默失败长期潜伏。
# 起因:2026-06-21 发现 fred_cli.py 在 HK43 缺失致 VIX/原油等海外数据刷新 exit 2 静默失败 3 周,
# 缓存停在 5-28/5-30 用户无感知。本脚本检查所有「带 recipe 的 overseas dataset」CSV mtime,
# 超 STALE_DAYS 天未更新则飞书告警。
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
threshold=$((now_epoch - STALE_DAYS * 86400))

# 收集「带 recipe」的 overseas dataset 名(recipe 存在 = 该数据集应定期刷新)
# 排除备份/旁路源(如 tencent_hk_daily_ak 是 tencent_hk_daily 的 akshare 备份,主数据新鲜即不报)
mapfile -t WATCHED < <(grep -lE '^category:\s*overseas' "${RECIPES}"/*.yaml 2>/dev/null \
  | xargs -I{} basename {} .yaml 2>/dev/null \
  | grep -vE '_(ak|bak|alt)$' \
  | sort -u)

[ ${#WATCHED[@]} -eq 0 ] && { echo "[$(ts)] no overseas recipes found, skip"; exit 0; }

STALE_LIST=""
for ds in "${WATCHED[@]}"; do
  csv="${OVERSEAS}/${ds}.csv"
  [ -f "$csv" ] || { STALE_LIST="${STALE_LIST}${ds}(无CSV文件)\n"; continue; }
  mtime_epoch=$(stat -c %Y "$csv")
  age_days=$(( (now_epoch - mtime_epoch) / 86400 ))
  if [ "$mtime_epoch" -lt "$threshold" ]; then
    STALE_LIST="${STALE_LIST}${ds}(${age_days}天未更新)\n"
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
