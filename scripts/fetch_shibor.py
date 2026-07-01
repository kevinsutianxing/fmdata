"""Fetch SHIBOR from AkShare and resample to monthly."""
import pandas as pd
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy

def main():
    # Set proxy for akshare
    proxy_url = _get_qg_proxy()
    if proxy_url:
        _set_requests_proxy(proxy_url)
    
    import akshare as ak
    df = ak.macro_china_shibor_all()
    
    if df is None or df.empty:
        print("ERROR: no data returned")
        return
    
    # Rename columns to match existing format
    df = df.rename(columns={'日期': 'date', 'O/N-定价': 'shibor_on', '1Y-定价': 'shibor_1y'})
    
    # Convert date to monthly format
    df['date'] = pd.to_datetime(df['date'])
    df['month'] = df['date'].dt.to_period('M').astype(str)
    
    # Resample to monthly: take last value of each month
    monthly = df.groupby('month').agg({'shibor_on': 'last', 'shibor_1y': 'last'}).reset_index()
    monthly.columns = ['date', 'shibor_on', 'shibor_1y']
    
    out = STORE_DIR / 'macro/shibor.csv'
    monthly.to_csv(out, index=False)
    print(f"OK: {len(monthly)} rows saved to {out}")

if __name__ == '__main__':
    main()
