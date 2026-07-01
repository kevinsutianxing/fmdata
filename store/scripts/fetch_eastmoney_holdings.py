import re
from io import StringIO
from pathlib import Path
import requests
import pandas as pd

OUT=Path('/home/ubuntu/fmdata/store/market')
OUT.mkdir(parents=True,exist_ok=True)

def clean_html(txt):
    m=re.search(r'content:"(.*)",arryear',txt,re.S)
    if not m:
        m=re.search(r'content:"(.*)"\s*}',txt,re.S)
    html=m.group(1) if m else txt
    html=html.replace('\\"','"').replace('\\/','/')
    return html

def parse(year):
    url=f'https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code=009244&topline=10&year={year}'
    r=requests.get(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://fundf10.eastmoney.com/ccmx_009244.html'},timeout=30)
    r.encoding='utf-8'
    html=clean_html(r.text)
    title_dates=re.findall(r'(\d{4}年\d季度股票投资明细).*?截止至：<font class=.px12.>(\d{4}-\d{2}-\d{2})</font>', html)
    tables=pd.read_html(StringIO(html))
    rows=[]
    for i,t in enumerate(tables):
        date=title_dates[i][1] if i < len(title_dates) else None
        title=title_dates[i][0] if i < len(title_dates) else None
        t=t.copy()
        t.columns=[str(c).replace('\n','').replace(' ','') for c in t.columns]
        t['报告期']=date
        t['标题']=title
        rows.append(t)
    return pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()

all_rows=[]
for y in range(2021,2027):
    try:
        df=parse(y)
        print(y, df.shape, sorted(df['报告期'].dropna().unique().tolist()) if not df.empty else [])
        all_rows.append(df)
    except Exception as e:
        print(y,'ERR',repr(e))
all_df=pd.concat(all_rows,ignore_index=True)
all_df=all_df.dropna(subset=['股票代码','股票名称'], how='any')
# normalize useful columns
for c in all_df.columns:
    if '占净值' in c:
        all_df['占净值比例']=all_df[c].astype(str).str.replace('%','',regex=False).astype(float)/100
    if '持仓市值' in c:
        all_df['持仓市值万元']=pd.to_numeric(all_df[c], errors='coerce')
all_df['股票代码']=all_df['股票代码'].astype(str).str.zfill(6)
all_df.to_csv(OUT/'eastmoney_009244_top10_holdings_raw.csv',index=False,encoding='utf-8-sig')
print('rows',len(all_df),'periods',all_df['报告期'].nunique())
print(all_df[['报告期','序号','股票代码','股票名称','占净值比例','持仓市值万元']].head(15).to_string(index=False))
print(all_df[['报告期','序号','股票代码','股票名称','占净值比例','持仓市值万元']].tail(15).to_string(index=False))
