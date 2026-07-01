#!/usr/bin/env python3
"""
Fetch historical analyst ratings from cninfo via akshare.
Replaces tushare report_rc (rate-limited to 10/day at 2000 credits).

Uses QG proxy pool on SZ81. Runs one date per invocation.
Designed for cron: every 10 minutes.
"""
import akshare as ak
import pandas as pd
import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime

STORE = Path.home() / "fmdata" / "store"
HIST_DIR = STORE / "fundamentals" / "consensus_history"
HIST_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = HIST_DIR / "_progress_ak.json"

# Month-end trading dates (same as tushare version)
MONTH_ENDS = [
    "20210129","20210226","20210331","20210430","20210531","20210630",
    "20210730","20210831","20210930","20211029","20211130","20211231",
    "20220128","20220225","20220331","20220429","20220531","20220630",
    "20220729","20220831","20220930","20221031","20221130","20221230",
    "20230131","20230224","20230331","20230428","20230531","20230630",
    "20230731","20230831","20230928","20231031","20231130","20231229",
    "20240131","20240223","20240329","20240430","20240531","20240628",
    "20240731","20240830","20240930","20241031","20241129","20241231",
    "20250124","20250228","20250331","20250430","20250523",
]


# Dual QG proxy pool
_QG_POOLS = [
    {"key": os.environ.get("QG_PROXY_AUTHKEY", ""), "pwd": os.environ.get("QG_PROXY_AUTHPWD", "")},
    {"key": os.environ.get("QG_PROXY_AUTHKEY_2", ""), "pwd": os.environ.get("QG_PROXY_AUTHPWD_2", "")},
]


def _get_proxy():
    for pi, pool in enumerate(_QG_POOLS):
        key, pwd = pool["key"], pool["pwd"]
        if not key:
            continue
        try:
            resp = requests.get(
                f"https://share.proxy.qg.net/get?key={key}&num=1&format=json&distinct=true",
                timeout=10,
            )
            data = resp.json()
            if data.get("data"):
                server = data["data"][0]["server"]
                return f"http://{key}:{pwd}@{server}"
        except Exception as e:
            label = "primary" if pi == 0 else "fallback"
            print(f"  Proxy {label} fetch failed: {e}")
    return None


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": [], "current_idx": 0}


def save_progress(p):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(p, f, indent=2)


def fetch_one_date(date_str):
    proxy = _get_proxy()
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy

    try:
        df = ak.stock_rank_forecast_cninfo(date=date_str)
        if df is not None and len(df) > 0:
            df.to_csv(HIST_DIR / f"cninfo_ratings_{date_str}.csv", index=False)
            return len(df)
        return 0
    except Exception as e:
        print(f"  Error: {e}")
        return -1
    finally:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


if __name__ == "__main__":
    import sys

    if "--status" in sys.argv:
        prog = load_progress()
        n = len(prog["completed"])
        print(f"Progress: {n}/{len(MONTH_ENDS)} ({n*100//len(MONTH_ENDS)}%)")
        if prog["completed"]:
            print(f"Completed: {prog['completed'][:3]}...{prog['completed'][-3:]}")
        if prog["current_idx"] < len(MONTH_ENDS):
            print(f"Next: {MONTH_ENDS[prog['current_idx']]}")
        files = list(HIST_DIR.glob("cninfo_ratings_*.csv"))
        print(f"Files: {len(files)} cninfo rating files")
        sys.exit(0)

    prog = load_progress()
    idx = prog["current_idx"]

    if idx >= len(MONTH_ENDS):
        print(f"All {len(MONTH_ENDS)} dates done!")
        sys.exit(0)

    date_str = MONTH_ENDS[idx]
    if date_str in prog["completed"]:
        prog["current_idx"] += 1
        save_progress(prog)
        print(f"Skip {date_str} (done). Next: {MONTH_ENDS[prog['current_idx']]}")
        sys.exit(0)

    print(f"Fetching cninfo ratings for {date_str} ({idx+1}/{len(MONTH_ENDS)})...")
    n = fetch_one_date(date_str)

    if n >= 0:
        prog["completed"].append(date_str)
        prog["current_idx"] += 1
        save_progress(prog)
        print(f"{date_str}: {n} ratings. Progress: {len(prog['completed'])}/{len(MONTH_ENDS)}")

        if prog["current_idx"] >= len(MONTH_ENDS):
            print("All done!")
    else:
        print(f"{date_str}: failed, will retry next run")
