#!/bin/bash
# sw_pull_babysitter.sh — OOM 自愈保姆: 拉取进程死→自动续跑(断点零损失); DONE→跑 screen→退出
PLOG=~/fmdata/logs/sw_raw_monthend_pull.log
SLOG=~/fmdata/logs/sw_v2_screen.log
BLOG=~/fmdata/logs/sw_babysitter.log
echo "[$(date '+%F %T')] babysitter up" >> "$BLOG"
while true; do
  if grep -q "RAW MONTHEND DONE" "$PLOG" 2>/dev/null; then
    break
  fi
  if ! pgrep -f "^python3 /home/ubuntu/fmdata/scripts/sw_raw_monthend_pull" >/dev/null; then
    echo "[$(date '+%F %T')] pull dead -> relaunch" >> "$BLOG"
    python3 /home/ubuntu/fmdata/scripts/sw_raw_monthend_pull.py >> "$PLOG" 2>&1
    echo "[$(date '+%F %T')] pull exited rc=$?" >> "$BLOG"
  else
    sleep 120
  fi
done
echo "[$(date '+%F %T')] pull DONE -> screen" >> "$BLOG"
python3 /home/ubuntu/fmdata/scripts/sw_v2_screen.py >> "$SLOG" 2>&1
echo "[$(date '+%F %T')] SCREEN DONE" >> "$BLOG"
