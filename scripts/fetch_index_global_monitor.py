#!/usr/bin/env python3
"""五指数监控日线 → store/market/index_global_monitor_em.csv (global-volatility-risk-monitor 件1)。

指数/secid 映射(东财 push2his, 2026-11-07 searchapi 实测锚定):
  NDX     100.NDX100 纳斯达克100   🔴 100.NDX 是纳斯达克综合(IXIC), 不是 NDX100 — 勿改回
  SOX     251.SOX    费城半导体指数
  HSTECH  124.HSTECH 恒生科技指数 (源端回溯到基日 2014-12-31, 2020-07-27 上市)
  HSCEI   100.HSCEI  恒生中国企业指数
  HSI     100.HSI    恒生指数 (2026-11-07 加入; GVRM 原 HSI 仅 info_bridge 3年)
  VHSI    124.VHSI   恒指波幅指数 (15-40 量级, 错量级=取错标的)

通道: eastmoney_get(push2his + QG 代理池 IP 轮换) — 禁裸连东财。
  fqt=0 不复权(指数本无复权语义, 显式钉 0); SOX/VHSI 无成交量(volume=0 属源端事实)。

增量: 默认每指数拉最近 60 根 K 线(≈3 个月, 远超 monitor 要求的 14 天重叠窗), 按
  (ts_code, trade_date) upsert; --full 全历史(lmt=50000) 首铺用。逐指数落盘断点续跑。

SOX 源端历史边界(2026-11-07 实测): push2his kline 对 251.SOX 只给 2026-07-06 起
  (89 根)。更早的 SOX 用同属东财的 datacenter 行业指数接口 EMI00055562 补收盘
  (1994-05-04 起, 仅 close, OHL/volume 留空), source 列区分 em_kline/em_datacenter,
  两源重叠日 kline 优先(OHLC 全)。NDX100 kline 源端从 2019-04-08 起(实测无更早)。

用法: python3 fetch_index_global_monitor.py [--full]
输出列: ts_code, trade_date(YYYYMMDD), open, high, low, close, volume, amount, name, source
"""
import argparse
import sys

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR  # noqa: E402

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
COLS = ["date", "open", "close", "high", "low", "volume", "amount",
        "amp", "pct_chg", "change", "turnover"]

# ts_code -> (secid, 中文名, close 量级下限/上限 sanity 带)
# 带宽=全历史粗量级守门(NDX 含 2000s 低点 ~7000; 主要防线是上方中文名锚定检查)
SECIDS = {
    "NDX":     ("100.NDX100", "纳斯达克100",      1000, 50000),
    "SOX":     ("251.SOX",    "费城半导体指数",     100, 25000),
    "HSTECH":  ("124.HSTECH", "恒生科技指数",       500, 15000),
    "HSCEI":   ("100.HSCEI",  "国企指数",   500, 25000),
    "HSI":     ("100.HSI",    "恒生指数",          100, 45000),  # 全史回溯1969(1980s前<5000点), 防错标的靠名称锚
    "VHSI":    ("124.VHSI",   "恒指波幅指数",        5, 80),
}
OUT = STORE_DIR / "market/index_global_monitor_em.csv"
LMT_INCREMENTAL = "60"     # 最近 60 根日线
LMT_FULL = "50000"         # 全历史(东财 kline 上限, 从上市给起)


def fetch_one(ts_code, secid, lmt):
    """单指数 kline 拉取 → DataFrame(date/open/high/low/close/volume/amount/name)。"""
    from fmdata.recipe_fetcher import eastmoney_get
    js = eastmoney_get(KLINE_URL, {
        "secid": secid, "klt": "101", "fqt": "0", "lmt": lmt, "end": "20500000",
        "iscca": "1", "fields1": "f1,f2,f3,f4,f5,f6,f7,f8", "fields2": FIELDS2,
        "ut": "f057cbcbce2a86e2866ab8877db1d059", "forcect": "1"})
    data = (js or {}).get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        raise RuntimeError(f"{ts_code} secid={secid} no klines (name={data.get('name')})")
    import pandas as pd
    df = pd.DataFrame([k.split(",") for k in klines], columns=COLS)
    df["name"] = data.get("name", "")
    # 返回名必须与锚定中文名一致, 防 secid 静默漂移取错标的
    if ts_code in ("NDX", "SOX", "HSTECH", "HSCEI", "HSI", "VHSI"):
        expect = SECIDS[ts_code][1]
        if df["name"].iloc[0] != expect:
            raise RuntimeError(f"{ts_code} name drift: got {df['name'].iloc[0]!r} expect {expect!r}")
    return df


def fetch_sox_close(full):
    """SOX 收盘回填: 东财 datacenter 行业指数 EMI00055562(费城半导体), 仅 close。

    返回与主表同 schema 的 DataFrame(source=em_datacenter, OHL/volume/amount 留空)。
    full=True 翻全页(~33 页); 增量只取第 1 页(500 行≈2 年, 覆盖 14 天重叠窗绰绰)。
    """
    from fmdata.recipe_fetcher import _get_qg_proxy, _set_requests_proxy, _qg_ip_failed
    import requests
    import pandas as pd

    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    base = {"sortColumns": "REPORT_DATE", "sortTypes": "-1", "pageSize": "500",
            "reportName": "RPT_INDUSTRY_INDEX",
            "columns": "REPORT_DATE,INDICATOR_VALUE",
            "filter": '(INDICATOR_ID="EMI00055562")', "source": "WEB", "client": "WEB"}
    pages = range(1, 40) if full else range(1, 2)
    rows, last_err = [], None
    for page in pages:
        for attempt in range(3):
            try:
                _set_requests_proxy(_get_qg_proxy())
                r = requests.get(url, params={**base, "pageNumber": str(page)}, timeout=20)
                result = r.json().get("result") or {}
                data = result.get("data") or []
                rows.extend((d["REPORT_DATE"][:10].replace("-", ""), d["INDICATOR_VALUE"])
                            for d in data)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 — IP 轮换重试
                last_err = e
                _qg_ip_failed()
        if last_err is not None:
            raise RuntimeError(f"SOX datacenter page {page}: {last_err}")
        if page >= (result.get("pages") or 1):
            break
    if not rows:
        raise RuntimeError("SOX datacenter EMI00055562 returned no rows")
    df = pd.DataFrame(rows, columns=["trade_date", "close"]).drop_duplicates("trade_date")
    df.insert(0, "ts_code", "SOX")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if (~df["close"].between(100, 25000)).mean() > 0.05:
        raise RuntimeError(f"SOX datacenter close sanity FAIL: {df['close'].describe().to_dict()}")
    for col in ("open", "high", "low", "volume", "amount"):
        df[col] = float("nan")
    df["name"] = "费城半导体指数"
    df["source"] = "em_datacenter"
    return df[["ts_code", "trade_date", "open", "high", "low", "close",
               "volume", "amount", "name", "source"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全历史首铺(默认最近60根增量)")
    args = ap.parse_args()
    lmt = LMT_FULL if args.full else LMT_INCREMENTAL

    from fmdata.recipe_fetcher import _get_qg_proxy
    proxy = _get_qg_proxy() or ""
    host = proxy.split("@")[-1].split(":")[0] if proxy else "NONE"
    print(f"[proxy-proof] QG exit IP host: {host}", flush=True)

    import pandas as pd
    existing = pd.read_csv(OUT, dtype={"trade_date": str}) if OUT.exists() else None

    for ts_code, (secid, cn_name, lo, hi) in SECIDS.items():
        df = fetch_one(ts_code, secid, lmt)
        out = pd.DataFrame({
            "ts_code": ts_code,
            "trade_date": df["date"].str.replace("-", "", regex=False),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "volume": pd.to_numeric(df["volume"], errors="coerce"),
            "amount": pd.to_numeric(df["amount"], errors="coerce"),
            "name": df["name"],
            "source": "em_kline",
        })
        closes = out["close"].dropna()
        bad = (~closes.between(lo, hi)).mean() if len(closes) else 1.0
        if bad > 0.05:  # >5% 落在 sanity 带外 = 大概率取错标的, 拒绝落库
            raise RuntimeError(f"{ts_code} close sanity FAIL: {bad:.0%} outside [{lo},{hi}] "
                               f"(min={closes.min()}, max={closes.max()})")
        if existing is not None and not existing.empty:
            merged = pd.concat([existing, out], ignore_index=True)
            out = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        existing = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        tmp = OUT.with_suffix(".csv.tmp")
        existing.to_csv(tmp, index=False)  # 逐指数落盘: 断点续跑底线
        tmp.rename(OUT)
        print(f"[ok] {ts_code}({cn_name}) rows_fetched={len(df)} "
              f"range {out[out.ts_code == ts_code]['trade_date'].min()}.."
              f"{out[out.ts_code == ts_code]['trade_date'].max()} total={len(existing)}", flush=True)

    # SOX 收盘回填(datacenter EMI00055562): --full 翻全页, 增量只取第 1 页(500 行≈2 年)
    sox_new = fetch_sox_close(full=args.full)
    if existing is None:
        existing = pd.read_csv(OUT, dtype={"trade_date": str})
    kline_dates = set(existing.loc[existing.ts_code == "SOX", "trade_date"])
    sox_new = sox_new[~sox_new["trade_date"].isin(kline_dates)]  # kline 优先, 跳过重叠日
    if len(sox_new):
        existing = pd.concat([existing, sox_new], ignore_index=True)
        existing = existing.drop_duplicates(subset=["ts_code", "trade_date"], keep="first")
        existing = existing.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        tmp = OUT.with_suffix(".csv.tmp")
        existing.to_csv(tmp, index=False)
        tmp.rename(OUT)
    sox_all = existing[existing.ts_code == "SOX"]
    print(f"[ok] SOX backfill(em_datacenter) added={len(sox_new)} "
          f"SOX range {sox_all['trade_date'].min()}..{sox_all['trade_date'].max()}", flush=True)

    print(f"[done] {OUT} rows={len(existing)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
