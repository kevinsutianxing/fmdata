#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_szse_etf_pcf.py — 深市ETF PCF抓取(妙想MCP/Choice专题报表源)
链路: mxapi.eastmoney.com/mxds/mcp (streamable-http) -> mx_fund_finance_data -> 转置表展开
     -> 追加到 fmdata store/reference/etf_pcf_components.csv (与沪市同表,代码前缀区分: 51x/56x/58x=沪, 15x/16x=深)
key: 读 ~/.fmdata_mx_key (chmod 600, 不入代码库)
用法: python3 fetch_szse_etf_pcf.py                # 白名单
      python3 fetch_szse_etf_pcf.py 159915 159901  # 指定
深市主动ETF(2026-10-19获批9只): 南方/富国/大成/鹏华/工银瑞信/华宝/国泰/天弘/建信, 代码上市后回填 ACTIVE_ETF_WATCHLIST_SZ
"""
import sys, os, json, time, uuid, urllib.request
import pandas as pd

MCP_URL = 'https://mxapi.eastmoney.com/mxds/mcp'
KEY_FILE = os.path.expanduser('~/.fmdata_mx_key')
C_CSV = '/home/ubuntu/fmdata/store/reference/etf_pcf_components.csv'
STREAK_FILE = os.path.expanduser('~/fmdata/logs/.pcf_sz_empty_streak')
EMPTY_ALERT_STREAK = 2  # 连续空返回达到该次数才 exit 1(触发 cron 飞书告警); 2026-11-25 修 SZ3-004

ACTIVE_ETF_WATCHLIST_SZ = {
    # 'XXXXXX': '南方均衡配置主动ETF',
    # 'XXXXXX': '富国价值优选量化选股主动ETF',
    # 'XXXXXX': '大成红利智选主动ETF',
    # 'XXXXXX': '鹏华价值臻选主动ETF',
    # 'XXXXXX': '工银瑞信红利主动ETF',
    # 'XXXXXX': '华宝均衡精选主动ETF',
    # 'XXXXXX': '国泰鑫汇均衡收益主动ETF',
    # 'XXXXXX': '天弘均衡量化选股主动ETF',
    # 'XXXXXX': '建信均衡优选主动ETF',
}
DEFAULT_WATCH = ['159915', '159949', '159901', '159949'][:3]

def mcp_call(tool, arguments, timeout=90):
    key = open(KEY_FILE).read().strip()
    hdr = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
           'em_api_key': key}
    sid = uuid.uuid4().hex[:16]
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "fmdata-pcf", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}},
    ]
    out_texts = []
    for m in msgs:
        req = urllib.request.Request(MCP_URL, data=json.dumps(m).encode(), headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', errors='replace')
        # SSE 或 JSON 均处理
        for line in raw.split('\n'):
            if line.startswith('data:'):
                payload = line[5:].strip()
                if payload and payload != '[DONE]':
                    try:
                        j = json.loads(payload)
                        if j.get('id') == 2:
                            res = j.get('result', {})
                            tc = res.get('content', [])
                            for c in tc:
                                if c.get('type') == 'text':
                                    out_texts.append(c['text'])
                    except json.JSONDecodeError:
                        pass
            elif line.strip().startswith('{'):
                try:
                    j = json.loads(line)
                    if j.get('id') == 2:
                        for c in j.get('result', {}).get('content', []):
                            if c.get('type') == 'text':
                                out_texts.append(c['text'])
                except json.JSONDecodeError:
                    pass
    return '\n'.join(out_texts)

def parse_mx_pcf(text):
    """妙想返回的专题报表是转置表: data[0].columns=[指标, 股票名...], items=[[指标值...]]
    sheetName含日期。展开成行。"""
    # text 内嵌 JSON
    s, e = text.find('{'), text.rfind('}')
    d = json.loads(text[s:e+1])
    tab = d['data'][0]
    cols = tab['columns']          # [指标, 名称1, 名称2...]
    items = tab['items']           # 每行一个指标的跨股票值
    sheet = tab.get('sheetName', '')
    # 日期从 sheetName 提取 (2026-10-19)
    import re
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', sheet)
    trade_date = (m.group(1)+m.group(2)+m.group(3)) if m else None
    # 代码行
    code_row = next((it for it in items if it[0] == '成分股代码'), None)
    qty_row = next((it for it in items if '股票数量' in str(it[0])), None)
    flag_row = next((it for it in items if '现金替代标志' in str(it[0])), None)
    prem_row = next((it for it in items if '溢价比例' in str(it[0])), None)
    if not code_row:
        return trade_date, []
    rows = []
    for i in range(1, len(cols)):
        code = str(code_row[i]).split('.')[0] if code_row[i] else None
        if not code or code == '-':
            continue
        def pick(r):
            v = r[i] if r and i < len(r) else None
            return v
        flag_txt = str(pick(flag_row))
        flag_map = {'允许': 1, '必须': 2, '禁止': 0}
        rows.append({
            'trade_date': trade_date, 'fund_code': None,  # 由调用方填
            'instrument_id': code, 'instrument_name': cols[i],
            'quantity': pick(qty_row),
            'substitution_flag': flag_map.get(flag_txt, flag_txt),
            # ⚠️妙想深市'现金替代溢价比例'=上限口径(如73.0),与沪市XML实际溢价(如0.34)不同,不可跨市场直接比较
            'creation_premium_rate': pick(prem_row),
            'redemption_discount_rate': None, 'active_etf': False})
    return trade_date, rows

def update_empty_streak(empty, path=STREAK_FILE):
    """连续空返回计数: empty=True 累加, False 清零。返回累计次数(持久化跨 cron 次)。"""
    try:
        with open(path) as f:
            n = int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        n = 0
    n = n + 1 if empty else 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(str(n))
    except OSError as e:
        print(f'warn: cannot persist streak: {e}')
    return n


def main():
    args = sys.argv[1:]
    codes = args if args else (list(ACTIVE_ETF_WATCHLIST_SZ) or DEFAULT_WATCH)
    all_rows = []
    for code in codes:
        q = f"{code} 最新ETF申购赎回清单成分股信息明细 股票数量 现金替代标志 溢价比例"
        try:
            text = mcp_call('mx_fund_finance_data', {'query': q})
        except Exception as e:
            print(f'{code}: MCP call failed {e}')
            continue
        if '成分股代码' not in text:
            print(f'{code}: no PCF in response ({text[:80]}...)')
            continue
        td, rows = parse_mx_pcf(text)
        for r in rows:
            r['fund_code'] = code
            r['active_etf'] = code in ACTIVE_ETF_WATCHLIST_SZ
        all_rows.extend(rows)
        print(f'{code}: {td} {len(rows)} components')
        time.sleep(2)
    if not all_rows:
        streak = update_empty_streak(True)
        print(f'no rows (consecutive empty runs={streak})')
        if streak >= EMPTY_ALERT_STREAK:
            print(f'ALERT: {streak} 次连续空返回(阈值 {EMPTY_ALERT_STREAK}), exit 1 触发 cron 飞书告警')
            sys.exit(1)
        return
    update_empty_streak(False)  # 有数据即清零连续空返回计数
    df = pd.DataFrame(all_rows)
    if os.path.exists(C_CSV):
        old = pd.read_csv(C_CSV, dtype={'fund_code': str, 'instrument_id': str})
        df = pd.concat([old, df])
        df = df.drop_duplicates(subset=['trade_date', 'fund_code', 'instrument_id'], keep='last')
    df.to_csv(C_CSV, index=False)
    print(f'saved +{len(all_rows)} rows -> {C_CSV}')

if __name__ == '__main__':
    main()
