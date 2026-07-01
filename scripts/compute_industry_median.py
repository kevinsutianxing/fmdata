"""计算行业中位数指标: 从基本面+一致预期数据派生"""
import pandas as pd
import numpy as np
import requests
from pathlib import Path

STORE = Path("/home/ubuntu/fmdata/store")
output_path = STORE / "sw_extended/extended_industry_median.csv"

# 1. 获取基本面
r = requests.get("http://127.0.0.1:1934/market/fundamentals", params={"period": "20260331"})
resp = r.json()
fina = pd.DataFrame(resp["data"] if isinstance(resp, dict) else resp)
print(f"Fundamentals: {len(fina)} rows")

# 2. 获取行业映射 (stock_list含industry字段)
r2 = requests.get("http://127.0.0.1:1934/reference/stocks")
resp2 = r2.json()
stocks = pd.DataFrame(resp2["data"] if isinstance(resp2, dict) else resp2)
print(f"Stocks: {len(stocks)} rows, has industry: {'industry' in stocks.columns}")

# 3. 合并
fina['ts_code'] = fina['ts_code'].astype(str)
stocks['ts_code'] = stocks['ts_code'].astype(str)
merged = fina.merge(stocks[['ts_code', 'industry']], on='ts_code', how='left')
merged = merged.dropna(subset=['industry'])
print(f"Merged: {len(merged)} rows with industry, {merged['industry'].nunique()} industries")

# 4. 按行业中位数聚合
agg_cols = [c for c in ['roe', 'or_yoy', 'netprofit_yoy', 'roe_yearly', 'dt_netprofit_yoy'] if c in merged.columns]
result = merged.groupby('industry')[agg_cols].median().reset_index()

# 加period标记
if 'end_date' in fina.columns:
    result.insert(0, 'period', str(fina['end_date'].iloc[0]))

output_path.parent.mkdir(parents=True, exist_ok=True)
result.to_csv(output_path, index=False)
print(f"Saved: {len(result)} industries, metrics: {agg_cols}")
print(result.head(5).to_string())
