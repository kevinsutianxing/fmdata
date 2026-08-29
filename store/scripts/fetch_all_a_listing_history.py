#!/usr/bin/env python3
"""Refresh all-A-share list/delist intervals for PIT universe eligibility.

The file is reference metadata. Historical eligibility uses only the effective interval
``list_date <= as_of < delist_date``; current ``list_status`` is never backfilled as a
historical state.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
import tushare as ts

FIELDS = "ts_code,name,exchange,market,list_status,list_date,delist_date"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(Path.home() / "fmdata/store/reference/all_a_listing.csv"),
    )
    args = parser.parse_args()

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise SystemExit("TUSHARE_TOKEN is required")

    pro = ts.pro_api(token)
    parts: list[pd.DataFrame] = []
    for status in ("L", "D", "P"):
        frame = pro.stock_basic(exchange="", list_status=status, fields=FIELDS)
        if frame is not None and not frame.empty:
            parts.append(frame)
            print(f"stock_basic status={status}: {len(frame)} rows")
    if not parts:
        raise SystemExit("stock_basic returned no rows")

    out = pd.concat(parts, ignore_index=True, sort=False)
    out["ts_code"] = out["ts_code"].astype(str)
    out = out.drop_duplicates("ts_code", keep="first").sort_values("ts_code")
    for column in ("list_date", "delist_date"):
        out[column] = (
            out[column]
            .where(out[column].notna(), "")
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
        )

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    out.to_csv(temporary, index=False)
    temporary.replace(output)
    print(
        {
            "rows": int(len(out)),
            "listed_now": int((out["list_status"] == "L").sum()),
            "delisted": int((out["list_status"] == "D").sum()),
            "prelisted": int((out["list_status"] == "P").sum()),
            "output": str(output),
        }
    )


if __name__ == "__main__":
    main()
