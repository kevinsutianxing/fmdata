#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_sse_etf_pcf.py — 上交所ETF申购赎回清单(PCF)每日抓取
数据源(官方, 页面逆向验证于2026-10-19):
  列表: query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_PL_ETFGGSGSHQD_L&ETF_CLASS=01|02|03|04
  文件: query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={code}
  披露时点: 每交易日 08:30 (上交所2011年通知沿用至今)
输出:
  store/reference/etf_pcf_header.csv      每日清单头部(净值/最小申赎单位/预估现金差额)
  store/reference/etf_pcf_components.csv  成分券明细(代码/名称/股数/现金替代标志/溢价折价率)
用法:
  python3 fetch_sse_etf_pcf.py            # 白名单模式(watchlist)
  python3 fetch_sse_etf_pcf.py --all      # 全部沪市ETF(约540只, 较慢)
  python3 fetch_sse_etf_pcf.py 510050 510300   # 指定代码
主动ETF白名单: 2026-10-19首批获批18只中9只将登陆上交所, 代码待上市公告后加入 ACTIVE_ETF_WATCHLIST
"""
import sys, os, json, time, urllib.request, urllib.parse, xml.etree.ElementTree as ET
import pandas as pd

HDR = {'Referer': 'https://www.sse.com.cn/', 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
STORE = '/home/ubuntu/fmdata/store/reference'
H_CSV, C_CSV = f'{STORE}/etf_pcf_header.csv', f'{STORE}/etf_pcf_components.csv'

# 首批主动ETF(上交所9只, 代码上市公告后回填; 获批日2026-10-19)
ACTIVE_ETF_WATCHLIST = {
    # 'XXXXXX': '易方达品质均衡主动ETF',
    # 'XXXXXX': '华夏质量甄选主动ETF',
    # 'XXXXXX': '永赢景气均衡主动ETF',
    # 'XXXXXX': '摩根大盘成长主动ETF',
    # 'XXXXXX': '华泰柏瑞价值精选主动ETF',
    # 'XXXXXX': '汇添富均衡策略智选主动ETF',
    # 'XXXXXX': '华安均衡慧选主动ETF',
    # 'XXXXXX': '招商价值智选主动ETF',
    # 'XXXXXX': '平安红利景气主动ETF',
}
# 默认验证白名单(头部宽基, 用于管道健康监控)
DEFAULT_WATCH = ['510050', '510300', '510500', '588000', '512100']

def http_get(url, timeout=20, tries=3):
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if t == tries - 1:
                print(f'FAIL {url[:80]}: {e}')
                return None
            time.sleep(2 + t * 2)

def list_etfs(etf_class='01'):
    """拉某类ETF当日清单列表(分页)"""
    out, page = [], 1
    while page <= 30:
        q = urllib.parse.urlencode({'isPagination': 'true', 'pageHelp.pageSize': 100,
            'pageHelp.pageNo': page, 'sqlId': 'COMMON_SSE_PL_ETFGGSGSHQD_L',
            'ETF_CLASS': etf_class, 'type': 'inParams', 'FUND_CODE': '', 'KEY_WORDS': ''})
        raw = http_get(f'https://query.sse.com.cn/commonQuery.do?{q}')
        if raw is None:
            break
        d = json.loads(raw)
        rows = d.get('pageHelp', {}).get('data') or []
        if not rows:
            break
        out.extend(rows)
        total = d.get('pageHelp', {}).get('total') or 0
        if len(out) >= int(total):
            break
        page += 1
        time.sleep(0.4)
    return out

def parse_pcf(xml_bytes):
    """解析SSE PCF XML -> (header_dict, components_list)"""
    root = ET.fromstring(xml_bytes)
    def g(tag, default=None):
        el = root.find(tag)
        return el.text if el is not None else default
    hdr = {
        'trade_date': g('TradingDay'), 'fund_code': g('FundInstrumentID'),
        'creation_unit': g('CreationRedemptionUnit'), 'nav': g('NAV'),
        'nav_per_cu': g('NAVperCU'), 'pre_cash': g('PreCashComponent'),
        'est_cash': g('EstimatedCashComponent'), 'max_cash_ratio': g('MaxCashRatio'),
        'record_number': g('RecordNumber'), 'publish_iopv': g('PublishIOPVFlag'),
        'cr_switch': g('CreationRedemptionSwitch'),
    }
    comps = []
    for c in root.findall('.//Component'):
        def cg(tag):
            el = c.find(tag)
            return el.text if el is not None else None
        comps.append({'instrument_id': cg('InstrumentID'), 'instrument_name': cg('InstrumentName'),
                      'quantity': cg('Quantity'), 'substitution_flag': cg('SubstitutionFlag'),
                      'creation_premium_rate': cg('CreationPremiumRate'),
                      'redemption_discount_rate': cg('RedemptionDiscountRate')})
    return hdr, comps

def main():
    args = sys.argv[1:]
    codes = []
    if args and args[0] == '--all':
        allrows = []
        for cls in ['01', '02', '03', '04']:
            allrows.extend(list_etfs(cls))
        codes = sorted({r.get('FUNDID2') for r in allrows if r.get('FUNDID2')})
        print(f'ALL mode: {len(codes)} ETFs')
    elif args:
        codes = args
    else:
        codes = list(ACTIVE_ETF_WATCHLIST) or DEFAULT_WATCH
        if not ACTIVE_ETF_WATCHLIST:
            codes = DEFAULT_WATCH

    h_new, c_new = [], []
    for code in codes:
        raw = http_get(f'https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={code}')
        if raw is None or b'PortfolioComposition' not in raw[:500]:
            print(f'  {code}: no PCF')
            continue
        try:
            hdr, comps = parse_pcf(raw)
        except ET.ParseError as e:
            print(f'  {code}: XML parse error {e}')
            continue
        name = ACTIVE_ETF_WATCHLIST.get(code, '')
        hdr['fund_code'] = code
        h_new.append(hdr)
        for c in comps:
            c.update({'trade_date': hdr['trade_date'], 'fund_code': code, 'active_etf': code in ACTIVE_ETF_WATCHLIST})
            c_new.append(c)
        print(f"  {code}: {hdr['trade_date']} {len(comps)} components nav={hdr['nav']}")
        time.sleep(0.5)

    if not h_new:
        print('no data fetched'); return
    hdf, cdf = pd.DataFrame(h_new), pd.DataFrame(c_new)
    for path, df in [(H_CSV, hdf), (C_CSV, cdf)]:
        if os.path.exists(path):
            old = pd.read_csv(path, dtype={'fund_code': str, 'instrument_id': str})
            df = pd.concat([old, df])
            df = df.drop_duplicates(subset=[c for c in ['trade_date', 'fund_code', 'instrument_id'] if c in df.columns], keep='last')
        df.to_csv(path, index=False)
    print(f'saved: header +{len(h_new)} rows -> {H_CSV}')
    print(f'saved: components +{len(c_new)} rows -> {C_CSV}')

if __name__ == '__main__':
    main()
