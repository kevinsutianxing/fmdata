#!/usr/bin/env python3
"""美股 us_equity_kline_em 收益链全史重建（gfclaw eq_us_quote_daily，2026-11-21）。

背景(P0-2 跟进+终审审计)：EM push2his 源级缺段 + EM 序列邻洞/大波动日收益错误(实测
数百 bp 至 ~100%：ETN 2005-01-03 真实-1.71%(gfclaw==cboe 逐分钱)，EM 隐含~+71%；
QCOM 2004-08-16 真实+0.98%，EM ~+101%；AMD 2015-01-21 +9.38% 被 EM 错位到次日；
NVDA 2009-09-17 -5% 被 EM 错记到 09-21)。2026-11-21 全量审计 36 标的(ratio 全收益
公式)：18 只个股"多日>1% 偏差"判 REBUILD，ETF 六只(SPX 池) gfclaw 侧数据差(2025-04
大跌日 gfclaw 与 cboe 不符而 EM==cboe)禁用重建。

收益公式(2026-11-21 终审版，替代 wind_prior_close_px)：
  ret[t] = day_last[t]/day_last[t-1] × restated_ratio[t]/restated_ratio[t-1] − 1
  —— 经典全收益构造：restated_ratio 在除息/拆股/分配日跳变(ASML 除息/TSLA 3:1/
  NVDA 10:1/GOOGL 2014 C股分配四类事件实测全对)，与 prior_close_px 在 88,271 行
  有值样本上均值差 4.4e-6。wind_prior 对 GOOGL 2014-04-03 分配类事件不调整(-49.65%
  假崩)、prior_close_px 44% NaN，均不可用。

重建方式：对每标的取 gfclaw 全序列(2000-01-03 起)，锚定 CSV 最近共同交易日的收盘
水平逐日回链 P[t-1]=P[t]/(1+ret[t])；锚点行与其后 westock 增量尾行原样保留(westock
cron 继续往后接)。OHLC 用 gfclaw 日内比例还原；volume=deal_unit_qty；amount 留空；
source='gfclaw_return_chained' 溯源。幂等：重跑逐字节一致(float_precision=round_trip)。

🔴 警告：①已重建标的禁跑 EM 全量重拉(fetch_us_equity_kline.py 全量路径会整段覆盖
  冲掉重建段)；②ETF(SPX/QQQ/SMH/SOXQ/IWM/XLK)勿用 gfclaw 重建(gfclaw ETF 数据差)；
  ③gfclaw 全仓缺 2026-09-30(分区边界掉日)——重建后该日无棒属预期，GVRM 侧需删该日
  旧 em 残留行(见 GVRM 侧收尾记录)。
用法: python3 rebuild_us_equity_kline_gfclaw.py [--symbols ASML,CDNS] [--dry-run]
"""
import argparse
import glob
import os
import shutil
import sys
import time

import pandas as pd

GFCLAW_ROOT = "/data/gfclaw/local_market_data/eq_us_quote_daily"
OUT = "/data/fmdata/store/market/us_equity_kline_em.csv"
BACKUP_DIR = "/home/ubuntu/config-backups/gvrm-p02-backfill-20261121"
SOURCE_TAG = "gfclaw_return_chained"

# ts_code -> Wind 代码（同 fetch_us_equity_kline_fallback.py GF_MAP 实证映射；gfclaw 只读勿改）
GF_MAP = {
    "AAPL": "AAPL.O", "AMAT": "AMAT.O", "AMD": "AMD.O", "AMZN": "AMZN.O",
    "ANET": "ANET.N", "ASML": "ASML.O", "AVGO": "AVGO.O", "CDNS": "CDNS.O",
    "CEG": "CEG.O", "CRDO": "CRDO.O", "CRM": "CRM.N", "DDOG": "DDOG.O",
    "ETN": "ETN.N", "GOOGL": "GOOGL.O", "IWM": "IWM.P", "KLAC": "KLAC.O",
    "LRCX": "LRCX.O", "META": "META.O", "MRVL": "MRVL.O", "MSFT": "MSFT.O",
    "MU": "MU.O", "NOW": "NOW.N", "NVDA": "NVDA.O", "ORCL": "ORCL.N",
    "PLTR": "PLTR.O", "QCOM": "QCOM.O", "QQQ": "QQQ.O", "SMH": "SMH.O",
    "SNPS": "SNPS.O", "SOXQ": "SOXQ.O", "SPY": "SPY.P", "TSLA": "TSLA.O",
    "TSM": "TSM.N", "VRT": "VRT.N", "VST": "VST.N", "XLK": "XLK.P",
}


def load_gfclaw(codes):
    frames = []
    for f in sorted(glob.glob(f"{GFCLAW_ROOT}/period=*/*.parquet")):
        t = pd.read_parquet(f, columns=[
            "ticker_id", "mark_day", "day_last_px", "restated_ratio",
            "day_first_px", "day_peak_px", "day_floor_px", "deal_unit_qty"])
        frames.append(t[t.ticker_id.isin(codes)])
    g = pd.concat(frames, ignore_index=True)
    g["mark_day"] = g["mark_day"].astype(str)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔子集(默认: 23 只已审计重建标的)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    default_syms = ["ASML", "CDNS", "SNPS", "TSM", "TSLA",  # P0-2 五品种(重跑换 ratio 公式)
                    "ETN", "QCOM", "VST", "KLAC", "AMAT", "MSFT", "MRVL", "AAPL",
                    "AVGO", "ORCL", "LRCX", "MU", "AMD", "NVDA", "CRM", "AMZN",
                    "NOW", "ANET"]  # 终审审计 18 只 REBUILD 级
    wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or default_syms
    bad = [s for s in wanted if s not in GF_MAP]
    if bad:
        print(f"[REFUSE] unknown symbols: {bad}"); return 2

    gf = load_gfclaw({GF_MAP[s] for s in wanted})
    csv = pd.read_csv(OUT, dtype={"trade_date": str}, float_precision="round_trip")
    names = dict(csv.groupby("ts_code")["name"].last())

    out_frames, summary = [], []
    for sym in wanted:
        code = GF_MAP[sym]
        gs = (gf[gf.ticker_id == code].sort_values("mark_day")
              .drop_duplicates("mark_day").reset_index(drop=True))
        gs["ret"] = (gs.day_last_px / gs.day_last_px.shift(1)
                     * (gs.restated_ratio / gs.restated_ratio.shift(1)) - 1)
        sub = csv[csv.ts_code == sym].copy()
        sub_dates = sub.trade_date.str[:4] + "-" + sub.trade_date.str[4:6] + "-" + sub.trade_date.str[6:]
        have = dict(zip(sub_dates, sub.close))
        common = [d for d in gs.mark_day if d in have]
        if not common:
            print(f"[SKIP] {sym}: no common date with CSV"); out_frames.append(sub); continue
        anchor_date = max(common)
        P = float(have[anchor_date])
        ai = int(gs.index[gs.mark_day == anchor_date][0])
        # 链只用到 ret[1..ai]（首行自身 ret 不参与：链到首行即停）
        needed = gs.ret.iloc[1: ai + 1]
        if needed.isna().any():
            bad_days = gs.mark_day.iloc[1: ai + 1][needed.isna()].tolist()
            print(f"[SKIP] {sym}: interior NaN ret {bad_days[:3]} (chain unsafe)")
            out_frames.append(sub); continue
        closes = {anchor_date: P}
        for i in range(ai - 1, -1, -1):
            P = P / (1.0 + float(gs.ret.iloc[i + 1]))
            closes[gs.mark_day.iloc[i]] = P
        rows = []
        for i in range(0, ai):  # 锚点行原值不动；锚点后 westock 尾行保留
            d = gs.mark_day.iloc[i]
            c = closes[d]
            f = c / float(gs.day_last_px.iloc[i])
            rows.append({
                "ts_code": sym,
                "trade_date": d.replace("-", ""),
                "open": float(gs.day_first_px.iloc[i]) * f,
                "high": float(gs.day_peak_px.iloc[i]) * f,
                "low": float(gs.day_floor_px.iloc[i]) * f,
                "close": c,
                "volume": float(gs.deal_unit_qty.iloc[i]) if pd.notna(gs.deal_unit_qty.iloc[i]) else "",
                "amount": "",
                "name": names.get(sym, sym),
                "source": SOURCE_TAG,
            })
        tail = sub[sub.trade_date >= anchor_date.replace("-", "")]
        merged = pd.concat([pd.DataFrame(rows), tail], ignore_index=True).sort_values("trade_date")
        out_frames.append(merged)
        ret_chk = merged.close.pct_change().dropna()
        summary.append((sym, len(rows), merged.trade_date.min(), merged.trade_date.max(),
                        float(merged.close.min()), float(ret_chk.abs().max())))

    keep = csv[~csv.ts_code.isin(wanted)]
    newcsv = (pd.concat([keep, *out_frames], ignore_index=True)
              .sort_values(["ts_code", "trade_date"]).reset_index(drop=True))
    dup = int(newcsv.duplicated(["ts_code", "trade_date"]).sum())
    bad_px = int((pd.to_numeric(newcsv.close, errors="coerce") <= 0).sum())
    print(f"[check] rows {len(csv)} -> {len(newcsv)} | dup={dup} | nonpositive_close={bad_px}")
    for sym, n, d0, d1, cmin, rmax in summary:
        print(f"[ok] {sym}: rebuilt {n} bars, {d0}..{d1}, close_min={cmin:.2f}, max|ret|={rmax:.2%}")
    if dup or bad_px:
        print("[REFUSE] sanity fail, not writing"); return 2
    if args.dry_run:
        print("[dry-run] no write"); return 0
    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = f"{BACKUP_DIR}/us_equity_kline_em.csv.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(OUT, bak)
    tmp = OUT + ".tmp"
    newcsv.to_csv(tmp, index=False)
    os.replace(tmp, OUT)
    print(f"[done] wrote {OUT} (backup: {bak})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
