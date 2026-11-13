#!/usr/bin/env bash
# 刷新 3 个新闻源 recipe(market/macro/tech)进 fmdata。
# 每次消耗 NewsAPI 免费额度 3 次(每频道 1 次,cron 每天 1 轮 ≈ 3 次/天)。
# admin key 从 ~/fmdata/.env 读取,不进命令行/不进日志。
# 用法: bash refresh_news.sh   (cron 每天 08:33)
set -u
FMURL="http://127.0.0.1:1934"
ENV_FILE="${HOME}/fmdata/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found" >&2; exit 2
fi
AKEY=$(grep -E '^FMDATA_ADMIN_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)
if [ -z "$AKEY" ]; then
  echo "ERROR: FMDATA_ADMIN_KEY missing in $ENV_FILE" >&2; exit 2
fi

DATASETS="news_market news_macro news_tech"
FAIL=0    # 硬错误(error/parse_err/超时) — 真故障
EMPTY=0   # status=empty — NewsAPI 免费版 ~24h 延迟撞 --days 1 窗口的慢性现象
for ds in $DATASETS; do
  resp=$(curl -s --max-time 70 -X POST -H "X-API-Key: $AKEY" "${FMURL}/fetch/${ds}" 2>&1)
  status=$(echo "$resp" | python3 -c "import sys,json
try:
    print(json.load(sys.stdin).get('status','?'))
except Exception:
    print('parse_err')" 2>/dev/null)
  rows=$(echo "$resp" | python3 -c "import sys,json
try:
    print(json.load(sys.stdin).get('rows','-'))
except Exception:
    print('-')" 2>/dev/null)
  echo "[$ds] status=$status rows=$rows"
  if [ "$status" = "ok" ]; then
    :
  elif [ "$status" = "empty" ]; then
    EMPTY=$((EMPTY+1))
  else
    FAIL=$((FAIL+1))
  fi
done

# 2026-11-13: 历史 131 跑里 52 次至少 1 个 empty(40%),全部是免费版延迟、数据无真故障,
# 旧逻辑把它们当失败 exit1 → cron 天天误报。现在 empty 只记日志;
# 硬错误或 3/3 全空(真断供)才 exit1 触发告警。
if [ "$FAIL" -gt 0 ] || [ "$EMPTY" -eq 3 ]; then
  echo "WARNING: error=$FAIL empty=$EMPTY/3 — news refresh 告警级失败"
  exit 1
fi
[ "$EMPTY" -gt 0 ] && echo "NOTE: $EMPTY/3 news dataset(s) empty (NewsAPI 免费版~24h延迟,慢性非故障)"
echo "news refresh done: ok=$((3-FAIL-EMPTY)) empty=$EMPTY error=$FAIL"
