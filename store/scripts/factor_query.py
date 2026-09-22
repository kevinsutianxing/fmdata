#!/usr/bin/env python3
"""统一因子查询门面 —— 单入口取任意源因子，输出统一长表。

用法（同 ts_factor_value 的既有模式）：
  1. 改 recipes/factor_query.yaml 的 fetch.params：
     factor_id  必填，形如 sw.动量 / ts.alpha101_1 / cons.eps_fg_1y / cjpy.<名>
     trade_date 选填 YYYYMMDD（cjpy 源必填）
     ts_code    选填（cjpy 源必填；ts 源 ts_code/trade_date 至少一个）
  2. POST /fetch/factor_query
  3. GET /data/factor_query → 长表 factor_name,ts_code,trade_date,factor_value
  全目录（含各因子口径/PIT警告）：GET /data/factor_catalog

路由：
  sw.*   ← 本地宽表切片（139MB，usecols 按需读列，日期归一化为 YYYYMMDD）
  cons.* ← 本地长表过滤
  ts.*   ← tushare factor_value 按需直拉（生产脚本上下文，token 走服务 env，
           同 fetch_index_weight_000300 模式；09-14 实测限频宽松 ~6次/s，真瓶颈=试用态每日约1000次）
  cjpy.* ← cjpy.get_factor_data（天软服务端因子，code+date 必填）
"""
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

STORE = Path("~/fmdata/store").expanduser()
RECIPE = STORE / "recipes/factor_query.yaml"
OUT_CSV = STORE / "factors/factor_query.csv"
SW_CSV = STORE / "factors/sw_factor_value.csv"
CONS_CSV = STORE / "fundamentals/consensus_factors.csv"


def die(msg):
    print(f"[factor_query] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def norm_date(d):
    return str(d).replace("-", "").strip()


# ── 读参数（agent recipe 自读 yaml，params 即查询请求）──
cfg = yaml.safe_load(open(RECIPE))
params = (cfg.get("fetch") or {}).get("params") or {}
factor_id = str(params.get("factor_id") or "").strip()
trade_date = norm_date(params.get("trade_date") or "")
ts_code = str(params.get("ts_code") or "").strip()
if "." not in factor_id:
    die(f"factor_id 格式应为 <source>.<name>，当前: {factor_id!r}；目录见 /data/factor_catalog")
src, name = factor_id.split(".", 1)

out = None

if src == "sw":
    cols = ["date", "ts_code", name]
    try:
        df = pd.read_csv(SW_CSV, usecols=cols)
    except ValueError as e:
        die(f"sw 因子不存在或读列失败: {e}；可用因子见 /data/factor_catalog source=sw")
    if trade_date:
        df = df[df["date"].astype(str).str.replace("-", "") == trade_date]
    if ts_code:
        df = df[df["ts_code"] == ts_code]
    out = pd.DataFrame({
        "factor_name": name,
        "ts_code": df["ts_code"],
        "trade_date": df["date"].astype(str).str.replace("-", ""),
        "factor_value": df[name],
    }).dropna(subset=["factor_value"])

elif src == "cons":
    df = pd.read_csv(CONS_CSV, dtype={"trade_date": str})
    df = df[df["factor_name"] == name]
    if trade_date:
        df = df[df["trade_date"] == trade_date]
    if ts_code:
        df = df[df["ts_code"] == ts_code]
    out = df

elif src == "ts":
    import tushare as ts
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()
    kwargs = {"factor_name": name}
    if trade_date:
        kwargs["trade_date"] = trade_date
    if ts_code:
        kwargs["ts_code"] = ts_code
    if len(kwargs) == 1:
        die("ts 源需要 trade_date 或 ts_code 至少一个（全历史全市场太大）")
    out = pro.factor_value(**kwargs)
    if out is None or out.empty:
        die(f"tushare factor_value 返回空: {kwargs}")
    out = out[["factor_name", "ts_code", "trade_date", "factor_value"]]

elif src == "cjpy":
    if not (trade_date and ts_code):
        die("cjpy 源必须同时给 trade_date 和 ts_code（天软按证券×截面查询）")
    import cjpy
    df = cjpy.get_factor_data(code=ts_code, date=trade_date, factors=[name])
    if df is None or df.empty:
        die(f"cjpy.get_factor_data 返回空: {name} {ts_code} {trade_date}")
    if "factor_value" in df.columns:  # 长形
        out = df
    else:  # 宽形（各因子一列）→ melt
        if name not in df.columns:
            die(f"cjpy 返回列里没有 {name!r}: {list(df.columns)[:12]}")
        key_cols = [c for c in df.columns if str(c) in ("日期", "date", "证券代码", "代码", "code", "ts_code")]
        # 只取请求的因子列——维度列（截止日/名称等）不是因子值，不进 value
        long = df.melt(id_vars=key_cols, value_vars=[name],
                       var_name="factor_name", value_name="factor_value")
        code_col = next((c for c in key_cols if "代" in str(c) or "code" in str(c).lower()), key_cols[0] if key_cols else None)
        date_col = next((c for c in key_cols if "日" in str(c) or "date" in str(c).lower()), None)
        out = pd.DataFrame({
            "factor_name": long["factor_name"],
            "ts_code": long[code_col] if code_col else ts_code,
            "trade_date": long[date_col].astype(str).str.replace("-", "") if date_col else trade_date,
            "factor_value": long["factor_value"],
        }).dropna(subset=["factor_value"])
    if out is None or out.empty:
        die(f"cjpy 因子 {name!r} 无有效值（因子名不存在，或该证券/日期无数据；天软对未知因子返回 None 列不报错）；有效因子见 /data/factor_catalog source=cjpy")

else:
    die(f"未知源前缀 {src!r}（支持 sw/ts/cons/cjpy）；目录见 /data/factor_catalog")

OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
tmp = OUT_CSV.with_suffix(".csv.tmp")
out.to_csv(tmp, index=False)
os.replace(tmp, OUT_CSV)
d0, d1 = (out["trade_date"].min(), out["trade_date"].max()) if len(out) else ("-", "-")
print(f"factor_query[{factor_id}] -> {len(out)} rows, trade_date {d0}~{d1}, ts_code~{out['ts_code'].nunique()}只 -> {OUT_CSV}")
