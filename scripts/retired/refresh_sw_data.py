"""批量刷新申万一级行业收盘价、成交额数据"""
import sys
import time
import akshare as ak
import pandas as pd
import numpy as np
from pathlib import Path

STORE = Path("/home/ubuntu/fmdata/store")

# 获取行业列表
info_df = ak.sw_index_first_info()
codes = info_df['行业代码'].tolist()  # e.g. 801010.SI
names = info_df['行业名称'].tolist()
print(f"共 {len(codes)} 个一级行业")

# 读取现有数据确定起始日期
close_path = STORE / "market/sw_first_level_close.csv"
existing_dates = set()
last_date_str = "20200101"

if close_path.exists():
    old = pd.read_csv(close_path)
    if 'date' in old.columns:
        existing_dates = set(old['date'].astype(str).tolist())
        dates_sorted = sorted(existing_dates)
        last_date_str = dates_sorted[-1].replace("-", "")
        print(f"现有数据到 {dates_sorted[-1]}，将从此日期起增量更新")

# 批量获取每个行业的历史数据
all_close = {}  # date -> {industry: close}
all_amount = {}  # date -> {industry: amount}

for i, (code, name) in enumerate(zip(codes, names)):
    symbol = code.replace(".SI", "")  # 801010.SI -> 801010
    try:
        df = ak.index_hist_sw(symbol=symbol, period="day")
        if df is None or df.empty:
            print(f"  [{i+1}/{len(codes)}] {name}({symbol}) 无数据")
            continue
        
        # 只取增量部分
        if existing_dates:
            df['日期'] = pd.to_datetime(df['日期']).dt.strftime('%Y-%m-%d')
            df = df[~df['日期'].isin(existing_dates)]
            if df.empty:
                print(f"  [{i+1}/{len(codes)}] {name}({symbol}) 无新数据")
                continue
        
        for _, row in df.iterrows():
            d = str(row['日期']) if '日期' in df.columns else str(row.iloc[1])
            # 处理日期格式
            if 'T' in d:
                d = d[:10]
            
            close_val = row.get('收盘', np.nan)
            amount_val = row.get('成交额', np.nan)
            
            if d not in all_close:
                all_close[d] = {}
                all_amount[d] = {}
            all_close[d][name] = close_val
            all_amount[d][name] = amount_val
        
        print(f"  [{i+1}/{len(codes)}] {name}({symbol}) +{len(df)} 条")
        time.sleep(0.3)  # 避免过快请求
        
    except Exception as e:
        print(f"  [{i+1}/{len(codes)}] {name}({symbol}) 失败: {e}")

if not all_close:
    print("无新数据")
    sys.exit(0)

# 合并到现有数据
def build_df(existing_path, new_data, date_col='date'):
    rows = []
    if existing_path.exists():
        old = pd.read_csv(existing_path)
        for _, row in old.iterrows():
            d = str(row[date_col])[:10]
            row_dict = {date_col: d}
            for c in old.columns:
                if c != date_col:
                    row_dict[c] = row[c]
            rows.append(row_dict)
    
    for d in sorted(new_data.keys()):
        row_dict = {date_col: d}
        row_dict.update(new_data[d])
        rows.append(row_dict)
    
    return pd.DataFrame(rows)

# 写入收盘价
close_df = build_df(close_path, all_close)
close_df.to_csv(close_path, index=False)
print(f"\n收盘价: {len(close_df)} dates -> {close_path}")

# 写入成交额
amount_path = STORE / "market/sw_first_level_amount.csv"
amount_df = build_df(amount_path, all_amount)
amount_df.to_csv(amount_path, index=False)
print(f"成交额: {len(amount_df)} dates -> {amount_path}")

print(f"最新日期: {max(all_close.keys())}")
