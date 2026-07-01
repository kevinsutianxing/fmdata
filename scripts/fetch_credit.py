"""Fetch credit (social financing YoY) from AkShare."""
import pandas as pd
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy

def main():
    proxy_url = _get_qg_proxy()
    if proxy_url:
        _set_requests_proxy(proxy_url)
    
    import akshare as ak
    df = ak.macro_china_new_financial_credit()
    
    if df is None or df.empty:
        print("ERROR: no data returned")
        return
    
    # Extract month and YoY column
    df['date'] = pd.to_datetime(df['月份'].str.replace(r'年|月份', '', regex=True), format='%Y%m')
    df['date'] = df['date'].dt.strftime('%Y-%m')
    
    result = df[['date', '当月-同比增长']].copy()
    result.columns = ['date', 'credit_yoy']
    result = result.sort_values('date').reset_index(drop=True)
    
    out = STORE_DIR / 'macro/credit.csv'
    result.to_csv(out, index=False)
    print(f"OK: {len(result)} rows saved to {out}")

if __name__ == '__main__':
    main()
