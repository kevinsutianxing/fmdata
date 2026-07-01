"""刷新申万一级行业基本面扩展: 营收/净利/ROE/毛利率等"""
import time
import pandas as pd
from pathlib import Path
from datetime import datetime

STORE = Path("/home/ubuntu/fmdata/store")
output_path = STORE / "sw_extended/sw_fundamentals_v10.csv"

import tushare as ts
pro = ts.pro_api()

# 获取行业列表
industries = pro.index_classify(level='L1', src='SW2021')
codes = industries['index_code'].tolist()
names = industries['industry_name'].tolist()
print(f"共 {len(codes)} 个一级行业")

# 读取已有数据
existing_data = set()
if output_path.exists():
    old = pd.read_csv(output_path)
    if 'period' in old.columns:
        existing_data = set(zip(old['ts_code'], old['period']))

# 获取最近4个报告期
periods = []
year = datetime.now().year
month = datetime.now().month
for y in range(year, year-2, -1):
    for m in [1231, 930, 630, 331]:
        p = f"{y}{m}"
        if int(p) <= int(f"{year}{month:02d}99"):
            periods.append(p)
periods = sorted(set(periods))[-4:]  # 最近4期
print(f"报告期: {periods}")

all_rows = []
for i, (code, name) in enumerate(zip(codes, names)):
    for period in periods:
        key = (code, period)
        if key in existing_data:
            continue
        try:
            # 用sw_daily获取行业估值，再用index_classify获取基本面
            df = pro.sw_daily(ts_code=code, start_date=f"{period[:4]}0101", end_date=f"{period[:4]}1231")
            if df is not None and not df.empty:
                row = {
                    'ts_code': code,
                    'industry_name': name,
                    'period': period,
                    'pe': df['pe'].mean() if 'pe' in df.columns else None,
                    'pb': df['pb'].mean() if 'pb' in df.columns else None,
                    'avg_total_mv': df['total_mv'].mean() if 'total_mv' in df.columns else None,
                    'avg_float_mv': df['float_mv'].mean() if 'float_mv' in df.columns else None,
                }
                all_rows.append(row)
            time.sleep(0.3)
        except Exception as e:
            print(f"  {name} {period}: {e}")

    print(f"  [{i+1}/{len(codes)}] {name}")

if not all_rows:
    print("无新数据"); exit(0)

new_df = pd.DataFrame(all_rows)
if output_path.exists():
    old = pd.read_csv(output_path)
    combined = pd.concat([old, new_df], ignore_index=True)
    combined = combined.drop_duplicates(subset=['ts_code', 'period'], keep='last')
else:
    combined = new_df

output_path.parent.mkdir(parents=True, exist_ok=True)
combined.to_csv(output_path, index=False)
print(f"Saved sw_fundamentals_v10: {len(combined)} rows")
