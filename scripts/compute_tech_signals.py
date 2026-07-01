#!/usr/bin/env python3
"""Compute RSI/MACD/KDJ/Bollinger for all stocks in tech-indicators."""
import os
import sys
import pandas as pd
import pandas_ta as ta
import tushare as ts
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

STORE = os.environ.get("FMDATA_STORE", "/home/ubuntu/fmdata/store")
TOKEN = os.environ.get("TUSHARE_TOKEN", "")

def compute_signals(df):
    df = df.sort_values("trade_date").reset_index(drop=True)
    
    # RSI 6/12/24
    for period in [6, 12, 24]:
        df[f"rsi_{period}"] = ta.rsi(df["close"], length=period)
    
    # MACD (12,26,9)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df = pd.concat([df, macd], axis=1)
    
    # KDJ (9,3,3)
    low_min = df["low"].rolling(9).min()
    high_max = df["high"].rolling(9).max()
    rsv = (df["close"] - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50)
    k = pd.Series(50.0, index=df.index)
    d = pd.Series(50.0, index=df.index)
    j = pd.Series(50.0, index=df.index)
    for i in range(1, len(df)):
        k.iloc[i] = 2/3 * k.iloc[i-1] + 1/3 * rsv.iloc[i]
        d.iloc[i] = 2/3 * d.iloc[i-1] + 1/3 * k.iloc[i]
        j.iloc[i] = 3 * k.iloc[i] - 2 * d.iloc[i]
    df["k"] = k
    df["d"] = d
    df["j"] = j
    
    # Bollinger Bands (20,2)
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        df = pd.concat([df, bb], axis=1)
    
    # ATR (14)
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    
    # MA 5/10/20/60
    for period in [5, 10, 20, 60]:
        df[f"ma_{period}"] = df["close"].rolling(period).mean()
    
    return df

def main():
    pro = ts.pro_api(TOKEN)
    
    ti_path = os.path.join(STORE, "market", "tech_indicators.csv")
    if not os.path.exists(ti_path):
        log.error(f"tech_indicators.csv not found at {ti_path}")
        sys.exit(1)
    
    ti = pd.read_csv(ti_path)
    codes = ti["ts_code"].tolist()
    log.info(f"Computing signals for {len(codes)} stocks")
    
    all_results = []
    
    for i, code in enumerate(codes):
        if (i + 1) % 100 == 0:
            log.info(f"Progress: {i+1}/{len(codes)}")
        
        try:
            df = pro.daily(ts_code=code, start_date="20250101", 
                          fields="ts_code,trade_date,open,high,low,close,vol,amount")
            if df is None or df.empty:
                continue
            df = compute_signals(df)
            latest = df.iloc[-1].to_dict()
            all_results.append(latest)
        except Exception as e:
            log.warning(f"Failed {code}: {e}")
            continue
    
    result = pd.DataFrame(all_results)
    out_path = os.path.join(STORE, "market", "tech_signals.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    result.to_csv(out_path, index=False)
    log.info(f"Saved {len(result)} rows to {out_path}")

if __name__ == "__main__":
    main()
