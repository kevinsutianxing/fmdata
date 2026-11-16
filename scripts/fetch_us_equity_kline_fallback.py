#!/usr/bin/env python3
"""美股K线 gfclaw 备选源填充（2026-11-16，用户令：EM 主源失败→gfclaw 备选，慢慢替换路线）。

触发链: fetch_us_equity_kline_cron.sh 里主脚本重跑仍 rc=2 → 从其输出抓
FAILED_SYMBOLS 交给本脚本。主脚本 fetch_us_equity_kline.py **零改动**
（🔴v4 尾窗自校验禁改护栏）。

口径桥接（gfclaw 原始价 → EM hfq×factor 前复权序列）:
  gfclaw prior_close_px 是除息修正版 → r_t = close_t/prior_close_t ≈ EM fqt=1 日收益
  回补价 = EM 库存最后一根 close × Π(1+r_t)（重定基拼接，序列连续无假跳空）
  open/high/low 按当日 close/day_last_px 同因子缩放（保留日内形状）
  volume 原样；amount gfclaw 美股无此列 → 置 NaN；source=gfclaw_fallback 可溯源
  🔴局限: gfclaw T+1（23:10 同步）→ 最新一根美股棒可能仍缺，待次日 EM/gfclaw 补
  🔴只做 gap 填充不做 bootstrap（池内 36 标的 EM 均有史；空标的场景不现实，拒绝并说明）

用法: python3 fetch_us_equity_kline_fallback.py --symbols AAPL,NVDA [--dry-run]
"""
import argparse
import sys

sys.path.insert(0, "/home/ubuntu/fmdata")
import pandas as pd  # noqa: E402

from fmdata.config import STORE_DIR  # noqa: E402

OUT = STORE_DIR / "market" / "us_equity_kline_em.csv"

# 2026-11-16 实证映射（gfclaw eq_us_quote_daily 逐标的探测 36/36 命中；
# ETF 后缀无规律：SPY/XLK/IWM=.P 而 QQQ/SMH/SOXQ=.O，故硬编码勿猜）
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

MAX_GAP_BARS = 60  # 超窗=异常断档,拒补(勿用长链复合收益放大漂移),留给EM全量重锚


def _em_names():
    """name 列与主脚本同源（import 其常量, 不执行 main）。"""
    sys.path.insert(0, str(STORE_DIR.parent / "scripts"))
    from fetch_us_equity_kline import SECIDS
    return {sym: name for sym, (_, name) in SECIDS.items()}


def _gfclaw_bars(sym, start_ymd, end_ymd):
    from fmdata.gfclaw_store import query as gfq
    r = gfq("eq_us_quote_daily", ticker_id=GF_MAP[sym], start=start_ymd, end=end_ymd,
            columns="ticker_id,mark_day,prior_close_px,day_first_px,day_peak_px,"
                    "day_floor_px,day_last_px,deal_unit_qty", limit=200000)
    df = pd.DataFrame(r.get("data", []))
    if df.empty:
        return df
    df["mark_day"] = df["mark_day"].astype(str)  # date32 → 'YYYY-MM-DD'
    df = df.sort_values("mark_day").reset_index(drop=True)
    for c in ("prior_close_px", "day_first_px", "day_peak_px", "day_floor_px",
              "day_last_px", "deal_unit_qty"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fill_symbol(sym, existing, em_names, dry_run=False):
    """返回 (状态串, 新行df)。逐棒复合收益重定基；异常棒即停防漂移放大。

    收益基础: 优先 gfclaw prior_close_px(除息修正版); 该字段在部分日为 NaN
    (实测美股 ETF 周一棒, 2026-11-16) → 退化为 gfclaw 前一根原始收盘做桥
    (除息日会有股息量级误差, fallback 场景可接受; 30% 单日护栏兜底口径事故)。
    """
    sym_df = existing[existing.ts_code == sym].sort_values("trade_date")
    if sym_df.empty:
        return f"[skip] {sym}: EM 无历史(bootstrap 场景拒补, 跑主脚本全量)", None
    last_date = str(sym_df["trade_date"].max())
    last_ymd = f"{last_date[:4]}-{last_date[4:6]}-{last_date[6:]}"
    gf = _gfclaw_bars(sym, last_ymd, "2099-01-01")  # 含 <=last_date 的桥接锚棒
    if gf.empty:
        return f"[noop] {sym}: gfclaw 无 {last_ymd} 起的数据", None
    gf["ymd_s"] = gf["mark_day"].str.replace("-", "", regex=False)
    gap = gf[gf["ymd_s"] > last_date]
    if gap.empty:
        return f"[noop] {sym}: 已最新(gfclaw 也无 {last_date} 之后的新棒)", None
    if len(gap) > MAX_GAP_BARS:
        return f"[refuse] {sym}: 断档 {len(gap)} 根超窗(>{MAX_GAP_BARS}), 留给 EM 全量重锚", None

    anchor = float(sym_df["close"].iloc[-1])
    prev_gf_close = None  # gfclaw 前一根原始收盘(prior 缺失时的收益桥)
    rows = []
    for r in gf.itertuples():
        if r.ymd_s <= last_date:
            if r.day_last_px is not None and not pd.isna(r.day_last_px) and r.day_last_px > 0:
                prev_gf_close = r.day_last_px
            continue
        px = r.day_last_px
        if px is None or pd.isna(px) or px <= 0:
            return f"[stop] {sym}: {r.mark_day} close 无效, 中止防漂移, 已得 {len(rows)} 根", (pd.DataFrame(rows) if rows else None)
        if r.prior_close_px is not None and not pd.isna(r.prior_close_px) and r.prior_close_px > 0:
            ret = px / r.prior_close_px
        elif prev_gf_close:
            ret = px / prev_gf_close
        else:
            return f"[stop] {sym}: {r.mark_day} 无收益基础(prior与桥均缺), 已得 {len(rows)} 根", (pd.DataFrame(rows) if rows else None)
        if abs(ret - 1) > 0.30:  # 单日|收益|>30% 可疑(gfclaw 口径事故), 中止
            return f"[stop] {sym}: {r.mark_day} 收益 {ret:.3f} 可疑, 中止, 已得 {len(rows)} 根", (pd.DataFrame(rows) if rows else None)
        close = anchor * ret
        scale = close / px
        o, h, l = r.day_first_px, r.day_peak_px, r.day_floor_px
        o = o * scale if (o is not None and not pd.isna(o) and o > 0) else close
        h = h * scale if (h is not None and not pd.isna(h) and h > 0) else close
        l = l * scale if (l is not None and not pd.isna(l) and l > 0) else close
        rows.append({
            "ts_code": sym, "trade_date": r.ymd_s,
            "open": o, "high": max(h, close), "low": min(l, close), "close": close,
            "volume": (0 if (r.deal_unit_qty is None or pd.isna(r.deal_unit_qty)) else r.deal_unit_qty),
            "amount": float("nan"), "name": em_names.get(sym, sym),
            "source": "gfclaw_fallback",
        })
        anchor = close
        prev_gf_close = px
    out = pd.DataFrame(rows)
    return (f"[dry] {sym}: 应补 {len(out)} 根 {out['trade_date'].min()}..{out['trade_date'].max()}"
            if dry_run else
            f"[fill] {sym}: 补 {len(out)} 根 {out['trade_date'].min()}..{out['trade_date'].max()}"), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="逗号分隔失败标的(来自主脚本 FAILED_SYMBOLS)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    unknown = [s for s in syms if s not in GF_MAP]
    if unknown:
        print(f"[skip] 无 gfclaw 映射: {unknown}")

    existing = pd.read_csv(OUT, dtype={"trade_date": str})
    em_names = _em_names()
    appended, n_syms = 0, 0
    for sym in [s for s in syms if s in GF_MAP]:
        status, new = fill_symbol(sym, existing, em_names, dry_run=args.dry_run)
        print(status, flush=True)
        if new is not None and not new.empty and not args.dry_run:
            existing = pd.concat([existing, new], ignore_index=True)
            existing = existing.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
            tmp = OUT.with_suffix(".csv.tmp")
            existing.to_csv(tmp, index=False)  # 逐标的落盘
            tmp.rename(OUT)
            appended += len(new)
            n_syms += 1
    print(f"[done] fallback appended={appended} symbols={n_syms} dry_run={args.dry_run}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
