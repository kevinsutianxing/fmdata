#!/usr/bin/env python3
"""数据契约影子断言 (2026-11-12 立, 影子模式=只写日志不改任何管线).

每个数据集声明 预期节奏+日历, 本脚本比对 expected vs actual 写一行日志;
影子期(≥3天)只观测不进快照不阻断, 误报清零后才考虑接网页横幅.
交易日参考 = 本地 limit_list 日期序列(纯本地, 零外部调用).

契约表(随数据集增改):
  recap_local    每交易日(T-0)      expected=最新交易日
  industry_flow  东吴T+1            expected>=次新交易日
  etf_flow       东吴T+1            expected>=次新交易日
  pattern_map    SW D+1             expected>=次新交易日
  soochow_window 东吴Excel滚动窗     本地最新期 vs 最新交易日 gap<=6 (防窗口流失)
cron: 45 19 * * 1-5 (pack 19:35 构建后)
"""
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

CST = timezone(timedelta(hours=8))
ROOT = Path("/data/fmdata/store/data")
PACK = ROOT / "soochow/cn_board_pack.json"
LOG = Path("/home/ubuntu/fmdata/logs/data_contract_shadow.log")
GAP_ALERT_DAYS = 6

logging.basicConfig(
    filename=LOG, level=logging.INFO,
    format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def trade_dates():
    lim = pd.read_csv(ROOT / "market/ashare_limit_list.csv", dtype=str)
    lim = lim[lim["ts_code"].notna() & (lim["ts_code"] != "")]
    return sorted({d for d in lim["trade_date"].dropna() if d})


def fmt(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else str(d)


def main():
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        pack = json.loads(PACK.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logging.error("pack 不可读, 断言跳过: %s", e)
        return
    blocks = pack.get("blocks") or {}
    tds = trade_dates()
    if not tds:
        logging.error("limit_list 无有效交易日, 断言跳过")
        return
    latest, prev = tds[-1], (tds[-2] if len(tds) > 1 else tds[-1])

    checks = [
        ("recap_local", (blocks.get("recap_local") or {}).get("as_of"), latest, "T-0"),
        ("industry_flow", (blocks.get("industry_flow") or {}).get("as_of"), prev, "T+1"),
        ("etf_flow", (blocks.get("etf_flow") or {}).get("as_of"), prev, "T+1"),
        ("pattern_map", (blocks.get("pattern_map") or {}).get("data_date"), prev, "D+1"),
    ]
    lines = []
    for key, actual, expected, cadence in checks:
        if not actual:
            lines.append(f"{key} status=MISSING expected>={fmt(expected)}")
            continue
        status = "FRESH" if str(actual) >= fmt(expected) else "STALE"
        lines.append(f"{key} cadence={cadence} actual={actual} expected>={fmt(expected)} status={status}")
    # 东吴滚动窗流失: 本地最新期 vs 最新交易日(交易日序列本身就是天数参照)
    try:
        etf = pd.read_csv(ROOT / "soochow/soochow_etf_flow.csv", dtype=str)
        local_latest = max(d for d in etf["data_date"].dropna())
        gap = len([d for d in tds if d > local_latest.replace("-", "")])
        lines.append(f"soochow_window local_latest={local_latest} gap={gap}d threshold={GAP_ALERT_DAYS} status={'LOSS_RISK' if gap > GAP_ALERT_DAYS else 'OK'}")
        if gap > GAP_ALERT_DAYS:
            subprocess.run(["/home/ubuntu/.openclaw/scripts/send_feishu_alert.sh",
                            "东吴滚动窗流失风险",
                            f"本地最新期 {local_latest} 落后最新交易日 {fmt(latest)} 已 {gap} 个交易日(阈值{GAP_ALERT_DAYS}), 超过10日将永久丢历史, 请检查 fetch_soochow_mcp.py"],
                           timeout=30, capture_output=True)
    except Exception as e:  # noqa: BLE001 - 断言自身故障不外溢
        lines.append(f"soochow_window check_error={type(e).__name__}")
    logging.info("shadow run %s | %s", now, " | ".join(lines))


if __name__ == "__main__":
    main()
