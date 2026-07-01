#!/usr/bin/env python3
"""Fetch HS300 index weights for multiple dates and enrich with stock info + style classification.

Outputs to /home/ubuntu/fmdata/store/market/hs300_selected_weights.csv
"""
from pathlib import Path
import pandas as pd
from fmdata.fetcher import TushareFetcher

OUT = Path("/home/ubuntu/fmdata/store/market/hs300_selected_weights.csv")
DATES = ["20210129", "20210331", "20211231", "20241231", "20260331", "20260601"]

STOCK_LIST = Path("/home/ubuntu/fmdata/store/reference/stock_list.csv")


def classify_style(industry: str, name: str) -> str:
    text = f"{industry or }{name or }"
    if any(k in text for k in ["银行", "保险", "证券", "电力", "铁路", "高速", "煤炭", "石油", "运营商", "通信服务"]):
        return "金融红利/低波价值"
    if any(k in text for k in ["白酒", "食品", "医药", "家电", "医疗", "消费", "旅游", "商业", "乳制品"]):
        return "消费医药/核心资产"
    if any(k in text for k in ["电气设备", "电池", "半导体", "元器件", "通信设备", "软件服务", "互联网", "汽车", "汽车配件", "专用机械", "工业机械", "机器人", "航空", "军工", "电子"]):
        return "科技制造/成长弹性"
    if any(k in text for k in ["有色", "化工", "钢铁", "建材", "工程机械", "运输设备"]):
        return "周期制造/资源品"
    return "其他"


def main() -> None:
    fetcher = TushareFetcher()
    frames = []
    for d in DATES:
        df = fetcher._call("index_weight", None, index_code="000300.SH", trade_date=d)
        if df is not None and not df.empty:
            frames.append(df)
            print(f"  {d}: {len(df)} rows")
        else:
            print(f"  {d}: empty")
    if not frames:
        raise SystemExit("No data fetched")
    all_df = pd.concat(frames, ignore_index=True)
    stock = pd.read_csv(STOCK_LIST, dtype={"ts_code": str})[["ts_code", "name", "industry"]]
    all_df = all_df.merge(stock, left_on="con_code", right_on="ts_code", how="left")
    all_df["风格归类"] = all_df.apply(lambda r: classify_style(r.get("industry"), r.get("name")), axis=1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(OUT, index=False, encoding="utf-8-sig")
    summary = all_df.groupby("trade_date").agg(n=("con_code", "nunique"), weight_sum=("weight", "sum")).reset_index()
    print(summary.to_string(index=False))
    print(f"\nSaved {len(all_df)} rows to {OUT}")


if __name__ == "__main__":
    main()
