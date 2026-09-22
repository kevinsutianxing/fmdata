#!/usr/bin/env python3
"""Merge a manually supplied Wind index export into the fmdata CSV store.

Expected input columns: date, code, close; pre_close/pct_chg are optional.
The script is intentionally append/upsert-only on (code, date).
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pandas as pd


ALLOWED_CODES = {"885001.WI", "881001.WI"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--incoming", type=Path, required=True)
    ap.add_argument("--store", type=Path, required=True)
    args = ap.parse_args()

    incoming = pd.read_csv(args.incoming)
    required = {"date", "code", "close"}
    missing = required - set(incoming.columns)
    if missing:
        raise SystemExit(f"missing required columns: {sorted(missing)}")
    incoming["date"] = pd.to_datetime(incoming["date"], errors="coerce")
    incoming["code"] = incoming["code"].astype(str).str.strip()
    incoming["close"] = pd.to_numeric(incoming["close"], errors="coerce")
    incoming = incoming[incoming["code"].isin(ALLOWED_CODES)]
    incoming = incoming[incoming["date"].notna() & incoming["close"].notna() & (incoming["close"] > 0)].copy()
    if incoming.empty:
        raise SystemExit("no valid Wind rows after validation")

    incoming = incoming.sort_values(["code", "date"])
    if "pre_close" not in incoming:
        incoming["pre_close"] = incoming.groupby("code")["close"].shift(1)
    if "pct_chg" not in incoming:
        incoming["pct_chg"] = (incoming["close"] / incoming["pre_close"] - 1.0) * 100.0
    incoming["date"] = incoming["date"].dt.strftime("%Y-%m-%d")
    for c in ["open", "high", "low", "vol", "amount"]:
        if c not in incoming:
            incoming[c] = pd.NA
    incoming = incoming[["date", "code", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]]

    if args.store.exists():
        old = pd.read_csv(args.store)
        merged = pd.concat([old, incoming], ignore_index=True)
    else:
        merged = incoming
    merged = merged.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
    args.store.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.store.with_suffix(args.store.suffix + ".tmp")
    merged.to_csv(tmp, index=False)
    tmp.replace(args.store)
    print({"incoming_rows": len(incoming), "store_rows": len(merged), "date_min": merged.date.min(), "date_max": merged.date.max(), "codes": sorted(merged.code.unique()), "source_sha256": hashlib.sha256(args.incoming.read_bytes()).hexdigest()})


if __name__ == "__main__":
    main()
