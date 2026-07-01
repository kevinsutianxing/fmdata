#!/usr/bin/env python3
"""Refresh analyst consensus data from akshare (weekly).
Produces: analyst_consensus.csv, consensus_detail.csv, consensus_proxy.csv
Also saves a weekly snapshot for historical accumulation.
"""
import akshare as ak
import pandas as pd
import numpy as np
import time
import json
import urllib.request
from pathlib import Path
from datetime import datetime

STORE = Path.home() / "fmdata" / "store"
REG_PATH = Path.home() / "fmdata" / "registry.json"
FMURL = "http://127.0.0.1:1934"
SNAP_DIR = STORE / "fundamentals" / "consensus_snapshots"
SNAP_DIR.mkdir(parents=True, exist_ok=True)

# ── 1. analyst_consensus (EM - all stocks batch) ──────────
print("Fetching analyst_consensus (EM)...")
df = ak.stock_profit_forecast_em(symbol="")
mapped = pd.DataFrame({
    'SECUCODE': df['代码'].apply(lambda x: f"{x}.SZ" if x.startswith(('0','3')) else f"{x}.SH"),
    'SECURITY_CODE': df['代码'],
    'SECURITY_NAME_ABBR': df['名称'],
    'RATING_ORG_NUM': df['研报数'],
    'RATING_BUY_NUM': df['机构投资评级(近六个月)-买入'],
    'RATING_ADD_NUM': df['机构投资评级(近六个月)-增持'],
    'RATING_NEUTRAL_NUM': df['机构投资评级(近六个月)-中性'],
    'RATING_REDUCE_NUM': df['机构投资评级(近六个月)-减持'],
    'RATING_SALE_NUM': df['机构投资评级(近六个月)-卖出'],
    'YEAR1': 2025, 'YEAR_MARK1': 'A', 'EPS1': df['2025预测每股收益'],
    'YEAR2': 2026, 'YEAR_MARK2': 'E', 'EPS2': df['2026预测每股收益'],
    'YEAR3': 2027, 'YEAR_MARK3': 'E', 'EPS3': df['2027预测每股收益'],
    'YEAR4': 2028, 'YEAR_MARK4': 'E', 'EPS4': df['2028预测每股收益'],
})
mapped.to_csv(STORE / "fundamentals" / "analyst_consensus.csv", index=False)
# Save weekly snapshot for historical accumulation
today = datetime.now().strftime("%Y%m%d")
mapped.to_csv(SNAP_DIR / f"snapshot_{today}.csv", index=False)
print(f"  analyst_consensus: {len(mapped)} stocks (snapshot saved)")

# ── 2. consensus_detail (THS - top 100) ───────────────────
print("Fetching consensus_detail (THS top 100)...")
top100 = mapped.nlargest(100, 'RATING_ORG_NUM')
results = []
for _, row in top100.iterrows():
    code = str(row['SECUCODE']).split('.')[0]
    try:
        df2 = ak.stock_profit_forecast_ths(symbol=code, indicator='业绩预测详表-详细指标预测')
        df2['SECURITY_CODE'] = code
        df2['SECURITY_NAME'] = row['SECURITY_NAME_ABBR']
        results.append(df2)
    except Exception:
        pass
    time.sleep(0.2)
if results:
    combined = pd.concat(results, ignore_index=True)
    combined.to_csv(STORE / "fundamentals" / "consensus_detail.csv", index=False)
    print(f"  consensus_detail: {len(results)} stocks, {len(combined)} rows")

# ── 3. consensus_proxy (mixed proxy v2) ───────────────────
print("Building consensus_proxy (mixed v2)...")
consensus = mapped.copy()
consensus['SECURITY_CODE'] = consensus['SECURITY_CODE'].astype(str).str.zfill(6)
consensus['eps_growth_1y'] = np.where(
    consensus['EPS1'].notna() & (consensus['EPS1'] != 0),
    (consensus['EPS2'] - consensus['EPS1']) / consensus['EPS1'].abs(), np.nan)
consensus['eps_growth_1y'] = consensus['eps_growth_1y'].clip(-2.0, 3.0)

# Industry map (stock → SW industry)
with urllib.request.urlopen(f"{FMURL}/data/stock_sw_industry_map") as r:
    stock_ind = pd.DataFrame(json.loads(r.read())['data'])
sw_list = pd.read_csv(STORE / "reference/sw_industry_list.csv")
ind_map = stock_ind.merge(sw_list[['index_code','industry_name']],
                          left_on='sw_l1', right_on='index_code', how='left')
ind_map = ind_map.rename(columns={'stock_code': 'ts_code'})
ind_map['code'] = ind_map['ts_code'].str.split('.').str[0]

# Market cap
daily_basic = pd.read_csv(STORE / "market/daily_basic.csv")
daily_basic['code'] = daily_basic['ts_code'].str.split('.').str[0]
latest_date = daily_basic['trade_date'].max()
mktcap = daily_basic[daily_basic['trade_date'] == latest_date][['code','total_mv']].copy()
mktcap['total_mv'] = pd.to_numeric(mktcap['total_mv'], errors='coerce')

# Historical growth (from stock_fina)
fina_dir = STORE / "fundamentals" / "stock_fina"
all_fina = []
for f in sorted(fina_dir.glob("fina_*.csv")):
    dff = pd.read_csv(f)
    dff['period'] = f.stem.replace("fina_", "")
    all_fina.append(dff)
fina = pd.concat(all_fina, ignore_index=True)
fina['code'] = fina['ts_code'].str.split('.').str[0]
recent_periods = sorted(fina['period'].unique())[-4:]
recent = fina[fina['period'].isin(recent_periods)]
hist_growth = recent.groupby('code').agg(
    netprofit_yoy_latest=('netprofit_yoy', lambda x: x.dropna().iloc[-1] if len(x.dropna()) > 0 else np.nan),
    roe_latest=('roe', lambda x: x.dropna().iloc[-1] if len(x.dropna()) > 0 else np.nan),
).reset_index()
hist_growth['netprofit_yoy_latest'] = hist_growth['netprofit_yoy_latest'].clip(-200, 300)

# Merge consensus with industry + mktcap
cons = consensus.merge(ind_map[['code','industry_name']],
                       left_on='SECURITY_CODE', right_on='code', how='left')
cons = cons.merge(mktcap, left_on='SECURITY_CODE', right_on='code',
                  how='left', suffixes=('','_mc'))

# Component 1: Industry mktcap-weighted growth
def mktcap_w(g):
    v = g.dropna(subset=['eps_growth_1y','total_mv'])
    if len(v) == 0: return np.nan
    w = v['total_mv']
    return np.average(v['eps_growth_1y'], weights=w) if w.sum() > 0 else v['eps_growth_1y'].median()

ind_stats = cons.groupby('industry_name').apply(lambda g: pd.Series({
    'ind_mktcap_growth': mktcap_w(g),
    'ind_median_growth': g['eps_growth_1y'].dropna().median(),
    'ind_count': g['eps_growth_1y'].notna().sum(),
    'ind_buy_ratio': g['RATING_BUY_NUM'].sum() / max(g['RATING_ORG_NUM'].sum(), 1),
})).reset_index()

# Component 2: Leader growth (top 3 mktcap, median)
ind_leader = cons.groupby('industry_name').apply(
    lambda g: pd.Series({'ind_leader_growth':
        g.nlargest(3,'total_mv')['eps_growth_1y'].dropna().median()
        if g.nlargest(3,'total_mv')['eps_growth_1y'].notna().any() else np.nan})
).reset_index()

ind_proxy = ind_stats.merge(ind_leader, on='industry_name', how='left')

# Build final
all_stocks = ind_map[['code','industry_name','ts_code']].rename(columns={'code':'SECURITY_CODE'})
result = all_stocks.merge(
    consensus[['SECURITY_CODE','EPS1','EPS2','EPS3','EPS4','eps_growth_1y','RATING_ORG_NUM']],
    on='SECURITY_CODE', how='left')
result = result.merge(ind_proxy, on='industry_name', how='left')
result = result.merge(hist_growth[['code','netprofit_yoy_latest','roe_latest']],
                      left_on='SECURITY_CODE', right_on='code', how='left', suffixes=('','_h'))
result['has_consensus'] = result['EPS2'].notna()

# Mixed proxy: 40% ind_mktcap + 30% hist + 30% leader (or 55/45 if no hist)
mask = ~result['has_consensus']
hh = mask & result['netprofit_yoy_latest'].notna()
nh = mask & result['netprofit_yoy_latest'].isna()
result.loc[hh, 'eps_growth_mixed'] = (
    0.40 * result.loc[hh, 'ind_mktcap_growth'].fillna(0) +
    0.30 * result.loc[hh, 'netprofit_yoy_latest'].fillna(0) / 100 +
    0.30 * result.loc[hh, 'ind_leader_growth'].fillna(0))
result.loc[nh, 'eps_growth_mixed'] = (
    0.55 * result.loc[nh, 'ind_mktcap_growth'].fillna(0) +
    0.45 * result.loc[nh, 'ind_leader_growth'].fillna(0))
result.loc[~mask, 'eps_growth_mixed'] = result.loc[~mask, 'eps_growth_1y']

out = result[['SECURITY_CODE','ts_code','industry_name','has_consensus',
              'EPS1','EPS2','EPS3','EPS4','eps_growth_1y',
              'eps_growth_mixed',
              'ind_mktcap_growth','netprofit_yoy_latest','ind_leader_growth',
              'ind_count','ind_buy_ratio','roe_latest']].copy()
out.to_csv(STORE / "fundamentals/consensus_proxy.csv", index=False)
print(f"  consensus_proxy: {len(out)} stocks ({out['has_consensus'].sum()} direct, {(~out['has_consensus']).sum()} proxy)")

# ── 4. Update registry ────────────────────────────────────
with open(REG_PATH) as f:
    reg = json.load(f)
today = datetime.now().strftime("%Y-%m-%d")
reg["datasets"]["analyst_consensus"]["rows"] = len(mapped)
reg["datasets"]["analyst_consensus"]["last_updated"] = today
reg["datasets"]["consensus_proxy"]["rows"] = len(out)
reg["datasets"]["consensus_proxy"]["last_updated"] = today
if results:
    reg["datasets"]["consensus_detail"]["rows"] = len(combined)
    reg["datasets"]["consensus_detail"]["last_updated"] = today
with open(REG_PATH, "w") as f:
    json.dump(reg, f, ensure_ascii=False, indent=2)

print(f"Done. All datasets refreshed at {today}.")
