#!/usr/bin/env python3
"""fmdata.factor_store — Parquet 因子库统一读取接口（2026-11-05 立）。

数据底座:
  ts202 日频因子:  store/factors/ts202_parquet/<factor>/{base,delta-*}.parquet
                   长表 (trade_date int YYYYMMDD, ts_code, factor_value), 行组内按日期排序
  SW 月度三件套:   store/factors/{sw_raw_monthend_v2,sw_chip_monthend_v2,sw_neutral_monthend}.parquet
                   宽表 (date 'YYYY-MM-DD', ts_code, 因子列...)
  sw2_indrot 表:   csi1800-enhancement/data/sw2_indrot_stock_*.parquet 个股级行业轮动4列
                   宽表 (ym 'YYYYMM', ts_code, IND_ROT*) — 2026-11-06 增量, 键与三件套不同

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
    # P1 统一契约层 (DESIGN-20261106 §3; 2026-11-06 增量, 只加不改):
    fs.cross_section_by_id("cons.eps_fg_1y", "2026-10")              # 单因子截面 Series
    fs.factor_history_by_id("cjpy.PETTM", "600519.SH", allow_nonpit=True)  # 非PIT须显式放行
    fs.universe("2026-10"); fs.returns("2026-10"); fs.meta("sw2.EP"); fs.coverage("cjpy.PETTM")
"""
import glob
import inspect
import json
import os
import time

import pandas as pd
import pyarrow.parquet as pq

STORE = os.environ.get("FMDATA_FACTOR_STORE", "/data/fmdata/store/factors")

_SW_TABLES = {
    "raw": "sw_raw_monthend_v2.parquet",
    "chip": "sw_chip_monthend_v2.parquet",
    "neutral": "sw_neutral_monthend.parquet",
}

# P1 统一契约层 (DESIGN-20261106 §3, 2026-11-06 增量; 只加不改, 旧接口冻结)
CSI1800_DATA = os.environ.get(
    "FMDATA_CSI1800_DATA", "/home/ubuntu/claude-workspace/csi1800-enhancement/data")
_CJPY_DIR = os.path.join(CSI1800_DATA, "cjpy70")
_CJPY_AUDIT = os.path.join(_CJPY_DIR, "audit.log")

# 2026-11-06 IND_ROT 收尾: sw2_indrot 表映射(个股级, 绝对路径; 键=ym 非 date, 见 sw_panel 分支)。
# 纯加法——现有 raw/chip/neutral 三表行为零改动。
_SW_TABLES["sw2_indrot"] = os.path.join(CSI1800_DATA, "sw2_indrot_stock_202101_202610.parquet")

# 2026-11-14 华泰智研金工因子 (非PIT围栏层, 同 cjpy 语义; 纯加法):
#   store/factors/htsc/<slug>.parquet 长表 ym/ts_code/value, 月末截面, 取数即冻结;
#   llm_fadt 覆盖不达标(34%)仅快照积累于 htsc_pending/, 不在本命名空间。
_HTSC_DIR = os.path.join(STORE, "htsc")
_HTSC_AUDIT = os.path.join(_HTSC_DIR, "audit.log")

# 2026-11-17 RCE 六因子 (天风金工残余现金收益复现, gfclaw 冻结层; 纯加法):
#   store/factors/rce/<name>.parquet 长表 ym/ts_code/value, 月末截面(formation, 用于下月收益)。
#   PIT-safe: post_day 严格对齐 + 冻结物化, 无服务端重写 → 不设非PIT围栏。
_RCE_DIR = os.path.join(STORE, "rce")


class FactorStore:
    def __init__(self, root=STORE):
        self.root = root
        self.ts_root = os.path.join(root, "ts202_parquet")
        man_path = os.path.join(self.ts_root, "manifest.json")
        self.manifest = json.load(open(man_path)) if os.path.exists(man_path) else None
        self._sw_months_cache = {}
        self._ts_months_cache = None
        # P1 统一契约层缓存
        self._cons_path = None
        self._cons_cache = None
        self._spine_path = None
        self._returns_cache = None
        self._catalog_cache = None

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
        """SW 月度宽表切片。table∈{raw,chip,neutral,sw2_indrot}; month='YYYY-MM' 省略=全表。
        三件套键=date('YYYY-MM-DD', 月前缀匹配); sw2_indrot=个股级, 键=ym('YYYYMM' 精确匹配)。"""
        path = os.path.join(self.root, _SW_TABLES[table])
        if table == "sw2_indrot":
            if month is None:
                tbl = pq.read_table(path, columns=columns)
            else:
                ymc = str(month).replace("-", "")
                tbl = pq.read_table(path, columns=columns, filters=[("ym", "=", ymc)])
            return tbl.to_pandas().reset_index(drop=True)
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
            if table == "sw2_indrot":
                t = pq.read_table(os.path.join(self.root, _SW_TABLES[table]), columns=["ym"])
                ds = t.column("ym").to_pylist()
                self._sw_months_cache[table] = sorted({f"{d[:4]}-{d[4:6]}" for d in ds})
                return self._sw_months_cache[table]
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

    # ---------- P1 统一契约层 (DESIGN-20261106 §3; cons./cjpy. + universe/returns/meta) ----------
    # factor_id 优先入口 = cross_section_by_id / factor_history_by_id (旧 cross_section /
    # factor_history 为 ts202 专用, 签名冻结不动)。P2 起逐步把 sw./sw2./ts. 并入本层。

    def _cons_parquet(self):
        """cons7 月度 parquet 路径 (glob 前缀, 兼容未来改名/扩窗)。"""
        if self._cons_path is None:
            hits = sorted(glob.glob(os.path.join(CSI1800_DATA, "cons7_monthly_*.parquet")))
            if not hits:
                raise FileNotFoundError(f"cons7 parquet not found under {CSI1800_DATA}")
            if len(hits) > 1:
                raise RuntimeError(f"multiple cons7 parquets, ambiguous: {hits}")
            self._cons_path = hits[0]
        return self._cons_path

    def _cons_df(self):
        if self._cons_cache is None:
            self._cons_cache = pd.read_parquet(self._cons_parquet())
        return self._cons_cache

    def _nonpit_guard(self, factor_id, allow_nonpit, audit_paths=None):
        """cjpy.*/htsc.* PIT 围栏: 默认抛错; allow_nonpit=True 放行并写审计。
        audit_paths=(dir, audit_log) 缺省=cjpy; htsc 调用方传 (_HTSC_DIR, _HTSC_AUDIT)。"""
        if not allow_nonpit:
            raise PermissionError(
                f"{factor_id}: 非 PIT 因子(服务端实时计算口径, 无 as-of), 回测禁用未冻结值; "
                f"仅研究参照/ad-hoc 核对可显式传 allow_nonpit=True 放行(审计落 audit.log)")
        a_dir, a_log = audit_paths or (_CJPY_DIR, _CJPY_AUDIT)
        caller = "(unknown)"
        try:
            for fr in inspect.stack()[1:]:
                if os.path.abspath(fr.filename) != os.path.abspath(__file__):
                    caller = f"{fr.filename}:{fr.lineno}"
                    break
        except Exception:
            pass
        os.makedirs(a_dir, exist_ok=True)
        with open(a_log, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}|{factor_id}|{caller}\n")

    def _cjpy_series(self, factor, ym_raw):
        name = factor.split(".", 1)[1]
        path = os.path.join(_CJPY_DIR, f"{name}.parquet")
        if not os.path.exists(path):
            raise KeyError(f"cjpy factor not materialized: {name} (see cjpy70/manifest.json)")
        df = pd.read_parquet(path, filters=[("ym", "=", ym_raw)])
        if df.empty:
            raise KeyError(f"cjpy.{name}: no rows for ym={ym_raw} (window 202101..202610)")
        return df.set_index("ts_code")["value"].sort_index()

    def _htsc_series(self, factor, ym_raw):
        """htsc.<slug> 月末截面 → Series(index=ts_code)。长表 ym/ts_code/value, 取数即冻结。"""
        name = factor.split(".", 1)[1]
        path = os.path.join(_HTSC_DIR, f"{name}.parquet")
        if not os.path.exists(path):
            raise KeyError(f"htsc factor not materialized: {name} (see htsc/manifest.json)")
        df = pd.read_parquet(path, filters=[("ym", "=", ym_raw)])
        if df.empty:
            raise KeyError(f"htsc.{name}: no rows for ym={ym_raw} (window 202101..latest)")
        return df.set_index("ts_code")["value"].sort_index()

    def _rce_series(self, factor, ym_raw):
        """rce.<name> 月末截面 → Series(index=ts_code)。长表 ym/ts_code/value, 冻结物化,
        PIT-safe(post_day 对齐) → 无围栏。window 201712..202609。"""
        name = factor.split(".", 1)[1]
        path = os.path.join(_RCE_DIR, f"{name}.parquet")
        if not os.path.exists(path):
            raise KeyError(f"rce factor not materialized: {name} (see rce/manifest.json)")
        df = pd.read_parquet(path, filters=[("ym", "=", int(ym_raw))])   # rce ym 键=int
        if df.empty:
            raise KeyError(f"rce.{name}: no rows for ym={ym_raw} (window 201712..202609)")
        return df.set_index("ts_code")["value"].sort_index()

    @staticmethod
    def _ym_compact(ym):
        """'2026-10'|'202610' → '202610' (parquet 层原生键)。"""
        return str(ym).replace("-", "")

    def cross_section_by_id(self, factor_id, ym, allow_nonpit=False):
        """单因子单月截面 → pd.Series(index=ts_code)。
        factor_id: 'cons.<col>' | 'cjpy.<name>' | 'htsc.<slug>' | 'rce.<name>'; ym='YYYY-MM'(或 YYYYMM)。
        cjpy.*/htsc.* 默认抛 PermissionError(非PIT围栏), allow_nonpit=True 放行+审计。
        rce.* PIT-safe(冻结物化) 无围栏。"""
        ymc = self._ym_compact(ym)
        if factor_id.startswith("cons."):
            col = factor_id.split(".", 1)[1]
            df = self._cons_df()
            if col not in df.columns:
                raise KeyError(f"cons factor not found: {col} (cols={list(df.columns)})")
            sub = df[df.ym == ymc]
            if sub.empty:
                raise KeyError(f"cons.{col}: no rows for ym={ymc}")
            return sub.set_index("ts_code")[col].sort_index()
        if factor_id.startswith("cjpy."):
            self._nonpit_guard(factor_id, allow_nonpit)
            return self._cjpy_series(factor_id, ymc)
        if factor_id.startswith("htsc."):
            self._nonpit_guard(factor_id, allow_nonpit, audit_paths=(_HTSC_DIR, _HTSC_AUDIT))
            return self._htsc_series(factor_id, ymc)
        if factor_id.startswith("rce."):
            return self._rce_series(factor_id, ymc)
        raise NotImplementedError(
            f"namespace of '{factor_id}' not in P1 unified layer (cons./cjpy./htsc.); "
            f"ts/sw2 用 cross_section(month, factors) / sw_panel(table, month, columns)")

    def factor_history_by_id(self, factor_id, ts_code, allow_nonpit=False):
        """单因子单股全史 → pd.Series(index='YYYY-MM')。
        factor_id: 'cons.<col>' | 'cjpy.<name>'。cjpy.* PIT 围栏同上。"""
        if factor_id.startswith("cons."):
            col = factor_id.split(".", 1)[1]
            df = self._cons_df()
            if col not in df.columns:
                raise KeyError(f"cons factor not found: {col}")
            sub = df[df.ts_code == ts_code]
            if sub.empty:
                raise KeyError(f"cons.{col}: no rows for {ts_code}")
            s = sub.set_index("ym")[col].sort_index()
            s.index = s.index.str[:4] + "-" + s.index.str[4:]
            return s
        if factor_id.startswith("cjpy."):
            self._nonpit_guard(factor_id, allow_nonpit)
            name = factor_id.split(".", 1)[1]
            path = os.path.join(_CJPY_DIR, f"{name}.parquet")
            if not os.path.exists(path):
                raise KeyError(f"cjpy factor not materialized: {name}")
            df = pd.read_parquet(path, filters=[("ts_code", "=", ts_code)])
            if df.empty:
                raise KeyError(f"cjpy.{name}: no rows for {ts_code}")
            s = df.set_index("ym")["value"].sort_index()
            s.index = s.index.str[:4] + "-" + s.index.str[4:]
            return s
        if factor_id.startswith("htsc."):
            self._nonpit_guard(factor_id, allow_nonpit, audit_paths=(_HTSC_DIR, _HTSC_AUDIT))
            name = factor_id.split(".", 1)[1]
            path = os.path.join(_HTSC_DIR, f"{name}.parquet")
            if not os.path.exists(path):
                raise KeyError(f"htsc factor not materialized: {name}")
            df = pd.read_parquet(path, filters=[("ts_code", "=", ts_code)])
            if df.empty:
                raise KeyError(f"htsc.{name}: no rows for {ts_code}")
            s = df.set_index("ym")["value"].sort_index()
            s.index = s.index.str[:4] + "-" + s.index.str[4:]
            return s
        if factor_id.startswith("rce."):
            name = factor_id.split(".", 1)[1]
            path = os.path.join(_RCE_DIR, f"{name}.parquet")
            if not os.path.exists(path):
                raise KeyError(f"rce factor not materialized: {name} (see rce/manifest.json)")
            df = pd.read_parquet(path, filters=[("ts_code", "=", ts_code)])
            if df.empty:
                raise KeyError(f"rce.{name}: no rows for {ts_code}")
            s = df.set_index("ym")["value"].sort_index()
            s.index = s.index.astype(str).str[:4] + "-" + s.index.astype(str).str[4:]
            return s
        raise NotImplementedError(
            f"namespace of '{factor_id}' not in P1 unified layer; "
            f"ts 用 factor_history(factor, start, end)")

    def universe(self, ym):
        """CSI1800 统一网格当月股票清单 (spine: 300∪500∪1000 PIT 并集, 恰 1800 只)。"""
        ymc = self._ym_compact(ym)
        if self._spine_path is None:
            hits = sorted(glob.glob(os.path.join(CSI1800_DATA, "spine_1800_*.parquet")))
            if not hits:
                raise FileNotFoundError(f"spine parquet not found under {CSI1800_DATA}")
            if len(hits) > 1:
                raise RuntimeError(f"multiple spine parquets, ambiguous: {hits}")
            self._spine_path = hits[0]
        sp = pd.read_parquet(self._spine_path, filters=[("ym", "=", ymc)])
        if sp.empty:
            raise KeyError(f"spine has no ym={ymc} (initial window 202101..202610, cron 月度扩)")
        return sorted(sp.ts_code.tolist())

    def returns(self, ym):
        """月度收益截面 → pd.Series(index=ts_code, values=pct_chg)。ym='YYYY-MM'。
        源=store/factors/monthly_returns.csv (宇宙=历史并集, 无数据股为 NaN)。"""
        if self._returns_cache is None:
            self._returns_cache = pd.read_csv(
                os.path.join(self.root, "monthly_returns.csv"),
                dtype={"ts_code": str, "ym": str, "trade_date": str})
        ym_s = str(ym)
        ymm = ym_s if "-" in ym_s else f"{ym_s[:4]}-{ym_s[4:]}"
        sub = self._returns_cache[self._returns_cache.ym == ymm]
        if sub.empty:
            raise KeyError(f"monthly_returns has no ym={ymm}")
        s = sub.set_index("ts_code")["pct_chg"]
        return s[~s.index.duplicated(keep="last")].sort_index()

    def meta(self, factor_id):
        """catalog 元数据 (含 universe/delay 口径三元组字段 + pit_status)。"""
        if self._catalog_cache is None:
            self._catalog_cache = pd.read_csv(
                os.path.join(self.root, "factor_catalog.csv"), dtype=str).fillna("")
        row = self._catalog_cache[self._catalog_cache.factor_id == factor_id]
        if row.empty:
            raise KeyError(f"factor_id not in catalog: {factor_id} (未注册=不存在)")
        return row.iloc[0].to_dict()

    def coverage(self, factor_id=None):
        """覆盖矩阵。factor_id=None → 各层 coverage 文件路径 dict;
        'cons.X'/'cjpy.X' → pd.Series(index=ym, values=覆盖比例)。"""
        if factor_id is None:
            return {
                "cons": os.path.join(CSI1800_DATA, "cons7_monthly_coverage.csv"),
                "cjpy": os.path.join(_CJPY_DIR, "coverage.csv"),
            }
        if factor_id.startswith("cons."):
            name = factor_id.split(".", 1)[1]
            df = pd.read_csv(os.path.join(CSI1800_DATA, "cons7_monthly_coverage.csv"),
                             dtype={"ym": str})
            if name not in df.columns:
                raise KeyError(f"cons coverage has no column {name}")
            s = df.set_index("ym")[name]
        elif factor_id.startswith("cjpy."):
            name = factor_id.split(".", 1)[1]
            df = pd.read_csv(os.path.join(_CJPY_DIR, "coverage.csv"))
            col = "factor" if "factor" in df.columns else df.columns[0]
            sub = df[df[col] == name]
            if sub.empty:
                raise KeyError(f"cjpy coverage has no factor {name}")
            s = sub.drop(columns=[col]).iloc[0]
            s.index = s.index.astype(str)
        else:
            raise NotImplementedError("coverage() P1 supports cons.*/cjpy.*; ts/sw2 见 coverage_*.csv 审计产物")
        return s.astype(float).sort_index()

    # ---------- P3 统一调度器 (DESIGN §3; 2026-11-06 增量, 只加不改) ----------

    def _sw_column_series(self, name, ymm):
        """sw./sw2. 单列 → Series(index=ts_code)。列在 raw/chip/sw2_indrot 由 parquet schema 判定;
        旧 sw16 专用列(筹码中文名)不在 v2 表 → KeyError 指回 facade, 不跨库嫁接;
        sw2_indrot 为个股级 CSI1800 网格(非全A)。"""
        for table in ("raw", "chip", "sw2_indrot"):
            path = os.path.join(self.root, _SW_TABLES[table])
            if name in pq.read_schema(path).names:
                df = self.sw_panel(table, ymm, columns=["ts_code", name])
                if df.empty:
                    raise KeyError(f"sw_panel({table!r}) has no rows for ym={ymm}")
                s = df.set_index("ts_code")[name]
                return s[~s.index.duplicated(keep="last")].sort_index()
        raise KeyError(
            f"column '{name}' not in sw raw/chip/sw2_indrot 月度宽表 (sw. 行业轮动v1/旧筹码列走 "
            f"factor_query facade / sw_factor_value.csv, 勿与本层混口径)")

    def cross_section_any(self, factor_id, ym, allow_nonpit=False):
        """统一单因子单月截面 → DataFrame(ts_code, value)。按命名空间分发 (DESIGN §3 P3):
        ts.<name>   → cross_section(month, [name])   (ts202, 该月实际最后交易日)
        sw.<col> / sw2.<col> → sw_panel(raw|chip, month, [col])
        cons.<col>  → cross_section_by_id
        cjpy.<name> → cross_section_by_id (继承非PIT围栏: 默认 PermissionError,
                      allow_nonpit=True 放行+审计落 audit.log)
        htsc.<slug> → cross_section_by_id (华泰智研金工因子, 非PIT围栏同 cjpy)
        rce.<name> → cross_section_by_id (RCE六因子, PIT-safe 冻结物化无围栏)
        ym: 'YYYY-MM' 或 'YYYYMM'。"""
        ym_s = str(ym)
        ymm = ym_s if "-" in ym_s else f"{ym_s[:4]}-{ym_s[4:]}"
        if factor_id.startswith("ts."):
            name = factor_id.split(".", 1)[1]
            s = self.cross_section(ymm, [name])[name]
        elif factor_id.startswith(("sw.", "sw2.")):
            s = self._sw_column_series(factor_id.split(".", 1)[1], ymm)
        elif factor_id.startswith(("cons.", "cjpy.", "htsc.", "rce.")):
            s = self.cross_section_by_id(factor_id, ym_s, allow_nonpit=allow_nonpit)
        else:
            raise NotImplementedError(
                f"unknown namespace in '{factor_id}' (expect ts./sw./sw2./cons./cjpy./htsc./rce.)")
        out = pd.DataFrame({"ts_code": s.index.astype(str), "value": s.to_numpy()})
        return out.sort_values("ts_code", kind="mergesort").reset_index(drop=True)


def _ymd(x):
    """'2026-09-30'|'20260930'|20260930|int → int 20260930。"""
    return int(str(x).replace("-", ""))
