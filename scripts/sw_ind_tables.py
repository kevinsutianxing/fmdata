#!/usr/bin/env python3
"""SW 新因子库行业表全量拉取: IND_ROT(行业轮动4列) + IND_FIN(行业财务48列) + IND_EST(行业一致预期8列) + IND_EOD(行业日行情5列)。

用法: setsid nohup python3 sw_ind_tables.py >> ~/fmdata/logs/sw_ind_tables.log 2>&1 &
策略: 月度/季度表全范围单发(<5000行); 日频表半年分块(~3800行/块)。5000行超限自动再对半降级。
追加式落盘 + 断点续跑(按已有 TRADE_DATE 覆盖跳过该块)。
"""
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _SwMcpClient, SW_MCP_URL, SW_MCP_CODE

OUTDIR = STORE_DIR / "industry"
SLEEP = 0.3

TABLES = [
    ("CN_ASHARE_INDUSTRY/CN_ASHARE_IND_ROT", "sw_ind_rot.csv", "20170101", "20261103", False),
    ("CN_ASHARE_INDUSTRY/CN_ASHARE_IND_FIN", "sw_ind_fin.csv", "20140101", "20261103", False),
    ("CN_ASHARE_INDUSTRY/CN_ASHARE_IND_EST", "sw_ind_est.csv", "20090101", "20261103", True),
    ("CN_ASHARE_INDUSTRY/CN_ASHARE_IND_EOD", "sw_ind_eod.csv", "20090101", "20261103", True),
]


def log(msg):
    print(f"[{datetime.now().strftime('%F %T')}] {msg}", flush=True)


def halfyear_periods(start, end):
    s = pd.Period(f"{start[:4]}-{start[4:6]}", freq="M")
    e = pd.Period(f"{end[:4]}-{end[4:6]}", freq="M")
    out, cur = [], s
    while cur <= e:
        nxt = min(cur + 5, e)
        out.append((cur.start_time.strftime("%Y%m%d"), nxt.end_time.strftime("%Y%m%d")))
        cur = nxt + 1
    return out


def fetch(cli, name, start, end, codes=None):
    """单块拉取, 5000 行超限且可分割时自动对半降级(按日期中点)。"""
    try:
        d = cli.call("factorlib_query_factor",
                     {"name": name, "start": start, "end": end, "codes": codes}, 120)
        rows = d.get("data", [])
        return rows, None
    except Exception as e:
        msg = str(e)
        if ("5000" in msg or "too large" in msg.lower()) and codes is None:
            from datetime import date, timedelta
            sd = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
            ed = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
            mid_d = sd + (ed - sd) // 2
            if mid_d <= sd:
                return [], msg
            m1, e1 = start, mid_d.strftime("%Y%m%d")
            s2 = (mid_d + timedelta(days=1)).strftime("%Y%m%d")
            r1, err1 = fetch(cli, name, m1, e1)
            r2, err2 = fetch(cli, name, s2, end)
            return r1 + r2, err1 or err2
        return [], msg


def pull_table(cli, name, fname, start, end, chunked):
    out = OUTDIR / fname
    OUTDIR.mkdir(parents=True, exist_ok=True)
    have = set()
    if out.exists():
        try:
            for chunk in pd.read_csv(out, usecols=["TRADE_DATE"], chunksize=200000):
                have |= set(chunk["TRADE_DATE"].astype(str))
        except Exception:
            pass
    ranges = halfyear_periods(start, end) if chunked else [(start, end)]
    total_new = 0
    for a, b in ranges:
        rows, err = fetch(cli, name, a, b)
        if err:
            log(f"WARN {fname} {a}-{b}: {err[:120]}")
            continue
        if not rows:
            continue
        df = pd.DataFrame(rows)
        df["TRADE_DATE"] = df["TRADE_DATE"].astype(str)
        df = df[~df["TRADE_DATE"].isin(have)]
        if df.empty:
            continue
        header = not out.exists()
        df.to_csv(out, mode="a", header=header, index=False)
        have |= set(df["TRADE_DATE"])
        total_new += len(df)
        log(f"{fname} {a}-{b}: +{len(df)} 行")
        time.sleep(SLEEP)
    log(f"{fname}: 本轮 +{total_new} 行 完成")
    return total_new


def main():
    cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
    for name, fname, start, end, chunked in TABLES:
        log(f"=== {name} -> {fname} ===")
        pull_table(cli, name, fname, start, end, chunked)
    log("IND TABLES DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
