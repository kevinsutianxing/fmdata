"""Combine all macro datasets into macro_monthly.csv and macro_state.csv.

Sources (all read from fmdata store/macro/):
  - cpi.csv:     month, cpi_yoy (via cn_cpi -> needs rename)
  - ppi.csv:     month, ppi_yoy (via cn_ppi)
  - pmi.csv:     MONTH, PMI010403(mfg), PMI011000(nonmfg) + compute yoy
  - money_supply.csv: month, m2_yoy, m1_yoy + compute m1_m2_scissors
  - credit.csv:  date, credit_yoy
  - lpr.csv:     date(YYYY-MM-DD), 1y, 5y -> forward fill to monthly
  - shibor.csv:  date(YYYY-MM), shibor_on, shibor_1y
"""
import pandas as pd
import numpy as np
from pathlib import Path

STORE = Path('/home/ubuntu/fmdata/store/macro')


def load_and_normalize():
    """Load all macro CSVs and normalize to YYYY-MM index."""
    frames = {}

    # --- CPI ---
    cpi = pd.read_csv(STORE / 'cpi.csv')
    # Tushare cn_cpi columns: month, nt_yoy (同比), ... - need to check
    if 'month' in cpi.columns:
        cpi['date'] = pd.to_datetime(cpi['month'].astype(str), format='%Y%m').dt.strftime('%Y-%m')
    # Find the YoY column (nt_yoy or similar)
    yoy_col = [c for c in cpi.columns if 'yoy' in c.lower()]
    if yoy_col:
        cpi['cpi_yoy'] = cpi[yoy_col[0]]
    else:
        # Fallback: try to find the main value column
        cpi['cpi_yoy'] = np.nan
    frames['cpi'] = cpi[['date', 'cpi_yoy']].copy()

    # --- PPI ---
    ppi = pd.read_csv(STORE / 'ppi.csv')
    ppi['date'] = pd.to_datetime(ppi['month'].astype(str), format='%Y%m').dt.strftime('%Y-%m')
    frames['ppi'] = ppi[['date', 'ppi_yoy']].copy()

    # --- PMI (raw Tushare with code columns) ---
    pmi = pd.read_csv(STORE / 'pmi.csv')
    if 'MONTH' in pmi.columns:
        pmi['date'] = pd.to_datetime(pmi['MONTH'].astype(str), format='%Y%m').dt.strftime('%Y-%m')
        # PMI010403 = 制造业PMI, PMI011000 = 非制造业PMI
        pmi_mfg_col = 'PMI010403' if 'PMI010403' in pmi.columns else None
        pmi_nonmfg_col = 'PMI011000' if 'PMI011000' in pmi.columns else None
        
        if pmi_mfg_col and pmi_nonmfg_col:
            pmi = pmi[['date', pmi_mfg_col, pmi_nonmfg_col]].copy()
            pmi.columns = ['date', 'pmi_mfg', 'pmi_nonmfg']
            pmi = pmi.sort_values('date').reset_index(drop=True)
            # Compute YoY (12-month change as percentage)
            pmi['pmi_mfg_yoy'] = pmi['pmi_mfg'].pct_change(12) * 100
            pmi['pmi_nonmfg_yoy'] = pmi['pmi_nonmfg'].pct_change(12) * 100
    else:
        # Fallback: simplified format from akshare
        pmi['date'] = pd.to_datetime(pmi['月份'].str.replace(r'年|月份', '', regex=True), format='%Y%m').dt.strftime('%Y-%m')
        pmi = pmi.rename(columns={'制造业-指数': 'pmi_mfg', '制造业-同比增长': 'pmi_mfg_yoy',
                                    '非制造业-指数': 'pmi_nonmfg', '非制造业-同比增长': 'pmi_nonmfg_yoy'})
    frames['pmi'] = pmi[['date', 'pmi_mfg', 'pmi_mfg_yoy', 'pmi_nonmfg', 'pmi_nonmfg_yoy']].copy()

    # --- Money Supply ---
    ms = pd.read_csv(STORE / 'money_supply.csv')
    ms['date'] = pd.to_datetime(ms['month'].astype(str), format='%Y%m').dt.strftime('%Y-%m')
    ms['m1_m2_scissors'] = ms['m1_yoy'] - ms['m2_yoy']
    frames['ms'] = ms[['date', 'm2_yoy', 'm1_yoy', 'm1_m2_scissors']].copy()

# --- Credit (akshare format: 月份=2026年04月份, 当月-同比增长) ---
    credit = pd.read_csv(STORE / 'credit.csv')
    credit['date'] = credit['月份'].str.replace(r'年|月份', '', regex=True)
    credit['date'] = pd.to_datetime(credit['date'], format='%Y%m').dt.strftime('%Y-%m')
    credit = credit.rename(columns={'当月-同比增长': 'credit_yoy'})
    frames['credit'] = credit[['date', 'credit_yoy']].copy()

    # --- LPR (daily -> monthly: last value of month) ---
    lpr = pd.read_csv(STORE / 'lpr.csv')
    lpr['date_raw'] = pd.to_datetime(lpr['date'])
    lpr['date'] = lpr['date_raw'].dt.strftime('%Y-%m')
    lpr_monthly = lpr.groupby('date').agg({'1y': 'last', '5y': 'last'}).reset_index()
    lpr_monthly.columns = ['date', 'lpr_1y', 'lpr_5y']
    frames['lpr'] = lpr_monthly

    # --- SHIBOR (akshare format: date, bank, on_b, on_a, 1y_b, 1y_a) ---
    shibor = pd.read_csv(STORE / 'shibor.csv')
    shibor['shibor_on'] = shibor['on_a'].astype(float)
    shibor['shibor_1y'] = shibor['1y_a'].astype(float)
    shibor['month'] = pd.to_datetime(shibor['date']).dt.strftime('%Y-%m')
    shibor_monthly = shibor.groupby('month').agg({'shibor_on': 'mean', 'shibor_1y': 'mean'}).reset_index()
    shibor_monthly.columns = ['date', 'shibor_on', 'shibor_1y']
    frames['shibor'] = shibor_monthly

    return frames


def compute_macro_monthly(frames):
    """Join all frames on date and compute derived columns."""
    # Start with PMI (has good coverage from ~2005)
    result = frames['pmi'].copy()
    
    # Outer join others
    for key in ['cpi', 'ppi', 'ms', 'credit', 'lpr', 'shibor']:
        df = frames[key]
        result = result.merge(df, on='date', how='outer')
    
    # Sort and forward-fill sparse columns (LPR, SHIBOR)
    result = result.sort_values('date').reset_index(drop=True)
    for col in ['lpr_1y', 'lpr_5y', 'shibor_on', 'shibor_1y']:
        result[col] = result[col].ffill()
    
    # Compute derived columns
    result['cpi_ppi_gap'] = result['cpi_yoy'] - result['ppi_yoy']
    result['shibor_lpr_spread'] = result['shibor_1y'] - result['lpr_1y']
    
    return result


def compute_macro_state(monthly):
    """Add z-scores and macro state classification."""
    # Copy monthly columns
    state = monthly.copy()
    
    # Rolling z-scores (24-month window)
    window = 24
    for col, z_col in [
        ('m1_m2_scissors', 'm1m2_z'),
        ('credit_yoy', 'credit_z'),
        ('cpi_ppi_gap', 'cpi_ppi_z'),
        ('shibor_lpr_spread', 'shibor_z'),
        ('pmi_mfg', 'pmi_z'),
    ]:
        if col in state.columns:
            roll_mean = state[col].rolling(window, min_periods=12).mean()
            roll_std = state[col].rolling(window, min_periods=12).std()
            state[z_col] = (state[col] - roll_mean) / roll_std
    
    # Money-credit composite
    state['money_credit'] = (state.get('m1m2_z', 0) + state.get('credit_z', 0)) / 2
    
    # Macro state classification
    def classify(row):
        pmi_z = row.get('pmi_z', 0)
        credit_z = row.get('credit_z', 0)
        if pd.isna(pmi_z) or pd.isna(credit_z):
            return 'unknown'
        if pmi_z > 0.5 and credit_z > 0.5:
            return 'expansion'
        elif pmi_z < -0.5 or credit_z < -0.5:
            return 'recession'
        else:
            return 'neutral'
    
    state['macro_state'] = state.apply(classify, axis=1)
    
    return state


def main():
    print('Loading macro datasets...')
    frames = load_and_normalize()
    for k, v in frames.items():
        print(f'  {k}: {len(v)} rows, date range {v["date"].min()} ~ {v["date"].max()}')
    
    print('Computing macro_monthly...')
    monthly = compute_macro_monthly(frames)
    monthly.to_csv(STORE / 'macro_monthly.csv', index=False)
    print(f'  saved macro_monthly.csv: {len(monthly)} rows')
    
    print('Computing macro_state...')
    state = compute_macro_state(monthly)
    state.to_csv(STORE / 'macro_state.csv', index=False)
    print(f'  saved macro_state.csv: {len(state)} rows')


if __name__ == '__main__':
    main()
