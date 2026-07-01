"""增量刷新申万一级行业 PE/PB（只补近期缺失）"""
import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

STORE = Path("/home/ubuntu/fmdata/store")

pe_path = STORE / "market/sw_pe_history.csv"
pb_path = STORE / "market/sw_pb_history.csv"

existing_dates = set()
if pe_path.exists():
    old = pd.read_csv(pe_path)
    if 'date' in old.columns:
        existing_dates = set(old['date'].astype(str).str[:10].tolist())

# 只补最近30天的数据
start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
end = datetime.now().strftime("%Y%m%d")

print(f"获取 {start}~{end} 的 PE/PB...")
df = ak.index_analysis_daily_sw(symbol='一级行业', start_date=start, end_date=end)
if df is None or df.empty:
    print("无数据"); exit(0)

pe_rows, pb_rows = {}, {}
for _, row in df.iterrows():
    d = str(row['发布日期'])[:10]
    if d in existing_dates:
        continue
    name = row['指数名称']
    pe_rows.setdefault(d, {})[name] = row['市盈率']
    pb_rows.setdefault(d, {})[name] = row['市净率']

if not pe_rows:
    print("无新数据"); exit(0)

def append(path, new_data):
    rows = pd.read_csv(path).to_dict('records') if path.exists() else []
    for d in sorted(new_data):
        rows.append({'date': d, **new_data[d]})
    r = pd.DataFrame(rows).drop_duplicates('date', keep='last').sort_values('date').reset_index(drop=True)
    r.to_csv(path, index=False)
    return len(r)

print(f"PE: {append(pe_path, pe_rows)}, PB: {append(pb_path, pb_rows)}, latest={max(pe_rows)}")
