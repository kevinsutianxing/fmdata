#!/usr/bin/env python3
"""Build announcement-date PIT fundamentals for convertible-bond underlyings.

The source is Tushare ``fina_indicator`` and is accessed only from the fmdata
governance layer.  Each record is made investable no earlier than ``ann_date``.
The script retains a revision-risk flag because a current vendor pull cannot
prove that a subsequently revised historical number equals the originally
published value.  Consumers must preserve that flag in research audit.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

FMDATA_ROOT = Path("/home/ubuntu/fmdata")
sys.path.insert(0, str(FMDATA_ROOT))
from fmdata.config import STORE_DIR  # noqa: E402
from fmdata.fetcher import TushareFetcher  # noqa: E402
from fmdata.registry import register_dataset, scan_csv  # noqa: E402


def _stock_codes(fetcher: TushareFetcher) -> list[str]:
    basic = fetcher._call("cb_basic", None)
    if basic is None or basic.empty or "stk_code" not in basic:
        raise RuntimeError("cb_basic has no underlying-stock mapping")
    codes = (basic["stk_code"].dropna().astype(str).str.upper().str.strip())
    codes = codes[codes.str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
    return sorted(codes.unique().tolist())


def _normalise(raw: pd.DataFrame, stock_code: str, start: str, end: str) -> pd.DataFrame:
    columns = [
        "stock_code", "report_period", "effective_date", "available_date",
        "roe_pct", "roe_dt_pct", "roic_pct", "debt_to_assets_pct",
        "gross_margin_pct", "netprofit_yoy_pct", "revenue_yoy_pct",
        "source", "source_is_pit", "revision_safe",
    ]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=columns)
    frame = raw.copy()
    frame["effective_date"] = pd.to_datetime(frame.get("ann_date"), format="%Y%m%d", errors="coerce")
    frame["report_period"] = pd.to_datetime(frame.get("end_date"), format="%Y%m%d", errors="coerce")
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    frame = frame[(frame["effective_date"] >= start_ts) & (frame["effective_date"] <= end_ts)].copy()
    frame = frame[frame["effective_date"] >= frame["report_period"]].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    quality_cols = [c for c in ["roe", "roe_dt", "roic", "debt_to_assets", "gross_margin", "netprofit_yoy", "tr_yoy"] if c in frame]
    frame["_quality"] = frame[quality_cols].notna().sum(axis=1) if quality_cols else 0
    frame = (frame.sort_values(["effective_date", "report_period", "_quality"])
             .drop_duplicates(["effective_date", "report_period"], keep="last"))
    def metric(name: str, low: float, high: float) -> pd.Series:
        value = pd.to_numeric(frame.get(name), errors="coerce")
        return value.where(value.between(low, high))

    out = pd.DataFrame({
        "stock_code": stock_code.split(".")[0],
        "report_period": frame["report_period"],
        "effective_date": frame["effective_date"],
        "available_date": frame["effective_date"],
        "roe_pct": metric("roe", -100.0, 100.0),
        "roe_dt_pct": metric("roe_dt", -100.0, 100.0),
        "roic_pct": metric("roic", -100.0, 100.0),
        "debt_to_assets_pct": metric("debt_to_assets", 0.0, 200.0),
        "gross_margin_pct": metric("gross_margin", -100.0, 100.0),
        "netprofit_yoy_pct": metric("netprofit_yoy", -1000.0, 1000.0),
        "revenue_yoy_pct": metric("tr_yoy", -1000.0, 1000.0),
        "source": "tushare_fina_indicator",
        "source_is_pit": True,
        "revision_safe": False,
    })
    return out[columns].dropna(subset=["effective_date", "report_period"])


def build(start: str, end: str, limit: int | None = None) -> pd.DataFrame:
    fetcher = TushareFetcher()
    api_start = pd.Timestamp(start).strftime("%Y%m%d")
    api_end = pd.Timestamp(end).strftime("%Y%m%d")
    codes = _stock_codes(fetcher)
    if limit is not None:
        codes = codes[:limit]
    frames: list[pd.DataFrame] = []
    for index, code in enumerate(codes, start=1):
        try:
            raw = fetcher._call("fina_indicator", None, ts_code=code, start_date=api_start, end_date=api_end)
            normalised = _normalise(raw, code, start, end)
            if not normalised.empty:
                frames.append(normalised)
        except Exception as exc:  # source failures are recorded, not fabricated
            print(f"[warn] {code}: {exc}")
        if index % 50 == 0:
            print(f"[progress] {index}/{len(codes)} stocks, rows={sum(len(x) for x in frames)}")
        time.sleep(0.10)
    if not frames:
        raise RuntimeError("fina_indicator returned no usable announced fundamentals")
    return (pd.concat(frames, ignore_index=True)
            .sort_values(["stock_code", "effective_date", "report_period"])
            .drop_duplicates(["stock_code", "effective_date", "report_period"], keep="last")
            .reset_index(drop=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CB-underlying announcement-date PIT fundamentals.")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    parser.add_argument("--limit", type=int, default=None, help="smoke-test only; do not use for production")
    args = parser.parse_args()
    data = build(args.start, args.end, args.limit)
    out = STORE_DIR / "market" / "stock_fundamentals_pit.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".csv.tmp")
    data.to_csv(temp, index=False)
    temp.replace(out)
    metadata = scan_csv(out, "available_date")
    metadata.update({
        "file": "market/stock_fundamentals_pit.csv",
        "category": "fundamentals",
        "description": "转债正股财务指标（Tushare fina_indicator，按公告日可见；含修订风险标记）",
    })
    register_dataset("stock_fundamentals_pit", metadata)
    print(f"rows={len(data)} stocks={data['stock_code'].nunique()} {data['available_date'].min().date()}..{data['available_date'].max().date()} output={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
