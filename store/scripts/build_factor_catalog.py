#!/usr/bin/env python3
"""构建统一因子目录 factor_catalog.csv —— 因子库对外统一清单（2026-11-06 与物化状态对齐）。

数据源与读取方式（除 cjpy 目录实时拉取外全部本地，无外网）：
  sw   申万金工16因子(v1 z-score) ← factors/sw_factor_value.csv 表头列（自动感知新因子列）
  sw2  申万新因子库v2 共50列      ← factors/sw_chip_monthend_v2.parquet(筹码7)
                                    + sw_raw_monthend_v2.parquet(39=细分26+风格raw10+ML3,
                                      ML列2019-02起;风格raw与v1 z版非单调换基,R57实证)
                                    + sw2_indrot_stock_*.parquet(个股级行业轮动4列,
                                      2026-11-06登记;行业级全史在factors/sw_ind_rot_monthend_v2.parquet)
  ts   tushare付费272因子(已物化) ← store/ts_factor_list_paid_20261105.csv
                                    （试用202→付费272,2026-11-05全量物化到
                                     factors/ts202_parquet/ 至2026-10-30,manifest对账）
  cons 一致预期7因子(全窗物化)    ← csi1800-enhancement cons7月度面板parquet列
                                    (2021-01→2026-10共70月;周快照csv为fallback)
  cjpy 长江天软因子(70=61物化+1损坏+8纯指数)
                                   ← csi1800-enhancement/cjpy70/manifest.json+coverage.csv
                                     (2026-11-06 P1 补登:本地确定性构建,非PIT围栏层)
  htsc 华泰智研金工因子(4=3物化+1覆盖不达标pending)
                                   ← factors/htsc/manifest.json+coverage.csv
                                     (2026-11-14 接入:月末截面取数即冻结,非PIT围栏层)
  rce  天风金工RCE六因子复现(全物化)
                                   ← factors/rce/manifest.json+coverage.csv
                                     (2026-11-17 接入:gfclaw冻结层,PIT-safe无围栏)

输出：store/factors/factor_catalog.csv
列：factor_id,factor_name,source,category_cn,frequency,format,universe,
    history_start,pit_status,query_method,description,warning
factor_id 命名空间：<source>.<factor_name>（sw.动量 / sw2.EP / ts.size / cons.eps_fg_1y / cjpy.xxx）
统一查询入口：POST /fetch/factor_query（支持 sw/ts/cons/cjpy/rce 前缀；sw2 走
fmdata.factor_store.FactorStore().sw_panel 或 GET /data/sw_*_monthend_v2 静态端点）。
原子写：tmp + os.replace。重跑全量覆盖。
"""
import json
import os
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

STORE = Path("~/fmdata/store").expanduser()
OUT_CSV = STORE / "factors/factor_catalog.csv"

SW_CSV = STORE / "factors/sw_factor_value.csv"
SW2_CHIP_PQ = STORE / "factors/sw_chip_monthend_v2.parquet"
SW2_RAW_PQ = STORE / "factors/sw_raw_monthend_v2.parquet"
TS_PAID_CSV = STORE / "ts_factor_list_paid_20261105.csv"
CONS_CSV = STORE / "fundamentals/consensus_factors.csv"
CONS7_PQ = Path("~/claude-workspace/csi1800-enhancement/data/"
                "cons7_monthly_202101_202610.parquet").expanduser()
SW2_INDROT_PQ = Path("~/claude-workspace/csi1800-enhancement/data/"
                     "sw2_indrot_stock_202101_202610.parquet").expanduser()

# sw 16 因子的类别归属（10风格+4筹码+行业轮动+GBM量价，来源 docs/sw-mcp/RUNBOOK.md）
SW_CATEGORY = {}
for _f in ["估值", "低波", "低流动性", "分析师", "动量", "反转", "市值", "成长", "盈利", "红利"]:
    SW_CATEGORY[_f] = "风格"
for _f in ["筹码成本差", "机构筹码集中度", "筹码成本", "筹码合成"]:
    SW_CATEGORY[_f] = "筹码"
SW_CATEGORY["行业轮动"] = "行业轮动"
SW_CATEGORY["GBM量价"] = "机器学习量价"

# sw v1 4 筹码列：2026-08..10 行已由 v2 筹码列替代（provenance-20261104：逐位一致+秩相关0.956）
SW_CHIP_V1 = {"筹码成本差", "机构筹码集中度", "筹码成本", "筹码合成"}

# sw2 v2 列的分类与说明（来源 sw_factor_value.provenance-20261104.md 对齐终审证书）
SW2_STYLE_RAW = {"估值", "低波", "低流动性", "分析师", "动量", "反转", "市值", "成长", "盈利", "红利"}
SW2_ML = {"GBM量价", "RF量价", "XGB量价"}

# ts 付费清单英文类别 → 中文（来源 ts_factor_list_paid_20261105.csv factor_type）
TS_CATEGORY_CN = {
    "Alpha101": "Alpha101量价", "Quality": "质量", "Liquidity": "流动性", "Risk": "风险",
    "Momentum": "动量", "Reversal": "反转", "Growth": "成长", "Value": "价值", "Size": "市值",
}

# cons 7 因子的类别（来源 consensus_factor_catalog.json）
CONS_CATEGORY = {
    "eps_fg_1y": "预期水平", "eps_fg_cagr2": "预期水平", "pe_fwd_1y": "预期水平",
    "rating_buy_ratio": "预期水平", "rating_cover": "预期水平",
    "eps_rev_3m": "预期修正", "rating_rev_1m": "预期修正",
}

FACADE = "facade: 改 recipes/factor_query.yaml 的 fetch.params 后 POST /fetch/factor_query"

rows = []


def trunc(s, n=160):
    s = " ".join(str(s).split())
    return s[:n]


# ── sw v1（z-score，含 2026-08 口径拼接警示）──
sw_factors = [c for c in pd.read_csv(SW_CSV, nrows=0).columns if c not in ("date", "ts_code")]
for f in sw_factors:
    warn = "回测前先读 docs/sw-mcp/RUNBOOK.md 的PIT说明"
    if f in SW_CHIP_V1:
        warn += ";筹码4列2026-08..10行由sw2筹码列替代(逐位一致,跨库嫁接仅筹码列安全)"
    # 2026-11-06 收官: sw v1 16/16 全部 DEPRECATED(行业轮动由 sw2.IND_ROT 组替代)
    dep = ("DEPRECATED 2026-11-06: 由sw2.IND_ROT替代,ρ=0.97(口径不同禁拼接,数据保留仅历史对照+生产过渡);"
           if f == "行业轮动" else
           "DEPRECATED 2026-11-06: sw2 同名替代(口径不同禁拼接,数据保留仅历史对照+生产过渡);")
    rows.append({
        "factor_id": f"sw.{f}", "factor_name": f, "source": "sw",
        "category_cn": SW_CATEGORY.get(f, "未分类"),
        "frequency": "monthly", "format": "wide",
        "universe": "全A含退市", "history_start": "2016-01",
        "pit_status": (dep
                      + "月末快照,滞后~T+7;PIT待隔月diff验证(未闭环);2026-08起行=新库口径基,"
                      "与<=2026-07旧z基拼接且非单调换基(R57实证),跨边界时序计算禁直用;"
                      "退市股无幸存者偏差"),
        "query_method": FACADE + " 或 GET /data/sw_factor_value(宽表)",
        "description": "申万宏源金工月度因子(z-score标准化)",
        "warning": warn,
    })


# ── sw2（新因子库 V1.62：筹码7 + raw39，月末快照，2026-11-06 起登记）──
def _sw2_entries(pq_path, table):
    """从 v2 parquet 实测列生成目录行；history_start 按各列首个非空月实算。"""
    tbl = pq.read_table(pq_path)
    date_col = "date" if "date" in tbl.column_names else "ym"
    for c in tbl.column_names:
        if c in ("date", "ym", "ts_code"):
            continue
        valid = pc.is_valid(tbl[c])
        h0 = (str(pc.min(pc.filter(tbl[date_col], valid)).as_py())[:7]
              if pc.sum(valid).as_py() else "")
        if c.startswith("CN_ASHARE_CHIP__"):
            cat = "筹码(v2)"
            desc = ("新因子库筹码原始值(原版4+ADJUSTED/INDUSTRY_MV_NEUTRAL新3共7列);"
                    "与v1筹码列秩相关0.956,跨库可直接替换(R57b实证)")
            warn = "回测前先读 docs/sw-mcp/RUNBOOK.md 的PIT说明;隔月diff验证完成前回测须注明"
        elif c in SW2_STYLE_RAW:
            cat = "风格(v2raw)"
            desc = (f"新因子库风格合成原始值(未标准化);与v1 z-score版sw.{c}非单调换基"
                    "(R57秩相关:盈利0.59/分析师0.62/低波0.79/动量0.80/估值~1.0),禁跨库混用")
            warn = ("回测前先读 docs/sw-mcp/RUNBOOK.md 的PIT说明;隔月diff验证完成前回测须注明;"
                    "2020-01键=1/23疫情前最后交易日(保持网格一致)")
            if c in ("低波", "反转"):
                warn += (";🔴合成方向与 v1 z 基相反(低波高分=高波动暴露,与IVOL20秩相关+0.82~0.88"
                         "主会话实证);v1 习惯直接移植会翻转腿")
        elif c in SW2_ML:
            cat = "机器学习量价(v2)"
            extra = ";GBM为v1同名z版的raw重算" if c == "GBM量价" else ";RF/XGB为v2新增"
            desc = f"新因子库ML量价原始值,2019-01起结构性起始(94月),用则锁2019+窗{extra}"
            warn = "回测前先读 docs/sw-mcp/RUNBOOK.md 的PIT说明;隔月diff验证完成前回测须注明"
        else:
            cat = "细分小因子(v2)"
            desc = "新因子库细分小因子原始值(EP/BP/IVOL/TURN/AMIHUD/SUE等26列,2016起全窗)"
            warn = "回测前先读 docs/sw-mcp/RUNBOOK.md 的PIT说明;隔月diff验证完成前回测须注明"
        rows.append({
            "factor_id": f"sw2.{c}", "factor_name": c, "source": "sw2",
            "category_cn": cat, "frequency": "monthly", "format": "wide",
            "universe": "全A含退市", "history_start": h0,
            "pit_status": ("月末快照(日频表月末采样,as-of当日);服务端滞后~T+7;月末快照PIT待隔月"
                           "diff验证(未闭环);cron自愈(每月4日07:37刷新+每日09:07查补)"),
            "query_method": (f"fmdata.factor_store.FactorStore().sw_panel('{table}',month,columns)"
                             f" 或 GET /data/sw_{table}_monthend_v2(宽表CSV);facade不支持sw2前缀"),
            "description": desc,
            "warning": warn,
        })


_sw2_entries(SW2_CHIP_PQ, "chip")
_sw2_entries(SW2_RAW_PQ, "raw")

# ── sw2 行业轮动 4 列（2026-11-06 收尾登记：行业级物化+个股级PIT广播，sw v1 至此 16/16 退役）──
_INDROT_DESC = {
    "IND_ROT": ("行业轮动总得分,方向=原值透传(官方正向:得分高=次月配置优先);"
                "行业级全史2017-01起118月×30申万一级(剔综合)另存 factors/sw_ind_rot_monthend_v2.parquet"),
    "IND_ROT_FUND": "行业轮动总得分的构成分项FUND(与IND_ROT同口径行业广播)",
    "IND_ROT_CAP": "行业轮动总得分的构成分项CAP(与IND_ROT同口径行业广播)",
    "IND_ROT_MOM": "行业轮动总得分的构成分项MOM(与IND_ROT同口径行业广播)",
}
for _c in ("IND_ROT", "IND_ROT_FUND", "IND_ROT_CAP", "IND_ROT_MOM"):
    _w = "sw.行业轮动(v1)已由本组替代"
    if _c in ("IND_ROT_CAP", "IND_ROT_MOM"):
        _w += ";CAP/MOM分项肥尾(absmax 8.9/10.8),总得分/FUND符合z量级假设而分项不符合"
    rows.append({
        "factor_id": f"sw2.{_c}", "factor_name": _c, "source": "sw2",
        "category_cn": "行业轮动(v2)", "frequency": "monthly", "format": "wide-panel",
        "universe": "CSI1800月度网格~1800只/月(行业级=30申万一级剔综合)",
        "history_start": "2021-01",
        "pit_status": ("月末评分月初发布(D+2~D+4可得);历史重拉diff≤4.4e-16已验证不可变;"
                       "个股级=行业分按申万一级行业PIT广播(95.3%逐月归属+3.4%pts窗口+1.2%carry)"),
        "query_method": (f"parquet: {SW2_INDROT_PQ}(ym/ts_code/4得分列);"
                         "fmdata.factor_store.FactorStore().sw_panel('sw2_indrot',month,columns)"
                         f" 或 cross_section_any('sw2.{_c}',ym)"),
        "description": _INDROT_DESC[_c],
        "warning": _w,
    })

# ── ts（试用202→付费272，2026-11-05 全量物化；源=付费清单）──
ts_paid = pd.read_csv(TS_PAID_CSV)
for _, f in ts_paid.iterrows():
    rows.append({
        "factor_id": f"ts.{f['factor_name']}", "factor_name": f["factor_name"], "source": "ts",
        "category_cn": TS_CATEGORY_CN.get(str(f["factor_type"]), str(f["factor_type"])),
        "frequency": "daily", "format": "long",
        "universe": "全A~5500(财务比率类剔除金融~113只)",
        "history_start": "2016-01-29(tushare原生2010起,物化窗口)",
        "pit_status": ("已转付费(2026-11-05),全量物化至2026-10-30(factors/ts202_parquet/ 272因子,"
                       "manifest对账);口径混合:部分[0,1]排名标准化/部分原始值,逐因子核对"),
        "query_method": ("fmdata.factor_store.FactorStore().factor_history(<name>) 或 "
                         "factors/ts202_parquet/<name>/(base+delta);facade ts.*仍可按需直拉tushare"),
        "description": trunc(f.get("factor_desc", "")),
        "warning": "限频~1次/秒;code后缀勿写错(.SH/.SZ)",
    })

# ── cons（7因子全窗月度面板已物化，2026-11-06 起目录指向 parquet）──
if CONS7_PQ.exists():
    cons_factors = [c for c in pq.read_schema(CONS7_PQ).names if c not in ("ym", "ts_code")]
else:  # parquet 缺失时退回周快照长表注册名，目录仍可构建
    cons_factors = sorted(set(pd.read_csv(CONS_CSV, usecols=["factor_name"])["factor_name"]))
CONS_RATING = {"rating_buy_ratio", "rating_cover", "rating_rev_1m"}  # 评级类(2026-11-06部分回填)
for f in cons_factors:
    warn = "pe_fwd_1y价格与EPS快照日可能差1-3天"
    if f in ("rating_buy_ratio", "rating_cover"):
        warn = ("全窗(2026-11-06 report_rc 回填,重叠月均ρ=0.838/0.965);"
                "2021-01..2026-05=report_rc聚合(6m窗,机构数,券商刻度归一),2026-06起=周快照真值;"
                "两聚合器等级一致非逐位相等")
    elif f == "rating_rev_1m":
        warn = ("评级类因子仅202605-202610六个月历史(rating_rev_1m仅202606起,结构性缺口),"
                "禁用于长窗回测;report_rc 复现失败(ρ=0.255,微差信号SNR<1)")
    rows.append({
        "factor_id": f"cons.{f}", "factor_name": f, "source": "cons",
        "category_cn": CONS_CATEGORY.get(f, "预期"),
        "frequency": "monthly", "format": "wide-panel",
        "universe": "CSI1800月度网格~1800只/月(累计2678只)",
        "history_start": "2021-01",
        "pit_status": ("月度面板全窗物化2021-01→2026-10共70月(对账diff=0);快照日as-of;"
                       "增速类要求基期EPS>0;口径详见 recipes/consensus_factors.yaml"),
        "query_method": (f"parquet: {CONS7_PQ}(ym+ts_code+7因子宽表);"
                         "facade cons.*仍指周快照长表GET /data/consensus_factors"),
        "description": "分析师一致预期因子(预期水平5+预期修正2),月度formation口径",
        "warning": warn,
    })

# ── cjpy（P1 补登 2026-11-06：61 已物化 + 停牌时长 server-broken + 8 纯指数不适用）──
# 源=cjpy70/manifest.json+coverage.csv（本地确定性构建，不再实时拉 list_factors）
CJPY_DIR = Path("~/claude-workspace/csi1800-enhancement/data/cjpy70").expanduser()
CJPY_PIT = ("非PIT/服务端实时计算口径,回测禁用未冻结值,仅研究参照与ad-hoc核对(同妙想规则);"
            "独立层,不与sw/ts/cons同义列续接时序")
cjpy_man = json.load(open(CJPY_DIR / "manifest.json"))
_cjpy_cov = pd.read_csv(CJPY_DIR / "coverage.csv")
_ccol = "factor" if "factor" in _cjpy_cov.columns else _cjpy_cov.columns[0]
_cjpy_cov = _cjpy_cov.set_index(_ccol)
for _ent in cjpy_man["factors"]:
    _fname = _ent["factor"]
    if _ent.get("months_done", 0) > 0:  # 已物化（61 个，70/70 月）
        _cov_desc = ""
        if _fname in _cjpy_cov.index:
            _cr = pd.to_numeric(_cjpy_cov.loc[_fname], errors="coerce")
            _cov_desc = f";覆盖min{_cr.min():.4f}/mean{_cr.mean():.3f}(70月)"
        rows.append({
            "factor_id": f"cjpy.{_fname}", "factor_name": _fname, "source": "cjpy",
            "category_cn": "天软因子(已物化)",
            "frequency": "monthly", "format": "long-parquet",
            "universe": "CSI1800月度网格~1800只/月(累计2678只)",
            "history_start": "2021-01",
            "pit_status": CJPY_PIT,
            "query_method": (f"fmdata.factor_store.FactorStore().cross_section_by_id('cjpy.{_fname}',"
                             f"ym,allow_nonpit=True)(默认抛错=PIT围栏,放行记audit.log) 或 "
                             f"{CJPY_DIR}/{_fname}.parquet(ym/ts_code/value)"),
            "description": trunc(f"公式:{_ent.get('formula','')};dtype={_ent.get('dtype','')}{_cov_desc}"),
            "warning": ("服务端实时计算无as-of(财务类用最新修订值,前视风险显著);"
                        "单位为天软原生(量/额/股本/市值未换算);Amihud=1e8×|涨跌幅|/成交额;"
                        "FactorStore PIT围栏默认抛错"),
        })
    else:  # server-broken：停牌时长（70/70 格 FAIL，确定性公式损坏）
        _broken_note = next((b for b in cjpy_man.get("server_broken", []) if _fname in b), "")
        rows.append({
            "factor_id": f"cjpy.{_fname}", "factor_name": _fname, "source": "cjpy",
            "category_cn": "天软因子(server-broken)",
            "frequency": "server", "format": "none",
            "universe": "—", "history_start": "",
            "pit_status": CJPY_PIT + ";服务端公式损坏不可得",
            "query_method": "无(70/70格FAIL,确定性编译错,不做重试)",
            "description": trunc(f"公式:{_ent.get('formula','')};{_broken_note}"),
            "warning": trunc(_broken_note or "服务端公式损坏(2026-11-06 probe)"),
        })
for _idx_f, _reason in cjpy_man.get("excluded_factors", {}).items():  # 8 纯指数因子
    rows.append({
        "factor_id": f"cjpy.{_idx_f}", "factor_name": _idx_f, "source": "cjpy",
        "category_cn": "天软因子(不适用)",
        "frequency": "server", "format": "query",
        "universe": "纯指数(非个股)", "history_start": "",
        "pit_status": CJPY_PIT,
        "query_method": FACADE + "(指数口径,需同时给 trade_date 和 ts_code;未物化)",
        "description": trunc(_reason),
        "warning": "纯指数因子,不适用CSI1800个股面板,未物化",
    })

# ── htsc（华泰智研金工因子 2026-11-14 接入：3 物化 + 1 覆盖不达标 pending）──
# 源=factors/htsc/manifest.json+coverage.csv（本地确定性构建, 月末截面取数即冻结）。
# 准入门 2026-11-14: 深度>=24月 且 CSI1800覆盖>=95% (截面 2026-10-30 vs spine 202610)。
HTSC_DIR = STORE / "factors/htsc"
HTSC_PIT = ("non_pit_unverified:服务端为研报衍生日频序列,无as-of口径,历史可能重写;"
            "本地月末截面取数即冻结(已有ym键永不重写);回测禁用,仅研究参照/ad-hoc核对"
            "(同cjpy围栏,FactorStore默认抛错,allow_nonpit=True放行+审计)")
if (HTSC_DIR / "manifest.json").exists():
    htsc_man = json.load(open(HTSC_DIR / "manifest.json"))
    _htsc_cov = pd.read_csv(HTSC_DIR / "coverage.csv", dtype={"ym": str}) \
        if (HTSC_DIR / "coverage.csv").exists() else pd.DataFrame()
    for _ent in [*htsc_man.get("factors", []), *htsc_man.get("pending", [])]:
        _fname = _ent["factor"]
        if _ent.get("materialized", True) and _ent.get("months_done", 0) > 0:
            _cov_desc = ""
            if not _htsc_cov.empty and (_htsc_cov.factor == _fname).any():
                _cr = pd.to_numeric(_htsc_cov.loc[_htsc_cov.factor == _fname, "coverage"],
                                    errors="coerce").dropna()
                if len(_cr):
                    _cov_desc = f";CSI1800覆盖min{_cr.min():.4f}/mean{_cr.mean():.3f}"
            rows.append({
                "factor_id": f"htsc.{_fname}", "factor_name": _fname, "source": "htsc",
                "category_cn": "华泰金工因子(已物化)",
                "frequency": "monthly", "format": "long-parquet",
                "universe": "全A~5100(含北交所;月末截面)",
                "history_start": _ent.get("materialized_start", "2021-01"),
                "pit_status": HTSC_PIT,
                "query_method": (f"fmdata.factor_store.FactorStore().cross_section_any('htsc.{_fname}',"
                                 f"ym,allow_nonpit=True)(默认抛错=非PIT围栏,放行记audit.log) 或 "
                                 f"{HTSC_DIR}/{_fname}.parquet(ym/ts_code/value)"),
                "description": trunc(f"华泰智研MCP金工因子[{_ent.get('cn_name','')}],"
                                     f"get_quant_factor月末最后交易日截面;"
                                     f"server史{ _ent.get('server_history_start','') }起,本地物化窗"
                                     f"{_ent.get('materialized_start','')}..{_ent.get('materialized_end','')}"
                                     f"共{_ent.get('months_done',0)}月;准入{_ent.get('gate','')}{_cov_desc}"),
                "warning": ("PIT未验证(服务端可能重写历史,禁直接回测);月末截面口径;"
                            "覆盖北交所行读取时由universe自然过滤"),
            })
        else:  # 覆盖不达标 → 仅日度快照积累(factors/htsc_pending/), 不进 FactorStore
            rows.append({
                "factor_id": f"htsc.{_fname}", "factor_name": _fname, "source": "htsc",
                "category_cn": "华泰金工因子(pending快照)",
                "frequency": "daily", "format": "snapshot",
                "universe": "部分个股(~840只)", "history_start": "",
                "pit_status": HTSC_PIT,
                "query_method": (f"仅快照积累: factors/htsc_pending/{_fname}/<YYYYMMDD>.parquet"
                                 "(取数即冻结,cron日度);未接入FactorStore"),
                "description": trunc(f"华泰智研MCP金工因子[{_ent.get('cn_name','')}],"
                                     f"准入{_ent.get('gate','')};server史"
                                     f"{_ent.get('server_history_start','')}起"),
                "warning": trunc(f"覆盖不达标未接入({_ent.get('gate','')});快照仅研究参照"),
            })

# ── rce（天风金工RCE六因子复现 2026-11-17 接入：6 全物化）──
# 源=factors/rce/manifest.json+coverage.csv（gfclaw冻结层本地确定性构建, 月末截面）。
# PIT-safe: post_day 严格对齐 + 冻结物化(取数即冻结), 无服务端重写 → 不设非PIT围栏。
RCE_DIR = STORE / "factors/rce"
RCE_PIT = ("pit_safe:报表冻结层(ticker,cutoff)唯一+首次公告日post_day严格as-of对齐,"
           "本地物化取数即冻结;formation月末截面用于下月收益;回测可直接使用")
if (RCE_DIR / "manifest.json").exists():
    rce_man = json.load(open(RCE_DIR / "manifest.json"))
    _rce_cov = pd.read_csv(RCE_DIR / "coverage.csv", dtype={"ym": str}) \
        if (RCE_DIR / "coverage.csv").exists() else pd.DataFrame()
    for _ent in rce_man.get("factors", []):
        _fname = _ent["factor"]
        _cov_desc = ""
        if not _rce_cov.empty and (_rce_cov.factor == _fname).any():
            _cr = pd.to_numeric(_rce_cov.loc[_rce_cov.factor == _fname, "coverage"],
                                errors="coerce").dropna()
            if len(_cr):
                _cov_desc = f";elig覆盖min{_cr.min():.3f}/mean{_cr.mean():.3f}"
        rows.append({
            "factor_id": f"rce.{_fname}", "factor_name": _fname, "source": "rce",
            "category_cn": "天风RCE复现因子(已物化)",
            "frequency": "monthly", "format": "long-parquet",
            "universe": "全A剔次新/ST/停牌/北交所(金融含,因子值自然缺失)",
            "history_start": _ent.get("materialized_start", "201712"),
            "pit_status": RCE_PIT,
            "query_method": (f"fmdata.factor_store.FactorStore().cross_section_any('rce.{_fname}',"
                             f"ym)(PIT-safe无围栏) 或 POST /fetch/factor_query "
                             f"{{'params':{{'factor_id':'rce.{_fname}','trade_date':'YYYYMMDD'}}}}"),
            "description": trunc(f"天风金工RCE残余现金收益框架复现[{_ent.get('cn_name','')}],"
                                 f"gfclaw冻结层;"
                                 f"物化窗{_ent.get('materialized_start','')}..{_ent.get('materialized_end','')}"
                                 f"共{_ent.get('months_done',0)}月/{_ent.get('rows',0)}行;"
                                 f"{_ent.get('gate','')}{_cov_desc};"
                                 f"五子因子=raw水平(回测端去极值+标准化+中信一级+lnMV中性化),"
                                 f"composite=三腿中性化残差均值+z(终值)"),
            "warning": ("早期覆盖低(2018~61%,gfclaw证券id迁移致2019前多数股票报表挂内部id),"
                        "继承自复现基线;U因子含Q4单季FY公告前近似(1个月内边缘);"
                        "ST剔除用当前名单全期(复现简化)"),
        })

df = pd.DataFrame(rows, columns=[
    "factor_id", "factor_name", "source", "category_cn", "frequency", "format",
    "universe", "history_start", "pit_status", "query_method", "description", "warning",
])
dup = df["factor_id"].duplicated().sum()
if dup:
    print(f"[warn] {dup} duplicated factor_id")

OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
tmp = OUT_CSV.with_suffix(".csv.tmp")
df.to_csv(tmp, index=False)
os.replace(tmp, OUT_CSV)
print(f"factor_catalog: {len(df)} factors "
      f"(sw={sum(1 for r in rows if r['source']=='sw')}, "
      f"sw2={sum(1 for r in rows if r['source']=='sw2')}, "
      f"ts={sum(1 for r in rows if r['source']=='ts')}, "
      f"cons={sum(1 for r in rows if r['source']=='cons')}, "
      f"cjpy={sum(1 for r in rows if r['source']=='cjpy')}, "
      f"htsc={sum(1 for r in rows if r['source']=='htsc')}, "
      f"rce={sum(1 for r in rows if r['source']=='rce')}) -> {OUT_CSV}")
