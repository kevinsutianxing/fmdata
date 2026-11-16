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
FAIL=0
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
  [ "$status" = "ok" ] || FAIL=$((FAIL+1))
done

if [ "$FAIL" -gt 0 ]; then
  echo "WARNING: $FAIL/$({ echo $DATASETS | wc -w; }) news dataset(s) failed"
  exit 1
fi
echo "news refresh done: all ok"
