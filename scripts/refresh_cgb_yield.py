#!/usr/bin/env python3
"""Fetch 中债国债收益率日线.

Source: akshare bond_china_yield
Note: akshare bond_china_yield API has been unstable since 2022.
Default call returns 2020-02 ~ 2021-01 data only.
For current risk-free rate, agents should use ~2.5% or check macro_state.
"""
import akshare as ak
import pandas as pd
from pathlib import Path
from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy

# akshare 走 QG 代理池 (东财/中债接口裸连会被封 IP)
proxy_url = _get_qg_proxy()
if proxy_url:
    _set_requests_proxy(proxy_url)

# Get whatever data akshare can return (currently 2020-02 to 2021-01)
df = ak.bond_china_yield()
gov = df[df["曲线名称"] == "中债国债收益率曲线"].copy()
gov = gov.rename(columns={
    "日期": "date",
    "1年": "yield_1y",
    "5年": "yield_5y",
    "10年": "yield_10y",
    "30年": "yield_30y",
})
gov = gov[["date", "yield_1y", "yield_5y", "yield_10y", "yield_30y"]]
gov = gov.sort_values("date").reset_index(drop=True)

# If existing file has more recent data (e.g. from manual update), keep it
out = Path("/home/ubuntu/fmdata/store/macro/cgb_yield_daily.csv")
if out.exists():
    existing = pd.read_csv(out)
    if len(existing) > len(gov):
        print(f"existing ({len(existing)} rows) > fetched ({len(gov)} rows), keeping existing")
        print(f"saved {len(existing)} rows (unchanged)")
        exit(0)

out.parent.mkdir(parents=True, exist_ok=True)
gov.to_csv(out, index=False)
print(f"saved {len(gov)} rows (source stale, covers {gov['date'].min()} to {gov['date'].max()})")
