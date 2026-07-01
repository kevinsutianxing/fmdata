"""计算统一因子矩阵: 从daily_basic派生动量/波动/换手/市值等因子"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

STORE = Path("/home/ubuntu/fmdata/store")
output_path = STORE / "factors/factor_matrix_unified.csv"

# 加载daily_basic
daily_basic_path = STORE / "market/daily_basic.csv"
if not daily_basic_path.exists():
    print("daily_basic.csv not found, fetching...")
    import tushare as ts
    pro = ts.pro_api()
    # 拉最近1年
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
    db = pro.daily_basic(start_date=start, end_date=end)
    if db is not None and not db.empty:
        db.to_csv(daily_basic_path, index=False)
else:
    db = pd.read_csv(daily_basic_path)

if db is None or db.empty:
    print("No daily_basic data"); exit(1)

print(f"Loaded daily_basic: {len(db)} rows, {db['trade_date'].nunique()} dates")

# 检查必需列
required = ['ts_code', 'trade_date', 'close', 'turnover_rate', 'pe', 'pb', 'total_mv', 'circ_mv']
for col in required:
    if col not in db.columns:
        print(f"Missing column: {col}")

# 计算因子
dates = sorted(db['trade_date'].unique())
result = []

for i, date in enumerate(dates):
    day_data = db[db['trade_date'] == date].copy()
    if len(day_data) < 100:
        continue
    
    factors = pd.DataFrame()
    factors['ts_code'] = day_data['ts_code']
    factors['trade_date'] = date
    
    # 市值因子 (log total_mv)
    if 'total_mv' in day_data.columns:
        factors['size'] = np.log(day_data['total_mv'].astype(float).clip(lower=1))
    
    # 估值因子
    if 'pe' in day_data.columns:
        pe = day_data['pe'].astype(float)
        factors['ep'] = 1 / pe.clip(lower=0.1)  # E/P
    if 'pb' in day_data.columns:
        pb = day_data['pb'].astype(float)
        factors['bp'] = 1 / pb.clip(lower=0.1)  # B/P
    
    # 换手率因子
    if 'turnover_rate' in day_data.columns:
        factors['turnover'] = day_data['turnover_rate'].astype(float)
    
    result.append(factors)

if not result:
    print("No factor data computed"); exit(1)

factor_df = pd.concat(result, ignore_index=True)

# 保存
output_path.parent.mkdir(parents=True, exist_ok=True)
factor_df.to_csv(output_path, index=False)
print(f"Saved factor_matrix_unified: {len(factor_df)} rows, {factor_df['trade_date'].nunique()} dates")
print(f"Columns: {factor_df.columns.tolist()}")
