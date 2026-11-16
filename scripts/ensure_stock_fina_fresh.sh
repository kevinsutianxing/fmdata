#!/bin/bash
# 月度自愈：检查最近两个已过披露窗口的报告期 fina_{period}.csv 是否缺失，缺则补拉。
# 背景（2026-09-29）：20260630 期漏拉导致 /market/fundamentals 停在 Q1，
# 根因=stock_fina recipe 无 cron 且 batch_by_period 字段无实现。
# 判定规则：报告期结束日 + 62 天视为披露窗口关闭（0331→0601, 0630→0831,
# 0930→1231, 1231→次年0303，均晚于法定披露截止，保守）。
set -u
cd "$(dirname "$0")/.."
LOG=logs/ensure_stock_fina_fresh.log
mkdir -p logs
exec >>"$LOG" 2>&1
echo "=== $(date -Is) ensure_stock_fina_fresh start ==="

python3 - <<'PY'
import datetime as dt
import subprocess
import sys
from pathlib import Path

store = Path("store/fundamentals/stock_fina")
today = dt.date.today()
QMONTH = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}  # 季度(1基) -> (月,末日)


def quarter_ends(n):
    """最近 n 个已过去的季度末，按近到远。"""
    ends = []
    y, q = today.year, (today.month - 1) // 3 + 1  # 当前所处季度(1基)
    while len(ends) < n:
        m, d = QMONTH[q]
        end = dt.date(y, m, d)
        if end <= today:
            ends.append(end)
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
    return ends


for end in quarter_ends(2):
    period = end.strftime("%Y%m%d")
    if (today - end).days < 62:
        print(f"{period}: disclosure window not closed yet, skip")
        continue
    f = store / f"fina_{period}.csv"
    if f.exists():
        print(f"{period}: file present ({f.stat().st_size} bytes)")
        continue
    print(f"{period}: MISSING, fetching...")
    r = subprocess.run([sys.executable, "scripts/fetch_stock_fina_period.py", period])
    print(f"{period}: fetch exit={r.returncode}")
PY
echo "=== $(date -Is) done ==="
