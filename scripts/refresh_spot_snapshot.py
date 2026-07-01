#!/usr/bin/env python3
"""
盘中实时快照生产者 (RISK-1 Part ①)
单一受控生产者: 盘中抓全市场实时行情 → 原子覆盖写 store/market/spot_snapshot.csv (latest only)。
消费者读 GET /data/spot_snapshot (秒回), 不再各自经代理打东财 push2。

源优先级: ② 新浪 stock_zh_a_spot 经 QG 代理 (全市场~27s) → ③ 上次好快照 (stale 兜底)
  (东财 spot_em push2 重量端点经代理实测 0 行 = RISK-1 核心, 弃用; tushare realtime_quote
   实测无参只返1行非全市场, 全市场需分批ban风险高, 弃用。新浪源是唯一可用全市场实时源)

fmdata 生产脚本例外 (~/fmdata/scripts/refresh_*), 允许直连 akshare;
akshare 类必须套 QG 代理 (_get_qg_proxy/_set_requests_proxy, 照 fetch_shibor.py)。
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

FMDATA_HOME = Path.home() / 'fmdata'
sys.path.insert(0, str(FMDATA_HOME))
OUT_CSV = FMDATA_HOME / 'store' / 'market' / 'spot_snapshot.csv'

logging.basicConfig(filename='/tmp/spot_snapshot.log', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('spot_snapshot')


def _in_trading_hours():
    """盘中 9:25-15:00 (周一-五) 才抓; 否则 no-op (盘后行情走 EOD daily)。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 25) <= t <= (15 * 60)


def _serve_stale():
    """源 ③: 上次好快照。返回 list[dict] 或 None。"""
    try:
        import pandas as pd
        if OUT_CSV.exists():
            df = pd.read_csv(OUT_CSV)
            if not df.empty:
                log.info("served stale snapshot (%d rows)", len(df))
                return df.to_dict(orient='records')
    except Exception as e:
        log.warning("stale read failed: %s", e)
    return None


def _fetch_sina_qg():
    """源 ②: 新浪 stock_zh_a_spot 全市场实时, 经 QG 代理池 (akshare 类套代理)。
    新浪源走分页批量(~70 页 ~27s), 单生产者+缓存可接受; N 消费者各自打则必封。
    注: 东财 spot_em(push2 重量端点)经代理实测 0 行(RISK-1 核心), 故用新浪源。"""
    import os
    os.environ['TQDM_DISABLE'] = '1'  # 静默分页进度条
    from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy
    import akshare as ak
    proxy = _get_qg_proxy()
    if not proxy:
        log.warning("no QG proxy this tick; skip sina fetch")
        return None
    _set_requests_proxy(proxy)
    try:
        df = ak.stock_zh_a_spot()  # 新浪全市场 ~5500 行, ~27s
    except Exception as e:
        log.warning("新浪 stock_zh_a_spot failed: %s", e)
        return None
    finally:
        _set_requests_proxy(None)
    if df is None or df.empty:
        return None
    out = []
    for _, r in df.iterrows():
        # 新浪源代码带 sh/sz/bj 前缀 (sh600000) → 统一为 fmdata tushare 格式 600000.SH
        raw = str(r.get('代码', ''))
        if len(raw) > 6 and raw[:2] in ('sh', 'sz', 'bj'):
            ts_code = f"{raw[2:]}.{raw[:2].upper()}"
        else:
            ts_code = raw
        out.append({
            'ts_code': ts_code,
            'name': str(r.get('名称', '')),
            'close': float(r.get('最新价', 0) or 0),
            'pct_chg': float(r.get('涨跌幅', 0) or 0),
            'vol': float(r.get('成交量', 0) or 0),
            'amount': float(r.get('成交额', 0) or 0),
        })
    return out


def _write(rows, source_tag):
    """原子覆盖写 (tmp + rename), 加 capture_ts / source 列。"""
    import pandas as pd
    capture_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in rows:
        r['capture_ts'] = capture_ts
        r['source'] = source_tag
    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_CSV.with_suffix('.csv.tmp')
    df.to_csv(tmp, index=False)
    tmp.rename(OUT_CSV)
    log.info("wrote %d rows from %s @ %s", len(rows), source_tag, capture_ts)


def main():
    if not _in_trading_hours():
        log.info("outside trading hours; skip")
        return 0
    rows = _fetch_sina_qg()              # 源 ② (新浪全市场, 经 QG 代理)
    if rows:
        _write(rows, 'sina')
        return 0
    if _serve_stale() is not None:        # 源 ③ (stale 已在盘上, 无需重写)
        log.info("no fresh data; kept stale snapshot")
        return 0
    log.warning("no data this tick (no fresh, no stale)")
    return 1


if __name__ == '__main__':
    sys.exit(main())
