"""Cross-dataset single-stock fan-out behind ``GET /research/stock/{code}``.

Reads canonical store CSVs directly with server-side per-code filtering so
callers never pull whole-market tables (fmdata 413-gate philosophy). Tables
above ``_CHUNK_THRESHOLD`` rows are chunk-scanned to stay inside the memory
budget; every section fails soft — one broken dataset never aborts a report.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from fmdata.config import STORE_DIR

_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}
_CACHE_LOCK = threading.Lock()
_MAX_CACHE = 48
_CHUNK_THRESHOLD = 100_000
_CHUNK_SIZE = 400_000

SECTION_ORDER = [
    "identity",
    "ipo",
    "financials",
    "earnings",
    "price_volume",
    "valuation",
    "flows",
    "industry",
    "comps",
    "index_membership",
    "consensus",
    "research_reports",
    "institutional_holders",
    "convertible_bond",
    "announcements",
    "hot_topics",
    "events",
    "factors",
]


# ── registry & IO helpers ──────────────────────────────────────────────


def _datasets() -> dict[str, dict[str, Any]]:
    from fmdata.registry import load_registry

    reg = load_registry()
    if isinstance(reg, dict):
        ds = reg.get("datasets", {})
    else:  # defensive: registry may evolve to a list
        ds = {item.get("name", ""): item for item in reg}
    return ds if isinstance(ds, dict) else {}


def _store_path(name: str) -> Path | None:
    meta = _datasets().get(name)
    if not meta:
        return None
    rel = meta.get("file") if isinstance(meta, dict) else None
    return (STORE_DIR / rel) if rel else None


def _cache_get(key: tuple) -> pd.DataFrame | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    return hit[1] if hit else None


def _cache_put(key: tuple, frame: pd.DataFrame) -> pd.DataFrame:
    with _CACHE_LOCK:
        if len(_CACHE) > _MAX_CACHE:
            _CACHE.clear()
        _CACHE[key] = (datetime.now().timestamp(), frame)
    return frame


def _read_small(name: str, usecols: list[str] | None = None) -> pd.DataFrame:
    """Full read (with cache) for datasets below the chunk threshold."""
    path = _store_path(name)
    if path is None or not path.exists():
        return pd.DataFrame()
    mtime = path.stat().st_mtime
    key = (name, str(sorted(usecols)) if usecols else "*", mtime)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    cols = None
    if usecols:
        header = pd.read_csv(path, nrows=0)
        cols = [c for c in usecols if c in header.columns]
    frame = pd.read_csv(path, usecols=cols)
    return _cache_put(key, frame)


def _scan_big(
    name: str,
    mask_fn: Callable[[pd.DataFrame], pd.Series],
    usecols: list[str] | None = None,
    cache_id: str = "",
) -> pd.DataFrame:
    """Chunk-scan a large table, keeping only rows where mask_fn is True."""
    path = _store_path(name)
    if path is None or not path.exists():
        return pd.DataFrame()
    mtime = path.stat().st_mtime
    key = (name, cache_id, str(sorted(usecols)) if usecols else "*", mtime)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    cols = None
    if usecols:
        header = pd.read_csv(path, nrows=0)
        cols = [c for c in usecols if c in header.columns]
    kept: list[pd.DataFrame] = []
    with pd.read_csv(path, usecols=cols, chunksize=_CHUNK_SIZE) as reader:
        for chunk in reader:
            sel = chunk[mask_fn(chunk)]
            if not sel.empty:
                kept.append(sel)
    frame = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=cols or [])
    return _cache_put(key, frame)


def _code_mask(series: pd.Series, c6: str, ts: str) -> pd.Series:
    """Match a code column against 6-digit / ts_code forms across dtypes."""
    sv = series.astype(str).str.strip().str.upper()
    targets = {c6, f"{c6}.0", ts.upper()}
    return sv.isin(targets) | sv.str.startswith(f"{c6}.", na=False)


def _records(frame: pd.DataFrame, n: int = 20, sort_by: str | None = None) -> list[dict]:
    if frame.empty:
        return []
    out = frame.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].astype(str).where(out[col].notna(), None)
    if sort_by and sort_by in out.columns:
        out = out.sort_values(sort_by, ascending=False)
    return out.head(n).where(out.notna(), None).to_dict("records")


def _latest(frame: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if frame.empty or date_col not in frame.columns:
        return frame
    return frame.sort_values(date_col, ascending=False).drop_duplicates(
        [c for c in ("ts_code", "end_date") if c in frame.columns] or None,
        keep="first",
    )


def _tail(frame: pd.DataFrame, date_col: str, n: int) -> pd.DataFrame:
    if frame.empty or date_col not in frame.columns:
        return frame.head(n)
    return frame.sort_values(date_col, ascending=False).head(n)


def _section(
    status: str,
    payload: dict[str, Any],
    sources: list[str],
    note: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "source_datasets": sources, **payload}
    if note:
        out["note"] = note
    return out


def _fail(err: str, sources: list[str]) -> dict[str, Any]:
    return _section("error", {"error": err[:300]}, sources)


def _empty(sources: list[str], why: str) -> dict[str, Any]:
    return _section("empty", {"reason": why}, sources)


# ── section builders ───────────────────────────────────────────────────


def _sec_identity(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["stock_list", "stock_basic_all", "namechange", "suspend_d"]
    basic = _read_small("stock_list")
    row = basic[_code_mask(basic["ts_code"], c6, ts)] if not basic.empty else pd.DataFrame()
    if row.empty:
        return _empty(src, f"{ts} not in stock_list")
    rec = _records(row, 1)[0]
    allb = _read_small("stock_basic_all")
    if not allb.empty:
        extra = allb[_code_mask(allb["ts_code"], c6, ts)]
        if not extra.empty:
            for k, v in _records(extra, 1)[0].items():
                rec.setdefault(k, v)
    names = _read_small("namechange")
    if not names.empty:
        rec["name_history"] = _records(
            names[_code_mask(names["ts_code"], c6, ts)].sort_values("start_date"), 8
        )
    susp = _read_small("suspend_d", usecols=["ts_code", "trade_date", "suspend_timing", "suspend_type"])
    susp_f = susp[_code_mask(susp["ts_code"], c6, ts)] if not susp.empty else pd.DataFrame()
    rec["recent_suspensions"] = _records(susp_f, 10, "trade_date")
    list_date = str(rec.get("list_date") or "")
    rec["days_since_listing"] = (
        (date.today() - datetime.strptime(list_date, "%Y%m%d").date()).days
        if list_date and len(list_date) == 8
        else None
    )
    try:
        profile = _read_small("tech_indicators")
        p = profile[_code_mask(profile["ts_code"], c6, ts)] if not profile.empty else pd.DataFrame()
        if not p.empty:
            rec["concepts"] = str(p.iloc[0].get("concepts", ""))[:200]
            rec["main_business"] = str(p.iloc[0].get("main_business", ""))[:200]
            for k in ("board", "is_csi800", "sw_category"):
                if pd.notna(p.iloc[0].get(k)):
                    rec[k] = p.iloc[0].get(k)
    except Exception:  # noqa: BLE001
        pass
    return _section("ok", {"profile": rec}, src)


def _sec_ipo(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["stock_ipo"]
    try:
        ipo = _read_small("stock_ipo")
    except Exception as exc:  # noqa: BLE001
        return _fail(f"stock_ipo: {exc}", src)
    if ipo.empty:
        return _empty(src, "stock_ipo dataset empty")
    f = ipo[_code_mask(ipo["ts_code"], c6, ts)]
    if f.empty:
        return _empty(src, f"{ts} 不在 2023 以来 IPO 表中(老股或未上市)")
    return _section(
        "ok",
        {"发行信息": _records(f.sort_values("ipo_date"), 3)},
        src,
        note="字段: price发行价/funds募资额/ballot中签率/pe发行市盈率/amount发行量(万股); 招股书全文走公告链接+妙想提炼",
    )


def _sec_financials(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["income", "balancesheet", "cashflow", "fina_indicator_combined"]
    out: dict[str, Any] = {}
    specs = {
        "income": (
            ["ts_code", "end_date", "ann_date", "total_revenue", "revenue", "operate_profit", "n_income", "n_income_attr_p"],
            "利润表",
        ),
        "balancesheet": (
            ["ts_code", "end_date", "ann_date", "total_cur_assets", "total_assets", "total_cur_liab", "total_liab", "inventories", "accounts_receiv"],
            "资产负债表",
        ),
        "cashflow": (
            ["ts_code", "end_date", "ann_date", "n_cashflow_act", "n_cashflow_inv_act", "free_cashflow", "end_bal_cash"],
            "现金流量表",
        ),
    }
    for name, (cols, label) in specs.items():
        try:
            df = _scan_big(name, lambda ch: _code_mask(ch["ts_code"], c6, ts), usecols=cols, cache_id=ts)
            df = _latest(df, "ann_date")
            out[label] = _records(_tail(df, "end_date", 12), 12)
        except Exception as exc:  # noqa: BLE001
            out[label] = {"error": str(exc)[:200]}
    try:
        fina = _scan_big(
            "fina_indicator_combined",
            lambda ch: _code_mask(ch["ts_code"], c6, ts),
            usecols=["ts_code", "ann_date", "end_date", "roe", "roe_yoy", "eps", "dt_eps", "bps", "or_yoy", "dt_netprofit_yoy", "basic_eps_yoy"],
            cache_id=ts,
        )
        out["财务指标"] = _records(_tail(_latest(fina, "ann_date"), "end_date", 12), 12)
    except Exception as exc:  # noqa: BLE001
        out["财务指标"] = {"error": str(exc)[:200]}
    try:
        ext = _read_small("stock_fina_extended",
                          usecols=["ts_code", "end_date", "ann_date", "gross_margin", "netprofit_margin", "q_roe", "ocf_yoy", "op_yoy", "assets_turn"])
        ef = ext[_code_mask(ext["ts_code"], c6, ts)] if not ext.empty else pd.DataFrame()
        out["盈利质量扩展"] = _records(_tail(ef, "end_date", 8), 8)
    except Exception as exc:  # noqa: BLE001
        out["盈利质量扩展"] = {"error": str(exc)[:200]}
    if not any(out.values()):
        return _empty(src, "no statement rows")
    return _section("ok", out, src)


def _sec_earnings(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["performance_forecast", "performance_express", "income_full", "semiannual_tracker"]
    out: dict[str, Any] = {}
    try:
        pf = _read_small("performance_forecast")
        out["业绩预告"] = _records(pf[_code_mask(pf["SECURITY_CODE"], c6, ts)] if not pf.empty else pf, 6, "NOTICE_DATE")
    except Exception as exc:  # noqa: BLE001
        out["业绩预告"] = {"error": str(exc)[:200]}
    try:
        pe = _read_small("performance_express")
        out["业绩快报"] = _records(pe[_code_mask(pe["股票代码"], c6, ts)] if not pe.empty else pe, 4)
    except Exception as exc:  # noqa: BLE001
        out["业绩快报"] = {"error": str(exc)[:200]}
    try:
        inc = _scan_big("income_full", lambda ch: _code_mask(ch["ts_code"], c6, ts), usecols=["ts_code", "ann_date", "end_date", "归母净利润_元", "营收_元"], cache_id=ts)
        out["正式财报"] = _records(_tail(inc, "ann_date", 6), 6)
    except Exception as exc:  # noqa: BLE001
        out["正式财报"] = {"error": str(exc)[:200]}
    try:
        tr = _read_small("semiannual_tracker")
        out["半年报追踪"] = _records(tr[_code_mask(tr["code"], c6, ts)] if not tr.empty else tr, 4, "notice_date")
    except Exception as exc:  # noqa: BLE001
        out["半年报追踪"] = {"error": str(exc)[:200]}
    try:
        af = _read_small("actual_financials")
        f = af[_code_mask(af["SECURITY_CODE"], c6, ts)] if not af.empty else pd.DataFrame()
        out["近四季业绩报表"] = _records(_tail(f, "REPORTDATE", 4), 4)
    except Exception as exc:  # noqa: BLE001
        out["近四季业绩报表"] = {"error": str(exc)[:200]}
    return _section("ok", out, src, note="合并优先级: 财报>快报>预告 (fdm规则)")


def _sec_price_volume(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["daily", "tech_signals"]
    try:
        px = _scan_big(
            "daily",
            lambda ch: _code_mask(ch["ts_code"], c6, ts),
            usecols=["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "amount"],
            cache_id=ts,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"daily scan: {exc}", src)
    if px.empty:
        return _empty(src, f"no daily rows for {ts}")
    tail250 = _tail(px, "trade_date", 250)
    summary = {
        "latest": _records(tail250, 1)[0],
        "ret_5d": round(float(tail250["pct_chg"].head(5).sum()), 2),
        "ret_20d": round(float(tail250["pct_chg"].head(20).sum()), 2),
        "ret_250d": round(float(tail250["pct_chg"].sum()), 2),
        "high_250d": float(tail250["high"].max()),
        "low_250d": float(tail250["low"].min()),
    }
    tech = _read_small("tech_signals")
    tech_f = tech[_code_mask(tech["ts_code"], c6, ts)] if not tech.empty else pd.DataFrame()
    summary["technical"] = _records(_tail(tech_f, "trade_date", 3), 3)
    try:
        spot = _read_small("spot_snapshot", usecols=["ts_code", "close", "pct_chg", "amount", "capture_ts"])
        sp = spot[_code_mask(spot["ts_code"], c6, ts)] if not spot.empty else pd.DataFrame()
        if not sp.empty:
            r0 = sp.iloc[-1]
            summary["spot_latest"] = {"close": r0.get("close"), "pct_chg": r0.get("pct_chg"),
                                      "capture_ts": str(r0.get("capture_ts", ""))[:19]}
    except Exception:  # noqa: BLE001
        pass
    return _section("ok", {"price": summary, "recent_daily": _records(tail250, 10)}, src,
                    note="daily 为历史快照(非实时), spot_latest 为最近盘中快照")


def _sec_valuation(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["daily_basic_hist", "daily_basic_dividend_history"]
    db = _read_small("daily_basic_hist")
    if db.empty:
        return _empty(src, "daily_basic_hist empty")
    f = db[_code_mask(db["ts_code"], c6, ts)]
    if f.empty:
        return _empty(src, f"no valuation rows for {ts}")
    f = _tail(f, "trade_date", 60)
    latest = _records(f, 1)[0]
    pctile = {}
    for col in ("pe_ttm", "pb", "ps_ttm", "dv_ratio"):
        if col in f.columns and pd.notna(f[col].iloc[0]):
            series = f[col].dropna()
            pctile[col + "_pctile_in_window"] = (
                round(float((series < series.iloc[0]).mean()) * 100, 1) if len(series) > 1 else None
            )
    return _section(
        "ok",
        {"latest": latest, "history": _records(f, 20), "percentiles": pctile},
        src,
        note="窗口分位仅覆盖 daily_basic_hist 现存行数(近期), 非完整3年",
    )


def _sec_flows(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["stock_moneyflow_daily", "margin_detail", "lhb_daily", "limit_list_d"]
    out: dict[str, Any] = {}
    try:
        mf = _read_small("stock_moneyflow_daily")
        f = mf[_code_mask(mf["ts_code"], c6, ts)] if not mf.empty else pd.DataFrame()
        lg = [c for c in ("buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount") if c in f.columns]
        if lg and not f.empty:
            net = f[lg[0]] + (f[lg[2]] if len(lg) > 2 else 0) - f[lg[1]] - (f[lg[3]] if len(lg) > 3 else 0)
            f = f.assign(main_net_amount=net)
        out["主力资金"] = _records(_tail(f, "trade_date", 20), 20)
    except Exception as exc:  # noqa: BLE001
        out["主力资金"] = {"error": str(exc)[:200]}
    try:
        md = _read_small("margin_detail", usecols=["信用交易日期", "标的证券代码", "融资余额", "融券余量"])
        f = md[_code_mask(md["标的证券代码"], c6, ts)] if not md.empty else pd.DataFrame()
        out["融资融券"] = _records(_tail(f, "信用交易日期", 10), 10)
    except Exception as exc:  # noqa: BLE001
        out["融资融券"] = {"error": str(exc)[:200]}
    try:
        lb = _read_small("lhb_daily")
        out["龙虎榜"] = _records(lb[_code_mask(lb["ts_code"], c6, ts)] if not lb.empty else lb, 6, "trade_date")
    except Exception as exc:  # noqa: BLE001
        out["龙虎榜"] = {"error": str(exc)[:200]}
    try:
        ll = _read_small("limit_list_d")
        out["涨跌停"] = _records(ll[_code_mask(ll["ts_code"], c6, ts)] if not ll.empty else ll, 6, "trade_date")
    except Exception as exc:  # noqa: BLE001
        out["涨跌停"] = {"error": str(exc)[:200]}
    return _section("ok", out, src)


def _industry_of(ts: str, c6: str, as_of: str) -> dict | None:
    pts = _read_small("sw_industry_pts")
    if pts.empty:
        return None
    f = pts[_code_mask(pts["stock_code"], c6, ts)]
    if f.empty:
        return None
    today = as_of.replace("-", "")
    f = f[(f["out_date"].isna()) | (f["out_date"].astype(str) <= "9999-")]
    row = f.sort_values("in_date", ascending=False).iloc[0].to_dict()
    row = {k: (None if pd.isna(v) else v) for k, v in row.items()}
    return row


def _sec_industry(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["sw_industry_pts", "sw_first_level_close", "sw_pe_history", "sw_pb_history", "sw_main_flow_net"]
    row = _industry_of(ts, c6, as_of)
    if not row:
        return _empty(src, f"no PIT industry mapping for {ts}")
    out: dict[str, Any] = {"industry": row}
    l1_name = str(row.get("l1_name") or "")
    for label, name in (("industry_pe", "sw_pe_history"), ("industry_pb", "sw_pb_history")):
        try:
            wide = _read_small(name)
            if not wide.empty and l1_name and l1_name in wide.columns:
                datecol = wide.columns[0]
                lastrow = wide.sort_values(datecol).iloc[-1]
                out[label] = {"as_of": str(lastrow[datecol]), "value": lastrow.get(l1_name)}
        except Exception as exc:  # noqa: BLE001
            out[label] = {"error": str(exc)[:150]}
    try:
        close = _read_small("sw_first_level_close")
        if not close.empty and l1_name and l1_name in close.columns:
            datecol = close.columns[0]
            s = close[[datecol, l1_name]].dropna().sort_values(datecol)
            if len(s) >= 2:
                out["industry_perf_1y"] = {
                    "latest": float(s[l1_name].iloc[-1]),
                    "chg_1y_pct": round((float(s[l1_name].iloc[-1]) / float(s[l1_name].iloc[-252]) - 1) * 100, 1) if len(s) > 252 else None,
                }
    except Exception as exc:  # noqa: BLE001
        out["industry_perf_1y"] = {"error": str(exc)[:150]}
    try:
        sv = _read_small("sw_fundamentals_v10")
        l1_code = str(row.get("l1_code") or "")
        if not sv.empty and l1_code:
            hit = sv[sv["ts_code"].astype(str) == l1_code].sort_values("period")
            if not hit.empty:
                r = hit.iloc[-1]
                out["industry_fundamentals"] = {"period": str(r.get("period")), "pe": r.get("pe"), "pb": r.get("pb"),
                                                "avg_total_mv": r.get("avg_total_mv")}
    except Exception as exc:  # noqa: BLE001
        out["industry_fundamentals"] = {"error": str(exc)[:150]}
    try:
        hg = _read_small("historical_industry_growth")
        if not hg.empty and l1_name:
            hit = hg[hg["industry_name"].astype(str) == l1_name].sort_values("period")
            if not hit.empty:
                r = hit.iloc[-1]
                out["industry_growth"] = {"period": str(r.get("period")),
                                          "median_netprofit_yoy": r.get("median_netprofit_yoy"),
                                          "median_roe": r.get("median_roe"), "stock_count": r.get("stock_count")}
    except Exception as exc:  # noqa: BLE001
        out["industry_growth"] = {"error": str(exc)[:150]}
    return _section("ok", out, src, note="行业归属为 PIT (sw_industry_pts); 宽表列名=申万一级行业中文名")


def _sec_comps(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["sw_industry_pts", "daily_basic_hist", "fina_indicator_combined", "stock_list", "extended_industry_median"]
    row = _industry_of(ts, c6, as_of)
    if not row:
        return _empty(src, "no industry mapping → comps unavailable")
    l1, l1_name = str(row.get("l1_code") or ""), row.get("l1_name")
    pts = _read_small("sw_industry_pts")
    members = pts[(pts["l1_code"] == l1) & (pts["out_date"].isna())] if not pts.empty else pd.DataFrame()
    if members.empty:
        return _empty(src, f"no current members in {l1}")
    codes = members["stock_code"].astype(str).str[:6]
    # 邻近市值: 用 daily_basic_hist 最新快照的总市值排距离
    db = _read_small("daily_basic_hist", usecols=["ts_code", "trade_date", "total_mv", "pe_ttm", "pb"])
    med: dict[str, Any] = {}
    if not db.empty:
        snap = db.sort_values("trade_date").groupby("ts_code").tail(1).set_index("ts_code")
        target_mv = snap.at[ts, "total_mv"] if ts in snap.index and pd.notna(snap.at[ts, "total_mv"]) else None
        pool = snap[snap.index.astype(str).str[:6].isin(set(codes))]
        if target_mv:
            pool = pool.assign(_d=(pool["total_mv"] - float(target_mv)).abs()).nsmallest(9, "_d")
        else:
            pool = pool.nlargest(9, "total_mv")
        names = _read_small("stock_list", usecols=["ts_code", "name"])
        nmap = dict(zip(names["ts_code"], names["name"])) if not names.empty else {}
        med = {
            r["ts_code"]: {
                "ts_code": r["ts_code"],
                "name": nmap.get(r["ts_code"]),
                "total_mv": r.get("total_mv"),
                "pe_ttm": r.get("pe_ttm"),
                "pb": r.get("pb"),
            }
            for _, r in pool.reset_index().iterrows()
        }
    # comps 的盈利指标 (ROE/增速) 一次 chunk 扫描
    try:
        fina = _scan_big(
            "fina_indicator_combined",
            lambda ch: ch["ts_code"].astype(str).str[:6].isin(set(codes)) | _code_mask(ch["ts_code"], c6, ts),
            usecols=["ts_code", "ann_date", "end_date", "roe", "or_yoy", "dt_netprofit_yoy"],
            cache_id="comps:" + l1,
        )
        for tc, grp in fina.groupby("ts_code"):
            if tc in med:
                last = grp.sort_values("ann_date").iloc[-1]
                med[tc]["roe"] = None if pd.isna(last["roe"]) else float(last["roe"])
                med[tc]["or_yoy"] = None if pd.isna(last["or_yoy"]) else float(last["or_yoy"])
    except Exception as exc:  # noqa: BLE001
        med["_fina_error"] = str(exc)[:150]
    med_row = None
    try:
        ext = _read_small("extended_industry_median")
        if not ext.empty and l1_name:
            hit = ext[ext["industry"].astype(str) == str(l1_name)].sort_values("period")
            if not hit.empty:
                r = hit.iloc[-1]
                med_row = {"period": str(r.get("period")), "roe": r.get("roe"), "or_yoy": r.get("or_yoy"), "netprofit_yoy": r.get("netprofit_yoy")}
    except Exception:  # noqa: BLE001
        pass
    return _section(
        "ok",
        {"l1": l1, "l1_name": l1_name, "peer_set": "市值邻近" if med else "fina_only", "comps": list(med.values()), "industry_median": med_row},
        src,
    )


def _sec_index_membership(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["index_weight_000300_full", "index_weight_000905_full", "index_weight_000852_full"]
    out: dict[str, Any] = {}
    for name, label in (
        ("index_weight_000300_full", "沪深300"),
        ("index_weight_000905_full", "中证500"),
        ("index_weight_000852_full", "中证1000"),
    ):
        try:
            w = _read_small(name, usecols=["con_code", "trade_date", "weight"])
            f = w[_code_mask(w["con_code"], c6, ts)] if not w.empty else pd.DataFrame()
            if f.empty:
                out[label] = {"member": False}
            else:
                f = f.sort_values("trade_date")
                out[label] = {
                    "member": True,
                    "since": str(f["trade_date"].iloc[0]),
                    "latest": str(f["trade_date"].iloc[-1]),
                    "weight": float(f["weight"].iloc[-1]),
                }
        except Exception as exc:  # noqa: BLE001
            out[label] = {"error": str(exc)[:150]}
    return _section("ok", out, src, note="全历史成分表, 无幸存者偏差")


def _sec_consensus(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["analyst_consensus", "analyst_eps_forecast", "consensus_detail"]
    out: dict[str, Any] = {}
    try:
        ac = _read_small("analyst_consensus")
        f = ac[_code_mask(ac["SECURITY_CODE"], c6, ts)] if not ac.empty else pd.DataFrame()
        out["机构评级"] = _records(f, 3)
    except Exception as exc:  # noqa: BLE001
        out["机构评级"] = {"error": str(exc)[:200]}
    try:
        ef = _read_small("analyst_eps_forecast")
        f = ef[_code_mask(ef["代码"], c6, ts)] if not ef.empty else pd.DataFrame()
        out["EPS预测"] = _records(f, 2)
    except Exception as exc:  # noqa: BLE001
        out["EPS预测"] = {"error": str(exc)[:200]}
    try:
        cd = _read_small("consensus_detail")
        f = cd[_code_mask(cd["SECURITY_CODE"], c6, ts)] if not cd.empty else cd
        out["一致预期明细"] = _records(f, 8)
    except Exception as exc:  # noqa: BLE001
        out["一致预期明细"] = {"error": str(exc)[:200]}
    try:
        ch = _scan_big(
            "consensus_history",
            lambda col: _code_mask(col["ts_code"], c6, ts),
            usecols=["ts_code", "quarter", "eps_mean", "eps_std", "eps_count", "np_mean", "report_date"],
            cache_id=ts,
        )
        if not ch.empty:
            ser = ch.sort_values("report_date").groupby("quarter").tail(1).sort_values("quarter").tail(8)
            eps = [None if pd.isna(v) else float(v) for v in ser["eps_mean"]]
            out["EPS预期修正轨迹"] = {
                "quarters": ser["quarter"].tolist(),
                "eps_mean": eps,
                "eps_mean_3q_change": round(eps[-1] - eps[-4], 3) if len(eps) >= 4 and eps[-1] is not None and eps[-4] is not None else None,
                "note": "同一 quarter 多期报告取最新; 正值=预期上修",
            }
    except Exception as exc:  # noqa: BLE001
        out["EPS预期修正轨迹"] = {"error": str(exc)[:200]}
    return _section("ok", out, src)


def _sec_research_reports(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    """个股研报时间线(200万行研报标题库): 近15篇 + 近90天覆盖度变化。"""
    src = ["report_rc_full_history"]
    try:
        rr = _scan_big(
            "report_rc_full_history",
            lambda ch: _code_mask(ch["ts_code"], c6, ts),
            usecols=["ts_code", "report_date", "report_title", "report_type", "org_name", "author_name"],
            cache_id=ts,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"report_rc scan: {exc}", src)
    if rr.empty:
        return _empty(src, f"{ts} 无覆盖研报(低覆盖股)")
    rr = rr.sort_values("report_date", ascending=False)
    rd = rr["report_date"].astype(str).str[:10].str.replace("-", "", regex=False)
    rr["_d"] = rd.values
    from datetime import datetime, timedelta
    ref = datetime.strptime(as_of.replace("-", ""), "%Y%m%d")
    w0 = (ref - timedelta(days=90)).strftime("%Y%m%d")
    w1 = (ref - timedelta(days=180)).strftime("%Y%m%d")
    recent = int((rd >= w0).sum())
    prior = int(((rd >= w1) & (rd < w0)).sum())
    latest_date = rd.max() if len(rd) else None
    return _section(
        "ok",
        {
            "coverage": {"last90d": recent, "prior90d": prior,
                         "change_pct": round((recent - prior) / prior * 100, 1) if prior else None,
                         "data_cutoff": latest_date},
            "reports": _records(rr.drop(columns=["_d"]), 15),
        },
        src,
        note="op_rt 评级为数值编码; 研报库更新可能滞后(data_cutoff), 近期观点走妙想",
    )


def _sec_institutional_holders(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    """基金重仓穿透: 哪些基金持有该股(季报口径) + 持仓变化。"""
    src = ["fund_portfolio_merged", "fund_portfolio_tushare"]
    try:
        fp = _read_small("fund_portfolio_merged",
                         usecols=["ts_code", "ann_date", "end_date", "stock_code", "hold_amount", "stk_mkv_ratio", "stk_float_ratio", "source"])
        f = fp[_code_mask(fp["stock_code"], c6, ts)] if not fp.empty else pd.DataFrame()
    except Exception as exc:  # noqa: BLE001
        return _fail(f"fund_portfolio: {exc}", src)
    if f.empty:
        return _empty(src, "公募季报未见重仓(非重仓股或未披露期)")
    f = f.sort_values("end_date", ascending=False)
    latest_end = f["end_date"].astype(str).max()
    cur = f[f["end_date"].astype(str) == latest_end]
    prev_end = sorted({str(x) for x in f["end_date"]})[-2] if f["end_date"].nunique() > 1 else None
    prev = f[f["end_date"].astype(str) == prev_end] if prev_end else pd.DataFrame()
    funds_now, funds_prev = set(cur["ts_code"]), set(prev["ts_code"]) if not prev.empty else set()
    return _section(
        "ok",
        {
            "latest_period": latest_end,
            "fund_count": len(cur),
            "fund_count_prev": len(funds_prev),
            "new_entries": len(funds_now - funds_prev),
            "exits": len(funds_prev - funds_now),
            "top_holders": _records(cur.sort_values("stk_float_ratio", ascending=False), 10),
        },
        src,
        note="stk_float_ratio=占流通盘%, stk_mkv_ratio=占净值%; 季报口径(前十大重仓)",
    )


def _sec_convertible_bond(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    """正股存续可转债: 价格/剩余规模/转股价 + 条款(转股价/赎回价/票息/到期)。"""
    src = ["cb_daily", "cb_terms_pit"]
    try:
        cb = _scan_big(
            "cb_daily",
            lambda ch: _code_mask(ch["stock_code"].astype(str), c6, ts),
            usecols=["date", "code", "name", "stock_code", "cb_close", "amount_million", "remaining_size_million", "stock_close", "conversion_price"],
            cache_id=ts,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(f"cb_daily scan: {exc}", src)
    if cb.empty:
        return _empty(src, f"{ts} 无存续可转债")
    cb["_d"] = cb["date"].astype(str).str[:10].str.replace("-", "", regex=False)
    ref = datetime.strptime(as_of.replace("-", ""), "%Y%m%d")
    alive = cb[cb["_d"] >= (ref - timedelta(days=90)).strftime("%Y%m%d")]
    if alive.empty:
        return _empty(src, f"{ts} 历史有转债但均已到期/摘牌 (该股最新转债记录 {cb['_d'].max()})")
    latest = alive.sort_values("_d").groupby("code").tail(1)
    try:
        terms = _read_small("cb_terms_pit")
        tmap = {}
        if not terms.empty:
            tmap = {str(r["code"]): r for _, r in terms.iterrows()}
    except Exception:  # noqa: BLE001
        tmap = {}
    bonds = []
    for _, r in latest.iterrows():
        t = tmap.get(str(r["code"]), {})
        bonds.append({
            "code": r.get("code"), "name": r.get("name"), "cb_close": r.get("cb_close"),
            "remaining_size_million": r.get("remaining_size_million"),
            "conversion_price": r.get("conversion_price") or t.get("conversion_price"),
            "coupon_rate": t.get("coupon_rate"), "maturity_date": t.get("maturity_date"),
            "redemption_price": t.get("redemption_price"), "as_of_date": str(r.get("date")),
        })
    return _section("ok", {"bonds": bonds}, src,
                    note="转债余额/转股价关系强赎与稀释; 条款为 PIT 口径")


def _sec_announcements(ts: str, c6: str, as_of: str, name: str = "") -> dict[str, Any]:
    src = ["disclosure_report", "irm_qa"]
    out: dict[str, Any] = {}
    try:
        dr = _read_small("disclosure_report")
        f = dr[_code_mask(dr["SECURITY_CODE"], c6, ts)] if not dr.empty else pd.DataFrame()
        out["公告"] = _records(_tail(f, "ANNOUNCEMENT_DATE", 15), 15)
    except Exception as exc:  # noqa: BLE001
        out["公告"] = {"error": str(exc)[:200]}
    try:
        # irm_qa 的 SECURITY_CODE_SRC 列摄入缺陷(全为'1'), 只能按 SECURITY_NAME 精确匹配
        qa = _scan_big(
            "irm_qa",
            lambda ch: ch["SECURITY_NAME"].astype(str) == name if name else ch["SECURITY_NAME"].astype(str) == "__none__",
            usecols=["SECURITY_NAME", "QUESTION", "QUESTION_DATE", "ANSWER_CONTENT", "ANSWERER"],
            cache_id="name:" + name,
        )
        out["互动易"] = _records(_tail(qa, "QUESTION_DATE", 8), 8)
    except Exception as exc:  # noqa: BLE001
        out["互动易"] = {"error": str(exc)[:200]}
    return _section(
        "ok",
        out,
        src,
        note="disclosure_report 为30天窗口快照且覆盖不全(部分大盘股缺行); 招股书全文/补全走妙想或巨潮链接",
    )


def _sec_hot_topics(ts: str, c6: str, as_of: str, name: str = "") -> dict[str, Any]:
    """近期热度: 互动易问答量时序(月度+近30天环比) + 最新问题 + 新闻表命中(小表兜底)。
    深度舆情/研报观点走 skill 层妙想(mx_finance_search_news), 本 section 只给确定性热度原料。"""
    src = ["irm_qa", "news_market", "rss_finance"]
    out: dict[str, Any] = {}
    try:
        qa = _scan_big(
            "irm_qa",
            lambda ch: ch["SECURITY_NAME"].astype(str) == name if name else ch["SECURITY_NAME"].astype(str) == "__none__",
            usecols=["SECURITY_NAME", "QUESTION", "QUESTION_DATE"],
            cache_id="hot:" + name,
        )
        if qa.empty:
            out["互动易热度"] = {"note": "该股无互动易记录(或为沪市股, 互动易以深市为主)"}
        else:
            qd = qa["QUESTION_DATE"].astype(str).str[:10].str.replace("-", "", regex=False)  # →YYYYMMDD
            monthly = (qd.str[:6] + "-月").value_counts().sort_index()
            recent = monthly.tail(3).to_dict()
            from datetime import datetime, timedelta
            ref = datetime.strptime(as_of.replace("-", ""), "%Y%m%d")
            d0 = (ref - timedelta(days=30)).strftime("%Y%m%d")
            d1 = (ref - timedelta(days=60)).strftime("%Y%m%d")
            d30, d60 = int((qd >= d0).sum()), int(((qd >= d1) & (qd < d0)).sum())
            out["互动易热度"] = {
                "monthly_qa_last3": recent,
                "last30d_count": d30,
                "prev30d_count": d60,
                "qoq_change_pct": round((d30 - d60) / d60 * 100, 1) if d60 else None,
            }
            latest = qa.sort_values("QUESTION_DATE", ascending=False).head(5)
            out["最新互动问题"] = [
                {"date": str(r["QUESTION_DATE"]), "q": str(r["QUESTION"])[:80]}
                for _, r in latest.iterrows()
            ]
    except Exception as exc:  # noqa: BLE001
        out["互动易热度"] = {"error": str(exc)[:200]}
    hits = []
    try:
        for ds, cols in (("news_market", ["title", "description"]), ("rss_finance", ["title", "summary"])):
            nd = _read_small(ds)
            if nd.empty:
                continue
            mask = pd.Series(False, index=nd.index)
            for c in cols:
                if c in nd.columns:
                    mask |= nd[c].astype(str).str.contains(name, regex=False, na=False)
            for _, r in nd[mask].head(3).iterrows():
                hits.append({"source": ds, "date": str(r.get("publishedAt", ""))[:10],
                             "title": str(r.get("title", ""))[:100]})
    except Exception as exc:  # noqa: BLE001
        out["news_error"] = str(exc)[:150]
    out["新闻命中"] = hits if hits else {"note": "本地新闻表(155条,英文/36kr源)未命中; 全量舆情用妙想查"}
    return _section("ok", out, src)


def _sec_events(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["restricted_release", "repurchase", "semiannual_investment"]
    out: dict[str, Any] = {}
    try:
        rr = _read_small("restricted_release")
        f = rr[_code_mask(rr["SECURITY_CODE"], c6, ts)] if not rr.empty else pd.DataFrame()
        out["限售解禁"] = _records(_tail(f, "RELEASE_DATE", 8), 8)
    except Exception as exc:  # noqa: BLE001
        out["限售解禁"] = {"error": str(exc)[:200]}
    try:
        rp = _read_small("repurchase")
        out["回购"] = _records(rp[_code_mask(rp["SECURITY_CODE"], c6, ts)] if not rp.empty else rp, 4)
    except Exception as exc:  # noqa: BLE001
        out["回购"] = {"error": str(exc)[:200]}
    try:
        si = _read_small("semiannual_investment")
        out["净利润断层评分"] = _records(si[_code_mask(si["code"], c6, ts)] if not si.empty else si, 3)
    except Exception as exc:  # noqa: BLE001
        out["净利润断层评分"] = {"error": str(exc)[:200]}
    try:
        pg = _read_small("stock_pledge")
        out["股权质押"] = _records(_tail(pg[_code_mask(pg["ts_code"], c6, ts)] if not pg.empty else pg, "end_date", 3), 3)
    except Exception as exc:  # noqa: BLE001
        out["股权质押"] = {"error": str(exc)[:200]}
    return _section("ok", out, src)


def _sec_factors(ts: str, c6: str, as_of: str) -> dict[str, Any]:
    src = ["sw_factor_value", "consensus_factors"]
    out: dict[str, Any] = {}
    try:
        sw = _scan_big("sw_factor_value", lambda ch: _code_mask(ch["ts_code"], c6, ts), cache_id=ts)
        out["sw_因子(月度)"] = _records(sw, 12, "date")
    except Exception as exc:  # noqa: BLE001
        out["sw_因子(月度)"] = {"error": str(exc)[:200]}
    try:
        cf = _read_small("consensus_factors")
        f = cf[_code_mask(cf["ts_code"], c6, ts)] if not cf.empty else pd.DataFrame()
        out["一致预期因子"] = _records(f, 7, "trade_date")
    except Exception as exc:  # noqa: BLE001
        out["一致预期因子"] = {"error": str(exc)[:200]}
    return _section("ok", out, src, note="ts_因子库(202个)需 POST /fetch/factor_query 按需取, 未自动包含")


# ── public entry ───────────────────────────────────────────────────────


def build_stock_report(
    code: str,
    sections: list[str] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Resolve ``code`` and fan out across sections; never raises on data errors."""
    as_of = as_of or date.today().isoformat()
    basic = _read_small("stock_list")
    if basic.empty:
        return {"status": "error", "error": "stock_list unavailable"}
    q = code.strip().upper()
    hit = basic[_code_mask(basic["ts_code"], q.split(".")[0].zfill(6), q)]
    if hit.empty:
        return {"status": "not_found", "error": f"{code} 未通过标的验证 (stock_list)", "hint": "GET /validate?codes=" + code}
    rec = hit.iloc[0]
    ts = str(rec["ts_code"])
    c6 = ts.split(".")[0]
    wanted = [s for s in (sections or SECTION_ORDER) if s in SECTION_ORDER]
    report: dict[str, Any] = {
        "code": ts,
        "name": str(rec.get("name", "")),
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    builders = {
        "identity": _sec_identity,
        "ipo": _sec_ipo,
        "financials": _sec_financials,
        "earnings": _sec_earnings,
        "price_volume": _sec_price_volume,
        "valuation": _sec_valuation,
        "flows": _sec_flows,
        "industry": _sec_industry,
        "comps": _sec_comps,
        "index_membership": _sec_index_membership,
        "consensus": _sec_consensus,
        "research_reports": _sec_research_reports,
        "institutional_holders": _sec_institutional_holders,
        "convertible_bond": _sec_convertible_bond,
        "announcements": _sec_announcements,
        "events": _sec_events,
        "factors": _sec_factors,
    }
    list_date = str(rec.get("list_date") or "")
    try:
        days = (date.today() - datetime.strptime(list_date, "%Y%m%d").date()).days if list_date else None
    except ValueError:
        days = None
    report["is_new_stock"] = bool(days is not None and days < 365)
    stock_name = str(rec.get("name", ""))
    builders["announcements"] = lambda ts, c6, as_of: _sec_announcements(ts, c6, as_of, stock_name)
    builders["hot_topics"] = lambda ts, c6, as_of: _sec_hot_topics(ts, c6, as_of, stock_name)
    for name in wanted:
        fn = builders[name]
        try:
            report[name] = fn(ts, c6, as_of)
        except Exception as exc:  # noqa: BLE001
            report[name] = _fail(f"{type(exc).__name__}: {exc}", ["internal"])
    return report
