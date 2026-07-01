"""Fetch LPR from Tushare and format date as YYYY-MM-DD."""
import pandas as pd
from fmdata.fetcher import TushareFetcher
from fmdata.config import STORE_DIR

def main():
    tushare = TushareFetcher()
    df = tushare._call("shibor_lpr", None)
    if df is None or df.empty:
        print("ERROR: no data returned")
        return
    
    # Convert date format: 20260320 -> 2020-03-20 (Tushare date is DDMMYYYY)
    # Actually Tushare shibor_lpr date format: YYYYMMDD as int, needs to be read properly
    df['date'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d')
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    df = df.sort_values('date').reset_index(drop=True)
    
    out = STORE_DIR / 'macro/lpr.csv'
    df.to_csv(out, index=False)
    print(f"OK: {len(df)} rows saved to {out}")

if __name__ == '__main__':
    main()
