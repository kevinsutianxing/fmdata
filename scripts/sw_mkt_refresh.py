#!/usr/bin/env python3
"""sw_futures_daily + sw_index_daily 月度增量刷新 (cron 每月 9 日 07:37 CST)。

走 fetch_dataset(=POST /fetch 同一代码路径), 增量=各序列库内最后日期-14天重叠。
任一非 ok 即 exit 1 → cron 触发飞书告警。0.15s/call 限速在 _fetch_sw_mcp 内建。
"""
import json
import sys
from datetime import datetime

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.recipe_fetcher import fetch_dataset


def main() -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    failed = []
    for name in ["sw_futures_daily", "sw_index_daily"]:
        try:
            r = fetch_dataset(name)
        except Exception as e:
            r = {"status": "exception", "message": str(e)[:200]}
        print(f"[{now}] {name}: {json.dumps(r, ensure_ascii=False)[:400]}", flush=True)
        if r.get("status") != "ok":
            failed.append(name)
    if failed:
        print(f"[{now}] FAILED: {failed}", flush=True)
        return 1
    print(f"[{now}] OK both", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
