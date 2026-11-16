#!/usr/bin/env python3
"""cashflow.parquet 缺口补拉(2026-10-08): income 有而 cashflow 无的 341 码(603xxx 掉块)。

用法: setsid nohup python3 fetch_cashflow_gap341_20261008.py >> ~/fmdata/store/research_export/cashflow_gap341.log 2>&1 &
口径: 与 csi1800_full_history_core_20261004/cashflow.parquet 一致 —— 10 字段, report_type=1(合并报表), 全历史。
幂等: 重跑按 parquet 现状重算缺口, 已补齐的自动跳过。
"""
import datetime
import os
import sys
import time

import pandas as pd
import tushare as ts

CORE = os.path.expanduser("~/fmdata/store/research_export/csi1800_full_history_core_20261004")
FIELDS = "ts_code,ann_date,f_ann_date,end_date,report_type,n_cashflow_act,n_cashflow_inv_act,free_cashflow,c_pay_acq_const_fiolta,end_bal_cash"
SLEEP = 0.15


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%F %T')}] {msg}", flush=True)


def _retry(fn, **kwargs):
    last = None
    for attempt in range(5):
        try:
            return fn(**kwargs)
        except Exception as e:
            last = e
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 4:
                time.sleep(5 + attempt * 5)
                continue
            raise
    raise last


def main():
    for line in open(os.path.expanduser("~/fmdata/.env")):
        if line.startswith("TUSHARE_TOKEN="):
            ts.set_token(line.split("=", 1)[1].strip().strip('"').strip("'"))
    pro = ts.pro_api()

    inc_codes = set(pd.read_parquet(f"{CORE}/income.parquet", columns=["ts_code"])["ts_code"])
    cf = pd.read_parquet(f"{CORE}/cashflow.parquet")
    todo = sorted(inc_codes - set(cf["ts_code"]))
    log(f"cashflow 缺口: {len(todo)} 码 (income={len(inc_codes)}, 已有={cf['ts_code'].nunique()})")
    if not todo:
        log("无缺口, 退出")
        return 0

    got, empty, failed = [], [], []
    for i, code in enumerate(todo):
        try:
            df = _retry(pro.cashflow, ts_code=code, fields=FIELDS)
            if df is not None and len(df):
                # 坑: 带 fields 参数时 report_type 返回 ArrowStringArray('1'), int 比较会全滤光
                df = df[df["report_type"].astype(str).str.strip() == "1"].copy()
                df["report_type"] = 1
                for col in ("ann_date", "f_ann_date", "end_date"):
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")  # parquet 侧是 Int64Dtype
                for col in ("n_cashflow_act", "n_cashflow_inv_act", "free_cashflow",
                            "c_pay_acq_const_fiolta", "end_bal_cash"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            if df is None or not len(df):
                empty.append(code)
            else:
                got.append(df)
        except Exception as e:
            log(f"ERR {code}: {str(e)[:100]}")
            failed.append(code)
        if i % 25 == 0:
            log(f"  {i}/{len(todo)}")
        time.sleep(SLEEP)

    if got:
        bak = f"{CORE}/cashflow.parquet.bak.20261008"
        if not os.path.exists(bak):
            cf.to_parquet(bak, index=False)
        merged = pd.concat([cf] + got, ignore_index=True)
        merged = merged.drop_duplicates(subset=["ts_code", "end_date", "ann_date", "report_type"], keep="last")
        merged = merged.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
        tmp = f"{CORE}/cashflow.parquet.tmp"
        merged.to_parquet(tmp, index=False)
        os.replace(tmp, f"{CORE}/cashflow.parquet")
        log(f"补入 {len(got)} 码, 合并后 {len(merged)} 行 / {merged['ts_code'].nunique()} 码 -> cashflow.parquet (备份 {bak})")
    log(f"DONE: 补回 {len(got)}, 服务端空 {len(empty)}: {empty[:20]}, 失败 {len(failed)}: {failed[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
