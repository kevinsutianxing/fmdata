#!/usr/bin/env python3
"""Build the convertible-bond research panel served by fmdata as /data/cb_daily.

Sanctioned direct-tushare caller (CLAUDE.md exception: scripts under
~/fmdata/scripts/ are fmdata's data sources and may call tushare directly).
The engine's adapter (convertible-bond-strategy-engine/
src/cb_strategy/monthly/fmdata.py) pulls /data/{dataset} from fmdata at
127.0.0.1:1934; this script materialises the datasets that adapter expects.

Produces three datasets under ~/fmdata/store/market/:
  - cb_daily.csv         (MANDATORY: the assembled point-in-recent-time panel;
                          carries every REQUIRED_COLUMNS field of panel.py,
                          including stock_close merged in)
  - cb_terms_pit.csv     (current-snapshot terms, explicitly labelled as not
                          PIT until announcement-effective history is loaded)
  - cb_market_daily.csv  (official CSI 000832 daily return = benchmark)

MVP scope & known limits (documented, not hidden):
  - ACTIVE universe only (cb_daily returns listed bonds; delisted bonds need
    historical cb_basic snapshots tushare does not expose -> survivorship-lite).
  - Recent history window (default 6 months). Pass --start/--end or --months
    for backtests; full history = larger fetch.
  - rating: '' (tushare cb_basic carries no CB rating) -> source_status unverified.
  - redemption_price: 105.0 placeholder (real 赎回价 lives in prospectus /
    rate_clause; parsing deferred) -> source_status unverified.
  - ytm_pct: an approximation based on the available rate_clause. It has no
    verified maturity-redemption cashflow and is explicitly marked as such.
  - Terms are CURRENT snapshot (cb_basic), not true historical PIT. Acceptable
    for a research sandbox; call out before any live conclusion.

Usage:
  python3 ~/fmdata/scripts/build_cb_panel.py                # default 6 months
  python3 ~/fmdata/scripts/build_cb_panel.py --months 24    # 2 years
  python3 ~/fmdata/scripts/build_cb_panel.py --start 20240101 --end 20260717
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# fmdata internal fetcher (allowed: this script IS a fmdata data source).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fmdata.fetcher import TushareFetcher  # noqa: E402
from fmdata.config import STORE_DIR  # noqa: E402
from fmdata.cb_benchmark import build_market_daily  # official CSI 000832; never equal-weight

MARKET_DIR = STORE_DIR / "market"
TUSHARE_DATE_FMT = "%Y%m%d"
PAR = 100.0
DEFAULT_REDEMPTION = 105.0


def _ts(date: pd.Timestamp) -> str:
    return date.strftime(TUSHARE_DATE_FMT)


def _trade_days(fetcher: TushareFetcher, start: str, end: str) -> list[str]:
    """Trading calendar between start and end inclusive (tushare trade_cal)."""
    try:
        cal = fetcher._call("trade_cal", None, exchange="SSE", start_date=start, end_date=end)
        if cal is not None and not cal.empty and "cal_date" in cal.columns and "is_open" in cal.columns:
            open_days = cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist()
            if open_days:
                return sorted(open_days)
    except Exception as exc:  # noqa: BLE001 - fall back to weekday sequence
        print(f"[warn] trade_cal unavailable ({exc}); falling back to weekdays")
    days = pd.date_range(start, end, freq="B").strftime(TUSHARE_DATE_FMT).tolist()
    return sorted(days)


def _strip_suffix(ts_code: str) -> str:
    return str(ts_code).split(".")[0].zfill(6)


def _ymd_to_ts(series: pd.Series) -> pd.Series:
    """YYYYMMDD numeric (int/float/nullable, may contain NaN) -> datetime.

    Going via astype(str) on a float column yields '20200810.0' which breaks
    format='%Y%m%d'; route through nullable Int64 to keep it clean."""
    return pd.to_datetime(
        series.astype("Int64").astype(str).replace("<NA>", pd.NA),
        format="%Y%m%d", errors="coerce",
    )


def fetch_cb_basic(fetcher: TushareFetcher) -> pd.DataFrame:
    """Current-snapshot terms for every CB tushare knows about (incl. delisted)."""
    df = fetcher._call("cb_basic", None)
    if df is None or df.empty:
        raise RuntimeError("cb_basic returned no data")
    df["code"] = df["ts_code"].map(_strip_suffix)
    df["stock_code"] = df["stk_code"].map(_strip_suffix)
    for c in ("list_date", "delist_date", "maturity_date", "value_date",
              "conv_start_date", "conv_end_date"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def window_bonds(cb_basic: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Bonds alive at any point in [start, end]: listed before end AND
    (still listed OR delisted after start). Includes force-redeemed/delisted
    bonds — essential to avoid survivorship bias."""
    s, e = int(start), int(end)
    mask = (cb_basic["list_date"] <= e) & (
        cb_basic["delist_date"].isna() | (cb_basic["delist_date"] >= s)
    )
    return cb_basic.loc[mask].drop_duplicates("code")


def fetch_cb_daily(fetcher: TushareFetcher, bonds: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """SURVIVORSHIP-FREE OHLCV: fetch each bond's full history by ts_code.

    A trade_date-range query silently omits delisted/force-redeemed bonds
    (often the rallied winners) -> survivorship bias. Querying by ts_code
    returns that bond's history right up to its delisting. `bonds` is the
    window-filtered cb_basic (incl. delisted)."""
    s, e = int(start), int(end)
    frames = []
    codes = bonds[["ts_code", "code"]].drop_duplicates("code").reset_index(drop=True)
    for i, row in enumerate(codes.itertuples(index=False)):
        try:
            df = fetcher._call("cb_daily", None, ts_code=row.ts_code)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] cb_daily {row.ts_code} failed: {exc}")
            continue
        if df is None or df.empty:
            continue
        td = pd.to_numeric(df["trade_date"], errors="coerce")
        df = df.loc[(td >= s) & (td <= e)].copy()
        if not df.empty:
            frames.append(df)
        if (i + 1) % 50 == 0:
            time.sleep(0.25)  # gentle on tushare rate limit
    if not frames:
        raise RuntimeError("cb_daily returned no data")
    raw = pd.concat(frames, ignore_index=True).drop_duplicates(["ts_code", "trade_date"])
    raw["code"] = raw["ts_code"].map(_strip_suffix)
    raw["date"] = pd.to_datetime(raw["trade_date"].astype(str), format="%Y%m%d")
    return raw


def fetch_cb_share(fetcher: TushareFetcher, bonds: pd.DataFrame) -> pd.DataFrame:
    """PIT conversion_price from cb_share (publish_date), per bond.

    cb_basic only has the LATEST conversion_price -> look-ahead on parity/
    premium. cb_share publishes conversion_price over time (captures 下修),
    so an asof on publish_date gives the price known as-of each date."""
    codes = bonds[["ts_code", "code"]].drop_duplicates("code").reset_index(drop=True)
    frames = []
    for i, row in enumerate(codes.itertuples(index=False)):
        try:
            df = fetcher._call("cb_share", None, ts_code=row.ts_code)
        except Exception:  # noqa: BLE001
            continue
        if df is None or df.empty or "publish_date" not in df.columns:
            continue
        pub = pd.to_datetime(df["publish_date"].astype(str), format="%Y%m%d", errors="coerce")
        cp = pd.to_numeric(df.get("convert_price"), errors="coerce")
        sub = pd.DataFrame({"code": row.code, "publish_date": pub, "conv_price_pit": cp})
        sub = sub.dropna(subset=["publish_date", "conv_price_pit"])
        sub = sub[sub["conv_price_pit"] > 0]
        if not sub.empty:
            frames.append(sub.sort_values("publish_date").drop_duplicates("publish_date", keep="last"))
        if (i + 1) % 50 == 0:
            time.sleep(0.25)
    if not frames:
        return pd.DataFrame(columns=["code", "publish_date", "conv_price_pit"])
    return pd.concat(frames, ignore_index=True)


def fetch_stock_panel(fetcher: TushareFetcher, stock_codes: set[str], days: list[str]) -> pd.DataFrame:
    """Underlying-stock daily close for the dates we need, filtered to CB underlyings.

    trade_date-scoped calls return the whole market for one day; filtering to the
    ~300 CB underlyings keeps payloads small and call count = number of days."""
    wanted = {c.zfill(6) for c in stock_codes if c}
    rows = []
    for i, day in enumerate(days):
        try:
            df = fetcher._call("daily", None, trade_date=day)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] daily {day} failed: {exc}")
            continue
        if df is None or df.empty:
            continue
        df = df[df["ts_code"].str[:6].isin(wanted)].copy()
        if not df.empty:
            df["stock_code"] = df["ts_code"].str[:6]
            df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
            rows.append(df[["stock_code", "date", "close", "pct_chg"]].rename(
                columns={"close": "stock_close", "pct_chg": "stock_pct_chg"}
            ))
        if (i + 1) % 40 == 0:
            time.sleep(0.2)  # gentle on tushare rate limit
    if not rows:
        return pd.DataFrame(columns=["stock_code", "date", "stock_close", "stock_pct_chg"])
    return pd.concat(rows, ignore_index=True).drop_duplicates(["stock_code", "date"])


def fetch_cb_call(fetcher: TushareFetcher, start: str, end: str) -> pd.DataFrame:
    """PIT 强赎/到期 events from cb_call, paged by quarter (tushare row cap).

    Returns the full call lifecycle per announcement: is_call in
    {已满足强赎条件, 公告提示强赎, 公告实施强赎, 公告不强赎, 公告到期赎回}."""
    s = pd.to_datetime(start, format="%Y%m%d")
    e = pd.to_datetime(end, format="%Y%m%d")
    frames = []
    cur = s
    while cur <= e:
        nxt = min(cur + pd.DateOffset(months=3), e)
        try:
            df = fetcher._call("cb_call", None, start_date=_ts(cur), end_date=_ts(nxt))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] cb_call {_ts(cur)} failed: {exc}")
            df = None
        if df is not None and not df.empty:
            frames.append(df)
        cur = nxt + pd.Timedelta(days=1)
        time.sleep(0.2)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(["ts_code", "ann_date", "is_call"])
    raw["code"] = raw["ts_code"].map(_strip_suffix)
    raw["ann_date"] = _ymd_to_ts(raw["ann_date"])
    return raw


def build_events_pit(call_events: pd.DataFrame) -> pd.DataFrame:
    """cb_events_pit: per (code, ann_date) call state for the engine to asof-ffill.

    call_announced True once trigger/announcement; call_count set to
    call_required so progress jumps to 1.0 at announcement; recent_no_reset
    when issuer explicitly declined to call (公告不强赎)."""
    cols = ["date", "code", "call_announced", "call_count",
            "call_required", "recent_no_reset"]
    if call_events.empty:
        return pd.DataFrame(columns=cols)
    e = call_events.dropna(subset=["ann_date", "code"]).copy()
    announced = e["is_call"].isin(["已满足强赎条件", "公告提示强赎", "公告实施强赎"])
    out = pd.DataFrame({
        "date": e["ann_date"],
        "code": e["code"],
        "call_announced": announced,
        "call_count": announced.astype(int) * 15,
        "call_required": 15,
        "recent_no_reset": e["is_call"].eq("公告不强赎"),
    })
    # keep the latest state per (code, date)
    return (out.sort_values(["code", "date"])
            .drop_duplicates(["code", "date"], keep="last")
            .reset_index(drop=True))


def per_bond_call_date(call_events: pd.DataFrame) -> pd.DataFrame:
    """code -> final call_date (last trading day before 强赎 redemption)."""
    if call_events.empty or "call_date" not in call_events.columns:
        return pd.DataFrame(columns=["code", "call_date"])
    e = call_events.copy()
    e["call_date"] = _ymd_to_ts(e["call_date"])
    e = e.dropna(subset=["call_date"])
    return (e.groupby("code")["call_date"].max().reset_index()
            .rename(columns={"call_date": "force_call_date"}))


def fetch_cb_ratings() -> pd.DataFrame:
    """Bond code -> 信用评级, from akshare bond_zh_cov (东财可转债一览).

    tushare exposes no CB rating; akshare does. 东财 must go through the QG
    proxy pool or the IP gets banned (CLAUDE.md: 禁止裸连东财). This script is
    a sanctioned fmdata data source, so it may call akshare with the proxy."""
    from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy
    import akshare as ak

    proxy = _get_qg_proxy()
    try:
        if proxy:
            _set_requests_proxy(proxy)
        df = ak.bond_zh_cov()
    finally:
        _set_requests_proxy(None)
    if df is None or df.empty:
        print("[warn] bond_zh_cov returned no ratings")
        return pd.DataFrame(columns=["code", "rating"])
    col_code = "债券代码" if "债券代码" in df.columns else df.columns[0]
    col_rate = "信用评级" if "信用评级" in df.columns else None
    if col_rate is None:
        print("[warn] no 信用评级 column in bond_zh_cov")
        return pd.DataFrame(columns=["code", "rating"])
    out = pd.DataFrame({
        "code": df[col_code].astype(str).str.zfill(6),
        "rating": df[col_rate].astype(str).str.strip().str.upper(),
    })
    out = out[out["rating"].notna() & (out["rating"] != "") & (out["rating"] != "NAN")]
    return out.drop_duplicates("code")


def fetch_stock_industry(fetcher: TushareFetcher) -> pd.DataFrame:
    """ts_code -> industry mapping for sector derivation."""
    try:
        df = fetcher._call("stock_basic", None, exchange="", list_status="L")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] stock_basic failed: {exc}")
        return pd.DataFrame(columns=["stock_code", "sector"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["stock_code", "sector"])
    df = df[df["ts_code"].notna()].copy()
    df["stock_code"] = df["ts_code"].str[:6]
    df["sector"] = df.get("industry", pd.Series(index=df.index)).fillna("UNKNOWN")
    return df[["stock_code", "sector"]].drop_duplicates("stock_code")


def _parse_coupon_schedule(rate_clause: object) -> list[tuple[int, float]] | None:
    """Parse 'YYYYMMDD-YYYYMMDD,票面利率:1.50%;...' into [(start_date, rate_pct), ...]."""
    import re
    if not isinstance(rate_clause, str) or not rate_clause:
        return None
    segs = []
    for seg in rate_clause.split(";"):
        m = re.search(r"(\d{8})\s*-\s*\d{8}.*?票面利率\s*[:：]\s*([\d.]+)", seg)
        if m:
            segs.append((int(m.group(1)), float(m.group(2))))
    return sorted(segs) if segs else None


def _bond_ytm_series(dates, prices, par, maturity_date, coupon_rate, schedule):
    """YTM (%) per row via cashflow IRR with step-up schedule.

    For each date, build remaining annual coupons (rate from schedule as-of
    each future coupon date, fallback to coupon_rate) + par at maturity, then
    bisection-solve the yield that discounts them to the current price."""
    import numpy as np
    out = np.full(len(dates), np.nan)
    mat = pd.Timestamp(maturity_date)
    for idx in range(len(dates)):
        price = prices[idx]
        asof = dates[idx]
        if not (price and price > 0) or pd.isna(asof) or pd.isna(mat):
            continue
        years = (mat - asof).days / 365.25
        if years <= 0:
            continue
        # remaining annual coupons (incl. maturity); round() avoids the
        # 3.0y -> 3.0014 -> ceil=4 float artifact from leap-year day counts.
        n = max(1, int(round(years)))
        cf = np.empty(n, dtype=float)
        for y in range(1, n + 1):
            cd = asof + pd.Timedelta(days=365 * y)
            cdi = int(cd.strftime("%Y%m%d"))
            rate = coupon_rate
            if schedule:
                appl = [r for (s, r) in schedule if s <= cdi]
                if appl:
                    rate = appl[-1]
            cf[y - 1] = par * (rate or 0.0) / 100.0
        cf[-1] += par
        times = np.arange(1, n + 1, dtype=float)
        lo, hi = -0.9, 2.0

        def npv(y):
            return float(np.sum(cf / (1.0 + y) ** times) - price)

        if npv(lo) * npv(hi) > 0:
            continue
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            if npv(mid) > 0:
                lo = mid
            else:
                hi = mid
        out[idx] = 0.5 * (lo + hi) * 100.0
    return out


def assemble_panel(cb_raw, cb_basic, stock_panel, industry, ratings, share, force_call) -> pd.DataFrame:
    # --- terms: merge static fields onto each daily row by code ---
    terms = cb_basic[[
        "code", "stock_code", "conv_price", "list_date", "maturity_date",
        "conv_start_date", "conv_stop_date", "delist_date", "remain_size",
        "issue_size", "coupon_rate", "bond_short_name", "rate_clause",
    ]].copy()
    terms = terms.drop_duplicates("code")
    terms["listing_date"] = _ymd_to_ts(terms["list_date"])
    terms["maturity_date"] = _ymd_to_ts(terms["maturity_date"])
    terms["conversion_start_date"] = _ymd_to_ts(terms["conv_start_date"])
    terms["last_conversion_date"] = _ymd_to_ts(terms["conv_stop_date"])
    terms["delisting_date"] = _ymd_to_ts(terms["delist_date"])
    if not force_call.empty:
        terms = terms.merge(force_call, on="code", how="left")
        terms["last_trade_date"] = terms["force_call_date"]
    else:
        terms["last_trade_date"] = pd.NaT
    # remaining_size: cb_basic is a CURRENT snapshot, so remain_size is 0/NaN
    # for ~808/1147 bonds (delisted or fully redeemed). Fall back to issue_size
    # (original issuance, populated for 1146/1147) so the universe filter does
    # not hard-exclude them as SMALL_REMAINING_SIZE. Static overestimate, but
    # consistent with the panel's static-size treatment.
    remain = pd.to_numeric(terms["remain_size"], errors="coerce")
    issue = pd.to_numeric(terms["issue_size"], errors="coerce")
    terms["remaining_size_million"] = remain.where(remain > 0, issue).fillna(0) / 1.0e6
    terms["coupon_rate"] = pd.to_numeric(terms["coupon_rate"], errors="coerce")
    terms = terms.rename(columns={"bond_short_name": "name", "conv_price": "conversion_price"})

    panel = cb_raw.merge(terms, on="code", how="left")

    # --- underlying stock close ---
    panel = panel.merge(stock_panel, on=["stock_code", "date"], how="left")

    # --- sector from underlying industry ---
    panel = panel.merge(industry, on="stock_code", how="left")

    # --- PIT conversion_price from cb_share (no look-ahead on parity/premium) ---
    if not share.empty:
        sh = (share.rename(columns={"publish_date": "date", "conv_price_pit": "conversion_price_pit"})
              .sort_values("date"))
        panel = pd.merge_asof(panel.sort_values("date"), sh,
                              on="date", by="code", direction="backward")
        # use PIT price where known, else fall back to cb_basic current snapshot
        panel["conversion_price"] = panel["conversion_price_pit"].fillna(panel["conversion_price"])

    # --- credit rating from akshare bond_zh_cov (tushare has none) ---
    panel = panel.merge(ratings, on="code", how="left")

    # --- engine column contract ---
    panel["cb_close"] = pd.to_numeric(panel["close"], errors="coerce")
    panel["cb_open"] = pd.to_numeric(panel["open"], errors="coerce")
    panel["amount_million"] = pd.to_numeric(panel["amount"], errors="coerce") / 1000.0  # 千元 -> 百万
    # tushare reports close=0 on suspended/no-trade days -> poison factor math
    # (pct_change -100%, inf vol, nan propagating through the optimizer).
    # Mask non-positive prices to NaN, then per-bond forward-fill so a suspended
    # bond carries its last price with ~0 return (matches reality).
    panel = panel.sort_values(["code", "date"])
    for col in ("cb_close", "cb_open"):
        panel[col] = panel[col].where(panel[col] > 0)
        panel[col] = panel.groupby("code")[col].ffill()
    panel["redemption_price"] = DEFAULT_REDEMPTION  # placeholder: real 赎回价 in prospectus
    panel["redemption_price_source"] = "placeholder_not_prospectus_verified"
    panel["redemption_price_is_approx"] = True
    # cb_basic is a current snapshot. Its list_date must not be represented as
    # the announcement-effective date of every historical clause state.
    panel["terms_is_pit"] = False
    panel["terms_available_date"] = pd.NaT
    panel["rating"] = panel["rating"].fillna("")
    panel["sector"] = panel["sector"].fillna("UNKNOWN")
    # Quotes, terms, sector and rating are genuine market-sourced data; only
    # redemption_price is a placeholder and ytm is approximate -> market_verified.
    panel["source_status"] = "market_verified"

    # proper YTM via cashflow IRR with step-up coupon schedule (parsed from
    # rate_clause); falls back to coupon_rate when clause is unparseable.
    panel["ytm_pct"] = np.nan
    for code, g in panel.groupby("code"):
        sched = _parse_coupon_schedule(g["rate_clause"].iloc[0]) if "rate_clause" in g else None
        mat = g["maturity_date"].iloc[0]
        if pd.isna(mat):
            continue
        panel.loc[g.index, "ytm_pct"] = _bond_ytm_series(
            g["date"].values, g["cb_close"].values, PAR, mat,
            float(g["coupon_rate"].iloc[0]) if pd.notna(g["coupon_rate"].iloc[0]) else 0.0,
            sched,
        )
    # _bond_ytm_series uses the parsed coupon schedule where available, but
    # cashflow timing is annualised and principal redemption is assumed at par.
    # It is a diagnostic proxy, never a production carry input.
    panel["ytm_source"] = "tushare_rate_clause_annualised_redemption_assumed_par"
    panel["ytm_is_approx"] = True
    panel["cashflow_version"] = "rate_clause_v1_no_prospectus_redemption"

    cols = [
        "date", "code", "name", "stock_code", "cb_close", "cb_open",
        "amount_million", "remaining_size_million", "stock_close",
        "conversion_price", "redemption_price", "redemption_price_source",
        "redemption_price_is_approx", "ytm_pct", "ytm_source", "ytm_is_approx",
        "cashflow_version", "terms_is_pit", "terms_available_date", "rating", "sector",
        "listing_date", "maturity_date", "conversion_start_date",
        "last_trade_date", "last_conversion_date", "delisting_date", "source_status",
    ]
    return panel[cols].sort_values(["code", "date"]).reset_index(drop=True)


def build_terms_pit(cb_basic: pd.DataFrame) -> pd.DataFrame:
    terms = cb_basic.drop_duplicates("code").copy()
    terms["effective_date"] = _ymd_to_ts(terms["list_date"])
    terms["code"] = terms["ts_code"].map(_strip_suffix)
    out = pd.DataFrame({
        "code": terms["code"],
        "effective_date": terms["effective_date"],
        "conversion_price": pd.to_numeric(terms["conv_price"], errors="coerce"),
        "redemption_price": DEFAULT_REDEMPTION,
        "coupon_rate": pd.to_numeric(terms["coupon_rate"], errors="coerce"),
        "maturity_date": _ymd_to_ts(terms["maturity_date"]),
        "terms_source": "tushare_cb_basic_current_snapshot",
        "terms_is_pit": False,
        "terms_available_date": pd.NaT,
    })
    # merge_asof rejects null join keys; drop bonds with unknown list_date
    # (they cannot be point-in-time merged anyway).
    return out.dropna(subset=["code", "effective_date"]).sort_values(
        ["code", "effective_date"]
    ).reset_index(drop=True)


# Benchmark series is now the OFFICIAL CSI 000832 index, sourced via
# fmdata.cb_benchmark.build_market_daily (imported above). The previous
# equal-weight cb_raw.groupby("date")["ret"].mean() * 100 implementation is
# intentionally removed: it constructed a small-bond-tilted proxy that is NOT
# the official index and would silently change the benchmark identity.
# See docs/CB_OFFICIAL_BENCHMARK_INTEGRATION.md.


def write_csv(df: pd.DataFrame, rel: str) -> Path:
    path = MARKET_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)
    print(f"[ok] wrote {len(df):,} rows -> {path}")
    return path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build CB research panel for fmdata.")
    p.add_argument("--months", type=int, default=6, help="history window in months (default 6)")
    p.add_argument("--start", type=str, default=None, help="YYYYMMDD start (overrides --months)")
    p.add_argument("--end", type=str, default=None, help="YYYYMMDD end (default today)")
    p.add_argument("--official-index-file", type=str, default=None,
                   help="path to official CSI 000832 CSV (default: fmdata MARKET_DIR/csi_cb_idx_hist.csv)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    end = pd.Timestamp(args.end) if args.end else pd.Timestamp.now().normalize()
    if args.start:
        start = pd.Timestamp(args.start)
    else:
        start = end - pd.DateOffset(months=args.months)
    start_s, end_s = _ts(start), _ts(end)
    print(f"[info] building CB panel {start_s} -> {end_s}")

    fetcher = TushareFetcher()

    days = _trade_days(fetcher, start_s, end_s)
    print(f"[info] trade days: {len(days)} ({days[0]} .. {days[-1]})")

    cb_basic = fetch_cb_basic(fetcher)
    print(f"[info] cb_basic: {len(cb_basic):,} bonds (incl. delisted)")

    bonds = window_bonds(cb_basic, start_s, end_s)
    print(f"[info] window bonds: {len(bonds):,} "
          f"({(bonds['delist_date'].notna()).sum()} delisted/force-redeemed included)")

    cb_raw = fetch_cb_daily(fetcher, bonds, start_s, end_s)
    print(f"[info] cb_daily: {len(cb_raw):,} rows, {cb_raw['code'].nunique()} bonds, "
          f"{cb_raw['date'].nunique()} days")

    share = fetch_cb_share(fetcher, bonds)
    print(f"[info] cb_share PIT conversion_price: {len(share):,} snapshots, "
          f"{share['code'].nunique() if not share.empty else 0} bonds")

    call_events = fetch_cb_call(fetcher, start_s, end_s)
    events_pit = build_events_pit(call_events)
    force_call = per_bond_call_date(call_events)
    print(f"[info] cb_call events: {len(call_events):,} -> cb_events_pit {len(events_pit):,} rows, "
          f"{force_call['code'].nunique() if not force_call.empty else 0} 强赎'd bonds")

    active_codes = set(cb_raw["code"].unique())
    stock_codes = set(cb_basic.loc[cb_basic["code"].isin(active_codes), "stock_code"].dropna())
    print(f"[info] fetching underlying stock daily for {len(stock_codes)} stocks / {len(days)} days")
    stock_panel = fetch_stock_panel(fetcher, stock_codes, days)
    print(f"[info] stock_panel: {len(stock_panel):,} rows")

    industry = fetch_stock_industry(fetcher)
    print(f"[info] industry map: {len(industry):,} stocks")

    ratings = fetch_cb_ratings()
    print(f"[info] ratings: {len(ratings):,} bonds with 信用评级")

    panel = assemble_panel(cb_raw, cb_basic, stock_panel, industry, ratings, share, force_call)
    terms_pit = build_terms_pit(cb_basic)
    market_daily = build_market_daily(cb_raw, official_index_file=args.official_index_file)

    write_csv(panel, "cb_daily.csv")
    write_csv(terms_pit, "cb_terms_pit.csv")
    write_csv(market_daily, "cb_market_daily.csv")
    write_csv(events_pit, "cb_events_pit.csv")

    # quick QA
    latest = panel["date"].max()
    print(f"[qa] latest date={latest.date()} bonds={panel['code'].nunique()} "
          f"stock_close={panel['stock_close'].notna().mean():.1%} "
          f"ytm={panel['ytm_pct'].notna().mean():.1%} "
          f"rating={panel['rating'].astype(str).replace('', pd.NA).notna().mean():.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
