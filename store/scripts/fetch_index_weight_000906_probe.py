#!/usr/bin/env python3
"""探测中证800(000906.SH)成分股权重的多源可达性 —— PROBE ONLY,不落 CSV。

fmdata 合规通道,禁止裸连:
  Source 1  Tushare index_weight      (token-auth,经 TushareFetcher;同 fetch_hs300_weights.py 模式)
  Source 2  akshare index_stock_cons_weight_csindex (中证指数公司官方,套 QG 代理池)

只回答"哪个源能给 / 给多少行 / 哪个交易日 / 字段 / 样本权重和"。不写文件。
"""
import sys
import time

CODE_TUSHARE = "000906.SH"
CODE_CGINDEX = "000906"  # csindex.com.cn / akshare symbol 不带 .SH


def probe_tushare():
    """试若干月末快照日(新老各几个),看 Tushare index_weight 是否覆盖 000906。"""
    from fmdata.fetcher import TushareFetcher
    f = TushareFetcher()
    # 新 + 老 各几个,判断既有覆盖也有历史深度
    dates = ["20260731", "20260630", "20260331", "20251231", "20240630", "20221230", "20210630"]
    hits = []
    for d in dates:
        try:
            df = f._call("index_weight", None, index_code=CODE_TUSHARE, trade_date=d)
        except Exception as e:
            print(f"  [tushare] {d}: EXCEPTION {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            time.sleep(0.4)
            continue
        if df is not None and not df.empty:
            wsum = float(df["weight"].sum()) if "weight" in df else float("nan")
            print(f"  [tushare] {d}: {len(df)} rows, weight_sum={wsum:.4f}", file=sys.stderr)
            hits.append((d, df))
        else:
            print(f"  [tushare] {d}: empty", file=sys.stderr)
        time.sleep(0.4)
    return hits


def probe_akshare():
    """akshare 中证指数公司权重快照(最新)。必须套 QG 代理池(CLAUDE.md 铁律)。"""
    from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy
    import akshare as ak

    p = _get_qg_proxy()
    print(f"  [akshare] proxy = {p}", file=sys.stderr)
    if p:
        _set_requests_proxy(p)
    try:
        df = ak.index_stock_cons_weight_csindex(symbol=CODE_CGINDEX)
    finally:
        _set_requests_proxy(None)
    return df


def main():
    print("=== Source 1: Tushare index_weight (000906.SH) ===", file=sys.stderr)
    ts_hits = []
    try:
        ts_hits = probe_tushare()
    except Exception as e:
        print(f"  [tushare] FATAL {type(e).__name__}: {e}", file=sys.stderr)

    if ts_hits:
        d, df = ts_hits[0]
        print(f"\n[TUSHARE OK] 最新命中 {d}: {len(df)} 只, columns={list(df.columns)}", file=sys.stderr)
        print(df.head(5).to_string(index=False), file=sys.stderr)
    else:
        print("\n[TUSHARE MISS] 所有试穿日期均空 —— Tushare index_weight 不覆盖 000906.SH", file=sys.stderr)

    print("\n=== Source 2: akshare index_stock_cons_weight_csindex (000906) ===", file=sys.stderr)
    try:
        ak_df = probe_akshare()
        if ak_df is not None and not ak_df.empty:
            wcol = "权重" if "权重" in ak_df.columns else ak_df.columns[-1]
            wsum = pd.to_numeric(ak_df[wcol].astype(str).str.replace("%", "").str.strip(), errors="coerce").sum()
            print(f"\n[AKSHARE OK] {len(ak_df)} 只, columns={list(ak_df.columns)}", file=sys.stderr)
            print(f"  权重列[{wcol}] sum={wsum:.4f}%", file=sys.stderr)
            print(ak_df.head(5).to_string(index=False), file=sys.stderr)
        else:
            print("\n[AKSHARE MISS] 返回空", file=sys.stderr)
    except Exception as e:
        print(f"\n[AKSHARE FATAL] {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)


if __name__ == "__main__":
    main()
