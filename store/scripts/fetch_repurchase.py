#!/usr/bin/env python3
"""全市场回购数据 (东方财富)
source: akshare stock_repurchase_em (单次查询全市场)
"""
import pandas as pd
import akshare as ak

OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/repurchase.csv"


def main():
    print("[repurchase] Fetching...", flush=True)
    df = ak.stock_repurchase_em()
    if df is None or len(df) == 0:
        print("[repurchase] WARNING: no data", flush=True)
        pd.DataFrame().to_csv(OUTPUT_CSV, index=False)
        return

    col_map = {
        "序号": "SEQ",
        "股票代码": "SECURITY_CODE",
        "股票简称": "SECURITY_NAME",
        "最新价": "CLOSE",
        "计划回购价格区间": "PLAN_PRICE_RANGE",
        "计划回购金额区间-下限": "PLAN_AMOUNT_MIN",
        "计划回购金额区间-上限": "PLAN_AMOUNT_MAX",
        "占公告前一日总股本比例-下限": "RATIO_MIN",
        "占公告前一日总股本比例-上限": "RATIO_MAX",
        "回购起始时间": "START_DATE",
        "实施进度": "STATUS",
        "已回购股份数量": "EXECUTED_SHARES",
        "已回购金额": "EXECUTED_AMOUNT",
        "最新公告日期": "ANNOUNCE_DATE",
    }
    keep = {k: v for k, v in col_map.items() if k in df.columns}
    df = df[list(keep.keys())].rename(columns=keep)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"[repurchase] DONE: {len(df)} rows -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
