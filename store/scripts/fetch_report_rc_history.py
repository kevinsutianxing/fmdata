#!/usr/bin/env python3
"""Backfill Tushare report_rc into an append-safe PIT analyst forecast history.

The output preserves every sell-side report row with report_date as the information date.
It is intended for quantitative revision/breadth signals; no consensus snapshot is
backfilled or fabricated.  The official API caps each response at 3000 rows, therefore
this script paginates each date window until exhaustion.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd
import tushare as ts

FIELDS = [
    "ts_code",
    "name",
    "report_date",
    "report_title",
    "report_type",
    "classify",
    "org_name",
    "author_name",
    "quarter",
    "op_rt",
    "op_pr",
    "tp",
    "np",
    "eps",
    "pe",
    "rd",
    "roe",
    "ev_ebitda",
    "rating",
    "max_price",
    "min_price",
    "imp_dg",
    "create_time",
]


def _windows(start: pd.Timestamp, end: pd.Timestamp, days: int):
    cursor = start
    while cursor <= end:
        right = min(cursor + pd.Timedelta(days=days - 1), end)
        yield cursor, right
        cursor = right + pd.Timedelta(days=1)


def _fetch_window(pro, start: pd.Timestamp, end: pd.Timestamp, pause: float) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    offset = 0
    limit = 3000
    while True:
        frame = pro.report_rc(
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields=",".join(FIELDS),
            limit=limit,
            offset=offset,
        )
        if frame is None or frame.empty:
            break
        parts.append(frame)
        if len(frame) < limit:
            break
        offset += limit
        time.sleep(pause)
    if not parts:
        return pd.DataFrame(columns=FIELDS)
    return pd.concat(parts, ignore_index=True)


def _dedupe_key(frame: pd.DataFrame) -> list[str]:
    preferred = [
        "ts_code",
        "report_date",
        "org_name",
        "author_name",
        "quarter",
        "report_title",
    ]
    return [col for col in preferred if col in frame.columns]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20100101")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y%m%d"))
    parser.add_argument(
        "--output",
        default=str(Path.home() / "fmdata/store/fundamentals/report_rc_history.csv"),
    )
    parser.add_argument("--window-days", type=int, default=90)
    parser.add_argument("--pause", type=float, default=0.10)
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required")
    start = pd.to_datetime(args.start, format="%Y%m%d")
    end = pd.to_datetime(args.end, format="%Y%m%d")
    if start > end:
        raise SystemExit("start must be <= end")

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(output, dtype={"ts_code": str}) if output.exists() else pd.DataFrame()

    pro = ts.pro_api(token)
    fetched: list[pd.DataFrame] = []
    for left, right in _windows(start, end, args.window_days):
        frame = _fetch_window(pro, left, right, args.pause)
        if not frame.empty:
            fetched.append(frame)
        print(f"report_rc {left.date()} -> {right.date()}: {len(frame)} rows")
        time.sleep(args.pause)

    if not fetched and existing.empty:
        raise SystemExit("report_rc returned no rows")
    new = pd.concat(fetched, ignore_index=True) if fetched else pd.DataFrame(columns=FIELDS)
    combined = pd.concat([existing, new], ignore_index=True, sort=False)
    if "report_date" in combined:
        combined["report_date"] = pd.to_datetime(combined["report_date"], errors="coerce")
    key = _dedupe_key(combined)
    if key:
        combined = combined.drop_duplicates(key, keep="last")
    sort_cols = [col for col in ["report_date", "ts_code", "org_name", "quarter"] if col in combined]
    if sort_cols:
        combined = combined.sort_values(sort_cols)
    if "report_date" in combined:
        combined["report_date"] = combined["report_date"].dt.strftime("%Y%m%d")

    temp = output.with_suffix(output.suffix + ".tmp")
    combined.to_csv(temp, index=False)
    temp.replace(output)
    print(f"saved {len(combined)} PIT report rows -> {output}")


if __name__ == "__main__":
    main()
