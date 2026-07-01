#!/usr/bin/env python3
"""Fetch active equity fund list from tushare fund_basic."""
import tushare as ts
import pandas as pd

pro = ts.pro_api()
df = pro.fund_basic(market='O')  # Open-ended funds

# Filter to equity-related types
equity_types = ['股票型', '混合型', '灵活配置', '指数型', '被动指数', '增强指数', '成长型', '价值型', '平衡型']
mask = df['invest_type'].fillna('').str.contains('|'.join(equity_types), na=False)
equity = df[mask].copy()

# Only active funds
if 'status' in equity.columns:
    equity = equity[equity['status'] == 'L']  # L=正常

out = '/home/ubuntu/fmdata/store/market/active_equity_fund_list.csv'
equity.to_csv(out, index=False)
print(f"Saved {len(equity)} funds to {out}")
