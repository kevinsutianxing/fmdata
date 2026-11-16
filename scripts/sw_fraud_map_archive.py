#!/usr/bin/env python3
"""SW MCP 全市场排雷地图(fraud_map)月度归档。

get_fraud_map 无参仅返最新月,无历史可回查 → 面板须本脚本自建归档。
归档即冻结: 键=返回的 data_date, 幂等(已存在跳过)。
🔴 响应 ~464KB 超 MCP/agent 上下文上限 —— 任何 agent 禁直调 get_fraud_map,
   只许经本脚本落盘后读文件。
cron: 每月 8 日 07:37 CST (crontab 2026-11-06 登记, 失败飞书告警)
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

ARCHIVE_DIR = Path("/home/ubuntu/fmdata/store/fundamentals/fraud_map_archive")


def main() -> int:
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    r = cli.call("get_fraud_map", {}, timeout=180)
    data_date = r.get("data_date")
    if not data_date:
        print(f"FAIL: 响应缺 data_date, 顶层键={sorted(r.keys())[:15]}", flush=True)
        return 1
    if not isinstance(data_date, str):
        data_date = str(data_date).replace("-", "")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE_DIR / f"fraud_map_{data_date}.json"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if out.exists():
        print(f"[{now}] SKIP idempotent: {out.name} 已存在(归档即冻结)", flush=True)
        return 0
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    tmp.rename(out)
    temp = r.get("temperature") or {}
    size_kb = out.stat().st_size / 1024
    print(f"[{now}] ARCHIVED {out.name}: {size_kb:.0f}KB generated_at={r.get('generated_at')} "
          f"temperature={json.dumps(temp, ensure_ascii=False)[:200]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
