#!/usr/bin/env python3
"""美股个股/ETF日线(前复权) → store/market/us_equity_kline_em.csv (global-volatility-risk-monitor 件, 2026-11-12)。

动机: CBOE delayed charts 偶发滞后整天(2026-11-12 实测美股收盘21h后仍无前日日K),
东财美股EOD当日北京午后即有 → GVRM 美股个股/ETF 换成此源为主, cboe 退居备源。

通道: eastmoney_get(push2his + QG 代理池 IP 轮换) — 禁裸连东财。
  🔴 fqt=1 前复权(分红+拆股复权): 与指数件 fetch_index_global_monitor.py 的 fqt=0 不同——
  个股必须复权, 未复权序列在拆股日有假跳变(NVDA 2024-06 十拆一 = 假-90%日收益)。
  GVRM 侧标 price_type=qfq_adjusted_close + adj_close=close(质量分5)。

增量语义(v4, 2026-11-12 改: 用户指出"天天全量"无理, 已重推数学改正):
  前复权锚只在除权除息日漂移(每股每年0-4次), 平常日历史一行不动 → 纯增量 append 即精确正确。
  无状态文件, CSV 自身即真相, 每次运行自校验:
    factor_now = qfq_last/hfq_last  (两口径尾根同日算出; 数学上 ≡ 1/累计复权因子)
    锚未漂移 ⟺ CSV 最后一根 close == hfq 同日 close × factor_now
    等式成立 → 只 append 尾窗内新日期(平常日 0~1 根, 流量 ~100 倍降, 代理池友好)
    等式破裂(除权锚漂移/源端改史) → 该标的全量重拉整段重写, 序列恒同锚自洽(无假收益锚缝)
    断档超尾窗(停牌/cron停摆>40根) → 该标的全量
  🔴 禁改回"无校验纯append": 除权日不重锚 = 拼接处假跳空。校验是正确性机制, 不是开销。

价格口径: hfq 全史 × factor 重定基 ≡ 锚定今天的前复权。日收益/回撤与 qfq 恒等, 全序列>0
  (EM 原生 fqt=1 深历史可 ≤0, AAPL 1990s 低至 -10.45, 不可直接用; hfq 永不为负)。

市场前缀(2026-11-12 实测锚定): 105=NASDAQ / 106=NYSE / 107=AMEX(NYSE American/Arca)。
  TSM(NYSE ADR)=106.TSM。返回名锚定防 secid 静默漂移(中文名为主, 部分美股返回英文名, 原样锚定)。

cron: 06:15 / 15:25 (1-5) 专用直跑(摘出 06:00 refresh-overseas 批: 与 us_stock_spot/cboe_iv_surface
  同先例, 批内 POST --max-time 90 不等长任务, 且失败面隔离)。平常日 ~4-5min(36标的×2小调用, 串行
  走代理池, 开销在握手非流量), 须在 15:40 sync 前跑完 → 15:25 槽(15:35 会撞 15:40)。失败飞书告警。

用法: python3 fetch_us_equity_kline.py [--symbols AAPL,NVDA]
输出列: ts_code, trade_date(YYYYMMDD), open, high, low, close, volume, amount, name, source(em_kline_hfq_rebased)
"""
import argparse
import sys
import time

sys.path.insert(0, "/home/ubuntu/fmdata")
from fmdata.config import STORE_DIR  # noqa: E402

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58"
COLS = ["date", "open", "close", "high", "low", "volume", "amount", "amp"]
TAIL_BARS = 40  # 快路径尾窗: 覆盖节假日+短期停摆; 断档超窗自动转全量

OUT = STORE_DIR / "market" / "us_equity_kline_em.csv"

# ts_code -> (secid, 东财返回名锚定)  [2026-11-12 逐个实测, 全部含当日(11-11)K]
SECIDS = {
    "AAPL": ("105.AAPL", "苹果"),
    "AMAT": ("105.AMAT", "应用材料"),
    "AMD": ("105.AMD", "超威半导体"),
    "AMZN": ("105.AMZN", "亚马逊"),
    "ANET": ("106.ANET", "Arista Networks Inc"),
    "ASML": ("105.ASML", "阿斯麦"),
    "AVGO": ("105.AVGO", "博通"),
    "CDNS": ("105.CDNS", "铿腾电子"),
    "CEG": ("105.CEG", "Constellation Energy Corp"),
    "CRDO": ("105.CRDO", "Credo Technology Group Holding "),
    "CRM": ("106.CRM", "赛富时"),
    "DDOG": ("105.DDOG", "Datadog Inc-A"),
    "ETN": ("106.ETN", "伊顿"),
    "GOOGL": ("105.GOOGL", "谷歌-A"),
    "IWM": ("107.IWM", "罗素2000ETF-iShares"),
    "KLAC": ("105.KLAC", "科磊"),
    "LRCX": ("105.LRCX", "拉姆研究"),
    "META": ("105.META", "Meta Platforms Inc-A"),
    "MRVL": ("105.MRVL", "迈威尔科技"),
    "MSFT": ("105.MSFT", "微软"),
    "MU": ("105.MU", "美光科技"),
    "NOW": ("106.NOW", "现在服务公司"),
    "NVDA": ("105.NVDA", "英伟达"),
    "ORCL": ("106.ORCL", "甲骨文"),
    "PLTR": ("105.PLTR", "Palantir Technologies Inc-A"),
    "QCOM": ("105.QCOM", "高通"),
    "QQQ": ("105.QQQ", "纳斯达克100ETF-Invesco"),
    "SMH": ("105.SMH", "半导体指数ETF-VanEck"),
    "SNPS": ("105.SNPS", "新思科技"),
    "SOXQ": ("105.SOXQ", "Invesco PHLX Semiconductor ETF"),
    "SPY": ("107.SPY", "标普500ETF-SPDR"),
    "TSLA": ("105.TSLA", "特斯拉"),
    "TSM": ("106.TSM", "台积电"),
    "VRT": ("106.VRT", "Vertiv Holdings Co-A"),
    "VST": ("106.VST", "Vistra Corp"),
    "XLK": ("107.XLK", "科技行业精选指数ETF-SPDR"),
}


def _get_kline(secid, expect_name, fqt, lmt):
    """东财 kline 拉取: QG 代理池 IP 轮换重试 + 名称锚定检查。返回 df(COLS)+name 列。"""
    from fmdata.recipe_fetcher import eastmoney_get

    last_err = None
    for _attempt in range(4):
        try:
            js = eastmoney_get(KLINE_URL, {
                "secid": secid, "klt": "101", "fqt": str(fqt), "lmt": str(lmt),
                "beg": "19900101", "end": "20500000",
                "fields1": "f1,f2,f3,f4,f5,f6", "fields2": FIELDS2})
            data = (js or {}).get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                raise RuntimeError(f"no klines fqt={fqt} (name={data.get('name')})")
            import pandas as pd
            df = pd.DataFrame([k.split(",") for k in klines], columns=COLS)
            df["name"] = data.get("name", "")
            got = df["name"].iloc[0]
            if got != expect_name:  # 锚定检查: 防 secid 静默漂移取错标的
                raise RuntimeError(f"name drift: got {got!r} expect {expect_name!r}")
            return df
        except Exception as e:  # noqa: BLE001 — IP 轮换重试
            last_err = e
            time.sleep(1.5)
    raise RuntimeError(f"secid={secid} fqt={fqt} failed after retries: {last_err}")


def fetch_full(ts_code, secid, expect_name):
    """全量路径: hfq 全史 + 近端 qfq 锚 → 重定基。返回 (原始hfq df, factor)。"""
    hfq = _get_kline(secid, expect_name, 2, 50000)
    qfq_tail = _get_kline(secid, expect_name, 1, 5)
    # 对齐最后一根: hfq/qfq 最后一根日期必须一致(同为最新交易日)
    if hfq["date"].iloc[-1] != qfq_tail["date"].iloc[-1]:
        raise RuntimeError(f"last-bar mismatch hfq={hfq['date'].iloc[-1]} qfq={qfq_tail['date'].iloc[-1]}")
    hfq_last_close = float(hfq["close"].iloc[-1])
    qfq_last_close = float(qfq_tail["close"].iloc[-1])
    if hfq_last_close <= 0 or qfq_last_close <= 0:
        raise RuntimeError(f"nonpositive anchor hfq={hfq_last_close} qfq={qfq_last_close}")
    return hfq, qfq_last_close / hfq_last_close


def try_incremental(secid, expect_name, sym_existing):
    """快路径: 尾窗自校验一致 → 返回新行 df(可为空=已最新); 校验破裂/断档/无历史 → None 走全量。

    自校验(无状态文件, CSV 即真相): factor_now 由当日两口径尾根算出;
    锚未漂移时 CSV 最后一根 close 必 == hfq 同日 close × factor_now(上次写库用的同一因子)。
    """
    import pandas as pd
    if sym_existing is None or sym_existing.empty:
        return None
    hfq_tail = _get_kline(secid, expect_name, 2, TAIL_BARS)
    qfq_tail = _get_kline(secid, expect_name, 1, 5)
    if hfq_tail["date"].iloc[-1] != qfq_tail["date"].iloc[-1]:
        return None  # 两口径尾根不同日: 源端半更新, 交全量路径判错
    factor_now = float(qfq_tail["close"].iloc[-1]) / float(hfq_tail["close"].iloc[-1])
    tail_dates = hfq_tail["date"].str.replace("-", "", regex=False)
    last_date = str(sym_existing["trade_date"].max())
    if last_date not in set(tail_dates):
        return None  # 断档超尾窗(停牌/cron停摆>TAIL_BARS根): 全量
    row = hfq_tail[tail_dates == last_date].iloc[-1]
    old_close = float(sym_existing[sym_existing["trade_date"] == last_date]["close"].iloc[-1])
    expect_close = float(row["close"]) * factor_now
    if abs(expect_close - old_close) > 1e-6 * max(abs(old_close), 1.0):
        return None  # 除权锚漂移或源端改史: 全量重锚(阈值1e-6, 真实除权漂移≥0.01%量级)
    new = hfq_tail[tail_dates > last_date].copy()
    new["date"] = tail_dates[tail_dates > last_date]
    return new, factor_now


def _to_out(ts_code, df, factor):
    """hfq 原始 df × factor → 输出宽表(前复权口径)。"""
    import pandas as pd
    return pd.DataFrame({
        "ts_code": ts_code,
        "trade_date": df["date"].str.replace("-", "", regex=False),
        "open": pd.to_numeric(df["open"], errors="coerce") * factor,
        "high": pd.to_numeric(df["high"], errors="coerce") * factor,
        "low": pd.to_numeric(df["low"], errors="coerce") * factor,
        "close": pd.to_numeric(df["close"], errors="coerce") * factor,
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
        "amount": pd.to_numeric(df["amount"], errors="coerce"),
        "name": df["name"],
        "source": "em_kline_hfq_rebased",
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔子集(默认全部; 排障用)")
    args = ap.parse_args()
    wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(SECIDS)

    import pandas as pd
    existing = pd.read_csv(OUT, dtype={"trade_date": str}) if OUT.exists() else None

    from fmdata.recipe_fetcher import _get_qg_proxy
    proxy = _get_qg_proxy() or ""
    host = proxy.split("@")[-1].split(":")[0] if proxy else "NONE"
    print(f"[proxy-proof] QG exit IP host: {host}", flush=True)

    failed = []
    for ts_code in wanted:
        secid, expect_name = SECIDS[ts_code]
        sym_existing = existing[existing.ts_code == ts_code] if existing is not None else None
        try:
            inc = None
            try:
                inc = try_incremental(secid, expect_name, sym_existing)
            except Exception as e:  # noqa: BLE001 — 快路径网络失败不弃标的, 降级全量
                print(f"[incr-degrade→full] {ts_code}: {e}", flush=True)
            if inc is None:
                hfq, factor = fetch_full(ts_code, secid, expect_name)
                out = _to_out(ts_code, hfq, factor)
                # 深度保护: 全量结果比库存断崖式缩水(源端截断/lmt被改) → 拒写告警, 不静默毁史
                if sym_existing is not None and len(out) < 0.6 * len(sym_existing):
                    raise RuntimeError(f"depth collapse {len(sym_existing)}→{len(out)}, refuse overwrite")
                replace = True
                mode = f"full rows={len(out)}"
            else:
                new, factor_now = inc
                out = _to_out(ts_code, new, factor_now)
                replace = False
                mode = f"incr rows={len(out)}"
            if not out.empty:
                closes = out["close"].dropna()
                # hfq×正因子全序列必>0; ≤0 = 重定基/源端异常 → 失败告警不落盘
                if closes.min() <= 0:
                    raise RuntimeError(f"close sanity FAIL (min={closes.min()})")
                if replace:
                    n_min = 100  # 全量: 整段历史至少百根
                else:
                    n_min = 1    # 增量: 只查新行
                if len(closes) < n_min:
                    raise RuntimeError(f"row count sanity FAIL ({len(closes)}<{n_min})")
                if existing is not None and not existing.empty and replace:
                    existing = existing[existing.ts_code != ts_code]
                existing = pd.concat([existing, out], ignore_index=True) if existing is not None else out
                existing = existing.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
                tmp = OUT.with_suffix(".csv.tmp")
                existing.to_csv(tmp, index=False)  # 逐标的落盘: 断点续跑底线
                tmp.rename(OUT)
            sym_rows = existing[existing.ts_code == ts_code]
            print(f"[ok:{mode}] {ts_code}({expect_name}) "
                  f"{sym_rows['trade_date'].min()}..{sym_rows['trade_date'].max()}", flush=True)
        except Exception as e:  # noqa: BLE001 — 单标的失败不拖垮整批
            print(f"[FAIL] {ts_code}: {e}", flush=True)
            failed.append(ts_code)
            continue
        time.sleep(0.3)

    print(f"[done] {OUT} rows={len(existing)} failed={failed}", flush=True)
    if failed:
        print(f"FAILED_SYMBOLS: {','.join(failed)}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
