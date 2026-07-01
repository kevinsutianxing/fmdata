#!/usr/bin/env python3
"""个股研报数据抓取 (东方财富)
全市场遍历，抓取每只股票的研究报告。
source: akshare stock_research_report_em
"""
import sys
import time
import pandas as pd
import akshare as ak

STOCK_LIST_CSV = "/home/ubuntu/fmdata/store/reference/stock_list.csv"
OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/research_reports.csv"
BATCH_DELAY = 0.3


def load_stock_codes():
    df = pd.read_csv(STOCK_LIST_CSV, dtype=str)
    code_col = "ts_code" if "ts_code" in df.columns else df.columns[0]
    codes = df[code_col].str.replace(r"\.\w+$", "", regex=True).tolist()
    return [c for c in codes if len(c) == 6]


def fetch_one(code):
    try:
        df = ak.stock_research_report_em(symbol=code)
        if df is None or len(df) == 0:
            return None
        df["SECURITY_CODE"] = code
        col_map = {
            "股票代码": "SECURITY_CODE_SRC",
            "股票简称": "SECURITY_NAME",
            "报告名称": "REPORT_TITLE",
            "东财评级": "RATING",
            "机构": "ORG_NAME",
            "近一月个股研报数": "ORG_REPORT_NUM",
            "2026-盈利预测-收益": "EPS_2026",
            "2026-盈利预测-市盈率": "PE_2026",
            "2027-盈利预测-收益": "EPS_2027",
            "2027-盈利预测-市盈率": "PE_2027",
            "2028-盈利预测-收益": "EPS_2028",
            "2028-盈利预测-市盈率": "PE_2028",
            "行业": "INDUSTRY",
            "日期": "PUBLISH_DATE",
            "报告PDF链接": "REPORT_PDF_URL",
        }
        keep = {k: v for k, v in col_map.items() if k in df.columns}
        df = df[list(keep.keys())].rename(columns=keep)
        return df
    except Exception:
        return None


def main():
    codes = load_stock_codes()
    print(f"[research_reports] Total stocks: {len(codes)}", flush=True)

    all_dfs = []
    for i, code in enumerate(codes):
        df = fetch_one(code)
        if df is not None:
            all_dfs.append(df)
        if (i + 1) % 100 == 0:
            collected = sum(len(d) for d in all_dfs)
            print(f"  [{i+1}/{len(codes)}] collected={collected}", flush=True)
        time.sleep(BATCH_DELAY)

    if not all_dfs:
        print("[research_reports] WARNING: no data collected", flush=True)
        pd.DataFrame().to_csv(OUTPUT_CSV, index=False)
        return

    result = pd.concat(all_dfs, ignore_index=True)
    result.to_csv(OUTPUT_CSV, index=False)
    print(f"[research_reports] DONE: {len(result)} rows -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
