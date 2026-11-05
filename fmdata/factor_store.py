#!/usr/bin/env python3
"""fmdata.factor_store — Parquet 因子库统一读取接口（2026-11-05 立）。

数据底座:
  ts202 日频因子:  store/factors/ts202_parquet/<factor>/{base,delta-*}.parquet
                   长表 (trade_date int YYYYMMDD, ts_code, factor_value), 行组内按日期排序
  SW 月度三件套:   store/factors/{sw_raw_monthend_v2,sw_chip_monthend_v2,sw_neutral_monthend}.parquet
                   宽表 (date 'YYYY-MM-DD', ts_code, 因子列...)

月键归一: 调用方一律用 "YYYY-MM"; 内部解析为各源的真实键
  (raw=日历月末字符串 / chip=实际交易日字符串 / ts=该月实际交易日 int 区间)。

增量语义: Parquet 文件不可追加 → 增量=同目录新增 delta 文件 + manifest 记账,
本接口按目录多文件透明合并(键去重 keep=last)。

用法:
    from fmdata.factor_store import FactorStore
    fs = FactorStore()
    fs.cross_section("2026-09", ["alpha101_44", "fcf_to_market"])   # 单月截面(宽)
    fs.factor_history("alpha101_44")                                  # 单因子全史(长)
    fs.sw_panel("raw", month="2026-09", columns=["EP","BP"])          # SW月度宽表切片
    fs.common_months()                                                # 回测 formation 网格
"""
import json
import os

import pandas as pd
import pyarrow.parquet as pq

STORE = os.environ.get("FMDATA_FACTOR_STORE", "/data/fmdata/store/factors")

_SW_TABLES = {
    "raw": "sw_raw_monthend_v2.parquet",
    "chip": "sw_chip_monthend_v2.parquet",
    "neutral": "sw_neutral_monthend.parquet",
}


class FactorStore:
    def __init__(self, root=STORE):
        self.root = root
        self.ts_root = os.path.join(root, "ts202_parquet")
        man_path = os.path.join(self.ts_root, "manifest.json")
        self.manifest = json.load(open(man_path)) if os.path.exists(man_path) else None
        self._sw_months_cache = {}
        self._ts_months_cache = None

    # ---------- ts202 日频因子 ----------

    def ts_factors(self):
        """可用因子清单（manifest 键）。"""
        return sorted(self.manifest["factors"]) if self.manifest else []

    def factor_history(self, factor, start=None, end=None):
        """单因子全史长表 [trade_date(int YYYYMMDD), ts_code, factor_value]。
        start/end: 'YYYYMMDD' 或 'YYYY-MM-DD' 或 int。"""
        rec = self.manifest["factors"][factor]
        parts = []
        for fn in rec["files"]:
            tbl = pq.read_table(os.path.join(self.ts_root, factor, fn),
                                columns=["trade_date", "ts_code", "factor_value"])
            parts.append(tbl.to_pandas())
        df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
        if start is not None:
            df = df[df.trade_date >= _ymd(start)]
        if end is not None:
            df = df[df.trade_date <= _ymd(end)]
        # base+delta 键去重(防御, 正常增量不重键)
        df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
        return df.sort_values(["trade_date", "ts_code"], kind="mergesort").reset_index(drop=True)

    def cross_section(self, month, factors):
        """单月截面宽表: index=ts_code, columns=factors, values=factor_value。
        month='YYYY-MM'; 取该月 ≤月末的最后一批 trade_date。"""
        lo = int(month.replace("-", "") + "01")
        hi = int(month.replace("-", "") + "31")
        cols = {}
        for f in factors:
            rec = self.manifest["factors"][f]
            parts = []
            for fn in rec["files"]:
                tbl = pq.read_table(os.path.join(self.ts_root, f, fn),
                                    columns=["trade_date", "ts_code", "factor_value"],
                                    filters=[("trade_date", ">=", lo), ("trade_date", "<=", hi)])
                parts.append(tbl.to_pandas())
            df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
                columns=["trade_date", "ts_code", "factor_value"])
            if df.empty:
                cols[f] = pd.Series(dtype="float64", name=f)
                continue
            last_day = df.trade_date.max()   # 该月实际交易日(长尾防回看)
            df = df[df.trade_date == last_day].drop_duplicates(subset="ts_code", keep="last")
            cols[f] = df.set_index("ts_code")["factor_value"].rename(f)
        out = pd.DataFrame(cols)
        return out

    def ts_months(self):
        """ts202 覆盖的月份清单 ['YYYY-MM', ...]（实际数据推导, 实例内缓存）。"""
        if self._ts_months_cache is None:
            s = set()
            for f in self.ts_factors():
                for d in self._factor_dates(f):
                    s.add(f"{d//100:06d}"[:4] + "-" + f"{d//100:06d}"[4:6])
            self._ts_months_cache = sorted(s)
        return self._ts_months_cache

    def _factor_dates(self, factor):
        rec = self.manifest["factors"][factor]
        ds = set()
        for fn in rec["files"]:
            t = pq.read_table(os.path.join(self.ts_root, factor, fn), columns=["trade_date"])
            ds |= set(t.column("trade_date").to_pylist())
        return ds

    # ---------- SW 月度三件套 ----------

    def sw_panel(self, table, month=None, columns=None):
        """SW 月度宽表切片。table∈{raw,chip,neutral}; month='YYYY-MM' 省略=全表。"""
        path = os.path.join(self.root, _SW_TABLES[table])
        if month is None:
            tbl = pq.read_table(path, columns=columns)
        else:
            lo, hi = f"{month}-01", f"{month}-31"   # 月前缀匹配, 兼容月末/实际交易日两种键
            tbl = pq.read_table(path, columns=columns,
                                filters=[("date", ">=", lo), ("date", "<=", hi)])
        return tbl.to_pandas().reset_index(drop=True)

    def sw_months(self, table):
        """SW 表覆盖月份 ['YYYY-MM', ...]。"""
        if table not in self._sw_months_cache:
            t = pq.read_table(os.path.join(self.root, _SW_TABLES[table]), columns=["date"])
            ds = t.column("date").to_pylist()
            self._sw_months_cache[table] = sorted({d[:7] for d in ds})
        return self._sw_months_cache[table]

    # ---------- 回测网格 ----------

    def common_months(self, tables=("raw", "chip")):
        """SW 表与 ts202 的月份交集 → 回测 formation 网格。"""
        s = set(self.ts_months())
        for t in tables:
            s &= set(self.sw_months(t))
        return sorted(s)


def _ymd(x):
    """'2026-09-30'|'20260930'|20260930|int → int 20260930。"""
    return int(str(x).replace("-", ""))
