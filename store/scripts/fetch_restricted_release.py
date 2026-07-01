#!/usr/bin/env python3
"""限售解禁数据 (东方财富)
全市场，按日期范围一次查询取全量。
source: akshare stock_restricted_release_detail_em
"""
import pandas as pd
from datetime import datetime, timedelta
import akshare as ak

OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/restricted_release.csv"
DAYS_FORWARD = 180


def main():
    start = datetime.now().strftime("%Y%m%d")
    end = (datetime.now() + timedelta(days=DAYS_FORWARD)).strftime("%Y%m%d")
    print(f"[restricted_release] Range: {start} ~ {end}", flush=True)

    df = ak.stock_restricted_release_detail_em(start_date=start, end_date=end)
    if df is None or len(df) == 0:
        print("[restricted_release] WARNING: no data", flush=True)
        pd.DataFrame().to_csv(OUTPUT_CSV, index=False)
        return

    col_map = {
        "股票代码": "SECURITY_CODE",
        "股票简称": "SECURITY_NAME",
        "解禁时间": "RELEASE_DATE",
        "限售股类型": "SHARE_TYPE",
        "解禁数量": "RELEASE_SHARES",
        "实际解禁数量": "ACTUAL_RELEASE_SHARES",
        "实际解禁市值": "ACTUAL_RELEASE_VALUE",
        "占解禁前流通市值比例": "FLOAT_RATIO",
        "解禁前一交易日收盘价": "PREV_CLOSE",
    }
    keep = {k: v for k, v in col_map.items() if k in df.columns}
    df = df[list(keep.keys())].rename(columns=keep)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"[restricted_release] DONE: {len(df)} rows -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
