#!/usr/bin/env python3
"""互动易问答数据抓取 (cninfo)
全市场遍历，抓取每只股票的互动易提问+已回答的全文。
source: akshare stock_irm_cninfo
"""
import sys
import time
import pandas as pd
import akshare as ak

STOCK_LIST_CSV = "/home/ubuntu/fmdata/store/reference/stock_list.csv"
OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/irm_qa.csv"
BATCH_DELAY = 0.3


def load_stock_codes():
    df = pd.read_csv(STOCK_LIST_CSV, dtype=str)
    code_col = "ts_code" if "ts_code" in df.columns else df.columns[0]
    codes = df[code_col].str.replace(r"\.\w+$", "", regex=True).tolist()
    return [c for c in codes if len(c) == 6]


def fetch_one(code):
    try:
        df = ak.stock_irm_cninfo(symbol=code)
        if df is None or len(df) == 0:
            return None
        df["SECURITY_CODE"] = code
        col_map = {
            "股票代码": "SECURITY_CODE_SRC",
            "公司简称": "SECURITY_NAME",
            "行业": "INDUSTRY",
            "问题": "QUESTION",
            "提问者": "QUESTIONER",
            "来源": "SOURCE",
            "提问时间": "QUESTION_DATE",
            "更新时间": "UPDATE_DATE",
            "问题编号": "QUESTION_ID",
            "回答内容": "ANSWER_CONTENT",
            "回答者": "ANSWERER",
        }
        keep = {k: v for k, v in col_map.items() if k in df.columns}
        df = df[list(keep.keys())].rename(columns=keep)
        return df
    except Exception:
        return None


def main():
    codes = load_stock_codes()
    print(f"[irm_qa] Total stocks: {len(codes)}", flush=True)

    all_dfs = []
    total_answered = 0
    for i, code in enumerate(codes):
        df = fetch_one(code)
        if df is not None:
            n_ans = df["ANSWER_CONTENT"].notna().sum() if "ANSWER_CONTENT" in df.columns else 0
            total_answered += int(n_ans)
            all_dfs.append(df)
        if (i + 1) % 100 == 0:
            collected = sum(len(d) for d in all_dfs)
            print(f"  [{i+1}/{len(codes)}] collected={collected} answered={total_answered}", flush=True)
        time.sleep(BATCH_DELAY)

    if not all_dfs:
        print("[irm_qa] WARNING: no data collected", flush=True)
        pd.DataFrame().to_csv(OUTPUT_CSV, index=False)
        return

    result = pd.concat(all_dfs, ignore_index=True)
    result.to_csv(OUTPUT_CSV, index=False)
    n_total = len(result)
    n_answered = int(result["ANSWER_CONTENT"].notna().sum()) if "ANSWER_CONTENT" in result.columns else 0
    print(f"[irm_qa] DONE: {n_total} rows ({n_answered} answered) -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
