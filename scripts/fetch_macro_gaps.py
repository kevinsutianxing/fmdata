import akshare as ak
import pandas as pd
from pathlib import Path

out = Path('/home/ubuntu/fmdata/store/macro')
out.mkdir(parents=True, exist_ok=True)

# bond_china_yield requires a date window; keep each request under one year.
parts = []
for start, end in [('2008-01-01','2008-12-31'),('2009-01-01','2009-12-31'),('2010-01-01','2010-12-31'),('2011-01-01','2011-12-31'),('2012-01-01','2012-12-31'),('2013-01-01','2013-12-31'),('2014-01-01','2014-12-31'),('2015-01-01','2015-12-31'),('2016-01-01','2016-12-31'),('2017-01-01','2017-12-31'),('2018-01-01','2018-12-31'),('2019-01-01','2019-12-31'),('2020-01-01','2020-12-31'),('2021-01-01','2021-12-31'),('2022-01-01','2022-12-31'),('2023-01-01','2023-12-31'),('2024-01-01','2024-12-31'),('2025-01-01','2025-12-31'),('2026-01-01','2026-07-20')]:
    try:
        x = ak.bond_china_yield(start.replace('-',''), end.replace('-',''))
        if x is not None and len(x):
            parts.append(x)
            print('bond', start, end, len(x), flush=True)
    except Exception as e:
        print('bond_error', start, end, str(e)[:160], flush=True)
if parts:
    bond = pd.concat(parts, ignore_index=True).drop_duplicates()
    bond.to_csv(out/'cgb_yield_curve_full_probe.csv', index=False)
    print('bond_total', bond.shape, flush=True)

# RMB midpoint; normalize all columns to strings before writing.
try:
    r = ak.macro_china_rmb()
    r = pd.DataFrame(r)
    r.columns = [str(c) for c in r.columns]
    for c in r.columns:
        r[c] = r[c].map(lambda v: '' if pd.isna(v) else str(v))
    r.to_csv(out/'macro_china_rmb_clean_probe.csv', index=False)
    print('rmb_total', r.shape, list(r.columns), flush=True)
except Exception as e:
    print('rmb_error', str(e)[:300], flush=True)
კ=0
