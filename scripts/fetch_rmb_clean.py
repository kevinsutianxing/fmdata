import time
import requests
import pandas as pd
from pathlib import Path

url = 'https://cdn.jin10.com/data_center/reports/exchange_rate.json'
r = requests.get(url, params={'_': time.time()}, timeout=30)
r.raise_for_status()
payload = r.json()
values = payload.get('values', {})
rows = []
for date, item in values.items():
    pair = item.get('美元/人民币') if isinstance(item, dict) else None
    if isinstance(pair, (list, tuple)) and pair:
        rows.append({'date': date, 'usdcny_mid': pair[0], 'usdcny_change': pair[1] if len(pair)>1 else None})
out = pd.DataFrame(rows)
if out.empty:
    raise RuntimeError('no USD/CNY records in response')
out['date'] = pd.to_datetime(out['date'], errors='coerce')
out['usdcny_mid'] = pd.to_numeric(out['usdcny_mid'], errors='coerce')
out['usdcny_change'] = pd.to_numeric(out['usdcny_change'], errors='coerce')
out = out.dropna(subset=['date','usdcny_mid']).drop_duplicates('date').sort_values('date')
out.to_csv('/home/ubuntu/fmdata/store/macro/usdcny_midpoint_full.csv', index=False)
print(out.shape, out.date.min(), out.date.max())
print(out.head(2).to_string(index=False))
print(out.tail(2).to_string(index=False))
