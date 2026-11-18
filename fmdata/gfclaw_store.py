"""gfclaw COS 数仓查询层。

读取 /data/gfclaw/local_market_data（腾讯 COS 同步的 Parquet 数仓，91 表，
A股/港股/美股/基金/转债/宏观，月度分区 1990→）。

分区结构: <table>/period=YYYY-MM/*.parquet（hive 风格）+ <table>/snapshot/。
特殊分区: pre2020-*（2020 前历史整段打包）、undated——始终扫描，行级过滤兜底。

PIT 铁律:
- period < 当月 = 冻结分区（history/ 归档，不可变）→ 回测可用
- period >= 当月 = current 层（当月整月每日重刷，未定型）→ 禁入回测终值
- frozen_only=true 强制只读冻结分区（机械 PIT 围栏）
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as pads

GFCLAW_ROOT = Path("/data/gfclaw/local_market_data")
DICT_SNAPSHOT = GFCLAW_ROOT / "market_data_dictionary" / "snapshot" / "part-current.parquet"

# 与 server.py 一致的输出上限（2026-09 OOM 教训：>上限序列化会拖死宿主）
MAX_ROWS = 200_000
MAX_CELLS = 10_000_000

# 行级日期列自动识别优先级（字段字典的 incremental_col 优先于本清单）
_DATE_COL_PRIORITY = (
    "mark_day", "post_day", "cutoff_day", "sample_day", "active_from",
    "alert_day", "suspend_day", "shift_day", "calendar_day", "trade_day",
)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _current_month() -> str:
    return dt.date.today().strftime("%Y-%m")


def _dict_df() -> pd.DataFrame:
    if not DICT_SNAPSHOT.exists():
        raise FileNotFoundError(
            f"gfclaw 字典快照缺失: {DICT_SNAPSHOT}（先跑 /data/gfclaw/scripts/sync_from_cos.py）"
        )
    return pd.read_parquet(DICT_SNAPSHOT)


def table_names() -> list[str]:
    if not GFCLAW_ROOT.exists():
        return []
    return sorted(p.name for p in GFCLAW_ROOT.iterdir() if p.is_dir() and not p.name.startswith("_"))


def tables_detail() -> list[dict[str, Any]]:
    """表清单 + 字段 + 分区概况（字典驱动，供 /gfclaw/tables）。"""
    d = _dict_df()
    active = d[d["is_active"].astype(bool)] if "is_active" in d.columns else d
    out = []
    for name in table_names():
        row = {"table": name}
        sub = active[active["table_name"] == name]
        if not sub.empty:
            sub = sub.sort_values("field_order")
            row["description"] = str(sub.iloc[0].get("table_description", "") or "")
            row["incremental_col"] = str(sub.iloc[0].get("incremental_col", "") or "")
            row["fields"] = {str(r.field_name): str(r.field_label or r.field_name) for r in sub.itertuples()}
        parts = _partitions(name)
        months = [p for p in parts if _MONTH_RE.match(p)]
        row["partitions"] = {
            "total": len(parts),
            "months": f"{min(months)}~{max(months)}" if months else "",
            "special": [p for p in parts if not _MONTH_RE.match(p)],
            "has_snapshot": "snapshot" in parts or (GFCLAW_ROOT / name / "snapshot").exists(),
            "current_month_present": _current_month() in parts,
        }
        out.append(row)
    return out


def _partitions(table: str) -> list[str]:
    tdir = GFCLAW_ROOT / table
    if not tdir.is_dir():
        return []
    names = []
    for p in tdir.iterdir():
        if p.is_dir() and p.name.startswith("period="):
            names.append(p.name[len("period="):])
    return sorted(names)


def _pick_date_col(schema_names: list[str], hint: str | None) -> str | None:
    for cand in ([hint] if hint else []) + list(_DATE_COL_PRIORITY):
        if cand in schema_names:
            return cand
    return next((c for c in schema_names if c.endswith("_day")), None)


def query(
    table: str,
    columns: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    ticker_id: str | int | None = None,
    frozen_only: bool = False,
    limit: int = 1000,
    date_col: str | None = None,
) -> dict[str, Any]:
    """查询一张 gfclaw 表。日期过滤走分区裁剪 + 行级过滤双层。

    start/end: YYYY-MM-DD（也接受 YYYY-MM）。ticker_id 为内部主键
    （映射见 eq_security_profile / eq_hk_security_profile 等表）。
    """
    tdir = GFCLAW_ROOT / table
    if not tdir.is_dir():
        raise FileNotFoundError(f"gfclaw 无此表: {table}（清单见 /gfclaw/tables）")

    parts = _partitions(table)
    cur = _current_month()
    start_m = (start or "")[:7]
    end_m = (end or "")[:7]
    selected: list[str] = []
    for p in parts:
        if not _MONTH_RE.match(p):      # pre2020-* / undated 永远纳入，行级过滤兜底
            selected.append(p)
            continue
        if start_m and p < start_m:
            continue
        if end_m and p > end_m:
            continue
        if frozen_only and p >= cur:
            continue
        selected.append(p)

    srcs = [tdir / f"period={p}" for p in selected]
    snap = tdir / "snapshot"
    if snap.is_dir():
        srcs.append(snap)
    srcs = [s for s in srcs if s.is_dir()]
    # pyarrow 24: 目录列表+hive 分区不支持 → 显式文件清单（分区裁剪已由目录选择完成）
    files = sorted(str(f) for s in srcs for f in s.rglob("*.parquet") if f.name != "_manifest.parquet")
    if not files:
        note = "无匹配分区"
        if frozen_only and _current_month() in parts:
            note += f"（frozen_only 围栏挡掉了当月分区 {_current_month()}：current 层每日重刷未定型，禁入回测；去掉 frozen_only 可查当月活数据）"
        if not srcs:
            note += "（检查 start/end 范围）"
        return {"rows": 0, "data": [], "note": note}

    dataset = pads.dataset(files, format="parquet")
    schema_names = dataset.schema.names

    dcol = _pick_date_col(schema_names, date_col)
    hard_limit = min(int(limit), MAX_ROWS)

    if not (start or end) and ticker_id is None:
        # 无过滤快路径：head 直取，避免大表(机构持仓114分区)全量物化 120s+ 超时
        table_arrow = dataset.head(hard_limit)
        total_rows = None
    else:
        # 行/列过滤下推到 parquet scanner(2026-11-18 事故修复):
        # 旧实现 dataset.to_table() 先全量物化再内存 mask——大表(ana_rolling 9.1G /
        # ana_snap 2.3G)无 start 查询会把 RSS 顶穿 cgroup memory.high,throttle
        # 风暴致全服务假死(/status 也无响应),只能重启。scanner 下推让 IO 层
        # 按 row-group 统计跳过无关数据块,列投影避免全列读取;语义与旧 mask
        # 完全等价(同 cast 字符串后比较)。
        expr = None
        if dcol and (start or end):
            fld = pads.field(dcol).cast("string")
            if start:
                expr = fld >= pads.scalar(start)
            if end:
                m2 = fld <= pads.scalar(end)
                expr = m2 if expr is None else expr & m2
        if ticker_id is not None and "ticker_id" in schema_names:
            m3 = pads.field("ticker_id").cast("string") == pads.scalar(str(ticker_id))
            expr = m3 if expr is None else expr & m3
        proj = None
        if columns:
            _cols = [c for c in columns if c in schema_names]
            if _cols:
                proj = _cols + ([dcol] if dcol and dcol not in _cols else [])
        table_arrow = dataset.scanner(columns=proj, filter=expr).to_table()
        total_rows = table_arrow.num_rows

    if columns:
        cols = [c for c in columns if c in schema_names]
        if cols:
            table_arrow = table_arrow.select(cols + ([dcol] if dcol and dcol not in cols else []))
    elif dcol and dcol in table_arrow.column_names and len(table_arrow.column_names) > 40:
        # 宽表默认裁剪：日期列 + 前 39 列，防止 10M cell 上限秒爆
        table_arrow = table_arrow.select(table_arrow.column_names[:40])

    df = table_arrow.slice(0, hard_limit).to_pandas()
    if df.size > MAX_CELLS:
        shrink = max(1, MAX_CELLS // df.shape[1])
        df = df.iloc[:shrink]

    return {
        "rows": int(len(df)),
        "total_matched": int(total_rows) if total_rows is not None else -1,
        "truncated": bool(total_rows is None or total_rows > len(df)),
        "date_col": dcol,
        "partitions_scanned": selected,
        "frozen_only": frozen_only,
        "columns": list(df.columns),
        "data": df.to_dict(orient="records"),
    }
