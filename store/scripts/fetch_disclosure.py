#!/usr/bin/env python3
"""巨潮公告数据抓取 (cninfo)
全市场最近 N 天公告，一次查询取全量。
source: akshare stock_zh_a_disclosure_report_cninfo (symbol="" 查全市场)
"""
import pandas as pd
from datetime import datetime, timedelta
import akshare as ak

OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/disclosure_report.csv"
DAYS_BACK = 30


def main():
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=DAYS_BACK - 1)).strftime("%Y%m%d")
    print(f"[disclosure] Range: {start} ~ {today}", flush=True)

    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol="", market="沪深京", keyword="", category="",
        start_date=start, end_date=today,
    )
    if df is None or len(df) == 0:
        print("[disclosure] WARNING: no data", flush=True)
        pd.DataFrame().to_csv(OUTPUT_CSV, index=False)
        return

    col_map = {
        "代码": "SECURITY_CODE",
        "简称": "SECURITY_NAME",
        "公告标题": "ANNOUNCEMENT_TITLE",
        "公告时间": "ANNOUNCEMENT_DATE",
        "公告链接": "ANNOUNCEMENT_URL",
    }
    keep = {k: v for k, v in col_map.items() if k in df.columns}
    df = df[list(keep.keys())].rename(columns=keep)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"[disclosure] DONE: {len(df)} rows -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
