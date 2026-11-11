#!/usr/bin/env python3
"""GVRM 晨会看板日度紧凑包构建器 (2026-11-10).

把东吴宏观 fmdata 落地数据 + SW 买点票池聚合成单文件 JSON (cn_board_pack.json),
GVRM(HK43) sync 经 `ssh sz81 cat` 拉取后由 risk_monitor/cn_board.py 组装进快照
`cn_morning_board` 键。重活(大表聚合/md解析)留在数据同侧, HK43 只消费小包。

数据源 (store/data/):
  soochow/soochow_daily_review.csv   游资复盘 md     → recap 块 (cn_md_parsers.parse_review)
  soochow/soochow_multi_news.csv     新闻日览 md     → news 块 (cn_md_parsers.parse_news)
  soochow/soochow_signal_pool.csv    信号池概览 md   → pool 块 (正则摘要)
  soochow/soochow_industry_flow.csv  行业资金流 L1   → industry_flow 块 (1d 实值 + 5d/20d 本地滚动和)
  soochow/soochow_etf_flow.csv       ETF 逐日申赎    → etf_flow 块 (1d/5d 排行, 5d 本地滚动和)
  soochow/soochow_etf_flow_agg.csv   指数口径聚合    → etf_flow.index_agg (最新日 5d/20d)
  soochow/soochow_timing_section.csv 择时全截面      → timing 块 (行业均值+环比+分位)
  soochow/soochow_sentiment_matrix.csv 机构情绪矩阵  → sentiment 块 (B/S 计数摘要)
  sw/sw_tech_screen.csv(+_meta)      SW买点共振票池  → tech_screen 块

纪律: 每块独立 fail-open(失败进 errors 不拖垮整包); 5d/20d 一律本地滚动和(服务端
滚动窗列禁用, 防重写); 所有数值直接来自源列, 聚合只做 sum/mean/差分。
用法: python3 build_cn_board_pack.py [--out PATH]
cron : 19:35(19:15 抓取后) + 07:50(07:40 自愈后) daily。
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, "/data/fmdata/scripts")

CST = timezone(timedelta(hours=8))
ROOT = Path("/data/fmdata/store/data")
OUT_DEFAULT = ROOT / "soochow/cn_board_pack.json"
PACK_VERSION = "cn-board-pack-v1"
N_TOP = 8


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def build_pack():
    blocks, errors = {}, {}

    def guard(name):
        def deco(fn):
            try:
                blocks[name] = fn()
                if not blocks[name]:
                    errors[name] = "空结果"
                    blocks.pop(name)
            except Exception as e:
                errors[name] = f"{type(e).__name__}: {e}"[:200]
        return deco

    @guard("recap_local")
    def _recap_local():
        """盘面硬数字(T-0, tushare 本地): 涨跌停/炸板/连板梯队/指数/成交额/亏钱效应.
        东吴复盘滞后 1-2 交易日, 本块保证晨会'昨日盘面'永远是最近交易日。"""
        lim = pd.read_csv(ROOT / "market/ashare_limit_list.csv", dtype=str)
        lim = lim[lim["ts_code"].notna() & (lim["ts_code"] != "")]
        d = str(sorted(lim["trade_date"].unique())[-1])
        day = lim[lim["trade_date"] == d]
        u = day[day["limit_type"] == "U"].copy()
        u["lt"] = u["limit_times"].astype(float)
        dd = day[day["limit_type"] == "D"]
        z = day[day["limit_type"] == "Z"]
        idx = pd.read_csv(ROOT / "market/ashare_index_brief.csv", dtype={"trade_date": str, "ts_code": str})
        idx = idx[idx["trade_date"] == d]
        moves = {r["name"]: round(float(r["pct_chg"]), 2) for _, r in idx.iterrows()}
        amt = idx[idx["ts_code"].isin(["000001.SH", "399106.SZ"])]["amount_qian"].astype(float).sum() / 1e5
        # 亏钱效应(近似): 前一交易日涨停股在今日的平均涨幅(复用东吴口径语义)
        dates_all = sorted(lim["trade_date"].unique())
        money_effect = None
        if len(dates_all) >= 2:
            prev = dates_all[-2]
            prev_u = lim[(lim["trade_date"] == prev) & (lim["limit_type"] == "U")]["ts_code"]
            ts = pd.read_csv(ROOT / "soochow/soochow_timing_section.csv")
            ts = ts[(ts["board"] == "全A") & (ts["date"] == f"{d[:4]}-{d[4:6]}-{d[6:8]}")]
            m = ts[ts["code"].isin(set(prev_u))]["chg"]
            if len(m) >= 5:
                money_effect = round(float(m.mean()) * 100, 2)
        ladder = [{"stock": r["name"], "boards": int(r["lt"]),
                   "theme": r.get("industry") or "", "note": ""}
                  for _, r in u.nlargest(8, "lt").iterrows()]
        # 近5个交易日涨跌停结构趋势(宽度变化, 历史随落地天数增长)
        trend = []
        for td in dates_all[-5:]:
            tday = lim[lim["trade_date"] == td]
            trend.append({"date": f"{td[:4]}-{td[4:6]}-{td[6:8]}",
                          "limit_up": int(len(tday[tday["limit_type"] == "U"])),
                          "limit_down": int(len(tday[tday["limit_type"] == "D"])),
                          "limit_broken": int(len(tday[tday["limit_type"] == "Z"]))})
        return {"as_of": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "trend": trend,
                "limit_up": int(len(u)), "limit_down": int(len(dd)),
                "limit_broken": int(len(z)),
                "first_board": int((u["lt"] == 1).sum()),
                "max_boards": int(u["lt"].max()) if len(u) else None,
                "total_amount_yi": round(float(amt), 0) if amt else None,
                "index_moves": moves, "money_effect": money_effect,
                "ladder": ladder,
                "source": "tushare limit_list_d/index_daily(本地计算, T-0收盘后)"}

    @guard("recap")
    def _recap():
        from cn_md_parsers import parse_review
        df = pd.read_csv(ROOT / "soochow/soochow_daily_review.csv")
        row = df.sort_values("date").iloc[-1]
        out = parse_review(row["raw"])
        if out:
            out["fetched_at"] = row["fetched_at"]
            out["full_md"] = row["raw"]  # 前端折叠区逐字全文(输出要求: 原样呈现)
        return out

    @guard("news")
    def _news():
        from cn_md_parsers import parse_news
        df = pd.read_csv(ROOT / "soochow/soochow_multi_news.csv")
        row = df.sort_values("date").iloc[-1]
        out = parse_news(row["raw"])
        if out:
            out["fetched_at"] = row["fetched_at"]
        return out

    @guard("pool")
    def _pool():
        df = pd.read_csv(ROOT / "soochow/soochow_signal_pool.csv")
        text = df.sort_values("date").iloc[-1]["raw"]
        date = df.sort_values("date").iloc[-1]["date"]
        sec = {}
        for title, body in re.findall(r"## (\S+)\n(.*?)(?=\n## |\Z)", text, re.S):
            stats = {}
            m = re.search(r"共 \*\*(\d+)\*\* 条 · \*\*(\d+)\*\* 个标的", body)
            if m:
                stats["total"], stats["names"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"状态：持有 (\d+) ｜ 到期 (\d+)", body)
            if m:
                stats["holding"], stats["expired"] = int(m.group(1)), int(m.group(2))
            m = re.search(r"最近触发日：\*\*([\d-]+)\*\*（(\d+) 条）", body)
            if m:
                stats["last_trigger_date"], stats["last_trigger_n"] = m.group(1), int(m.group(2))
            m = re.search(r"数据日期 \*\*([\d-]+)\*\*", body)
            stats["data_date"] = m.group(1) if m else None
            sec[title] = stats
        return {"date": date, "sections": sec,
                "tail": text[-600:]}  # 尾部风险提示原文节选(披露用)

    @guard("industry_flow")
    def _ind():
        df = pd.read_csv(ROOT / "soochow/soochow_industry_flow.csv")
        df = df[df["level"].astype(str).str.upper() == "L1"].copy()
        dates = sorted(df["data_date"].unique())
        as_of = dates[-1]
        col1d = "近1个交易日净流入额(亿元)"
        cur = df[df["data_date"] == as_of][["申万一级行业", col1d]]
        base = cur.set_index("申万一级行业")[col1d].astype(float)
        roll = {}
        for win in (5, 20):
            win_dates = dates[-win:]
            s = (df[df["data_date"].isin(win_dates)]
                 .groupby("申万一级行业")[col1d].sum())
            # 不足 win 个交易日照常给和, 但披露窗口实长
            roll[win] = {"sum": s.round(2), "ndays": len(win_dates)}
        def topn(s, n=N_TOP):
            s = s.dropna().sort_values(ascending=False)
            return [{"industry": k, "amount_yi": round(float(v), 2)}
                    for k, v in s.head(n).items()]
        return {
            "as_of": as_of,
            "top_in_1d": topn(base), "top_out_1d": topn(-base),
            "top_in_20d": topn(roll[20]["sum"]), "top_out_20d": topn(-roll[20]["sum"]),
            "win20_ndays": roll[20]["ndays"], "win5_ndays": roll[5]["ndays"],
            "top_in_5d": topn(roll[5]["sum"]), "top_out_5d": topn(-roll[5]["sum"]),
        }

    @guard("etf_flow")
    def _etf():
        df = pd.read_csv(ROOT / "soochow/soochow_etf_flow.csv")
        dates = sorted(df["data_date"].unique())
        as_of = dates[-1]
        col1d = "近1个交易日净流入额(亿元)"
        name_col = "证券名称"
        type_col = "投资类型(一级分类)"
        # 权益看板口径: 榜单只排 股票型/跨境 ETF(货币/债券/商品ETF申赎是流动性噪音)
        eq_types = {"股票型ETF", "跨境ETF"}
        if type_col in df.columns:
            df = df[df[type_col].isin(eq_types)].copy()
        cur = df[df["data_date"] == as_of]
        base = cur.set_index(name_col)[col1d].astype(float)
        s5 = (df[df["data_date"].isin(dates[-5:])]
              .groupby(name_col)[col1d].sum())
        def topn(s, n=N_TOP):
            s = s.dropna().sort_values(ascending=False)
            return [{"etf": k, "amount_yi": round(float(v), 2)}
                    for k, v in s.head(n).items()]
        # 指数口径聚合表: 最新日 5d/20d 净流入
        agg = pd.read_csv(ROOT / "soochow/soochow_etf_flow_agg.csv")
        agg_dates = sorted(agg["data_date"].unique())
        a = agg[agg["data_date"] == agg_dates[-1]].copy()
        a20 = a[["跟踪指数名称", "近20个交易日净流入额(亿元)"]].dropna()
        a20 = a20.sort_values("近20个交易日净流入额(亿元)", ascending=False)
        return {
            "as_of": as_of, "agg_as_of": agg_dates[-1],
            "top_in_1d": topn(base), "top_out_1d": topn(-base),
            "top_in_5d": topn(s5),
            "index_agg_20d_top": [{"index": r["跟踪指数名称"],
                                   "amount_yi": round(float(r["近20个交易日净流入额(亿元)"]), 1)}
                                  for _, r in a20.head(6).iterrows()],
            "index_agg_20d_bottom": [{"index": r["跟踪指数名称"],
                                      "amount_yi": round(float(r["近20个交易日净流入额(亿元)"]), 1)}
                                     for _, r in a20.tail(3).iterrows()],
        }

    @guard("timing")
    def _timing():
        df = pd.read_csv(ROOT / "soochow/soochow_timing_section.csv")
        df = df[df["board"] == "全A"].copy()
        dates = sorted(df["date"].unique())
        as_of, prev = dates[-1], dates[-2]
        def stats(d):
            g = df[df["date"] == d].groupby("ind1").agg(
                n=("code", "count"), risk=("risk", "mean"),
                momentum=("momentum", "mean"), score=("score", "mean"))
            return g.round(2)
        cur, old = stats(as_of), stats(prev)
        merged = cur.join(old, rsuffix="_prev", how="left")
        merged["risk_delta"] = (merged["risk"] - merged["risk_prev"]).round(2)
        rows = [{"industry": i, "n": int(r.n), "risk": r.risk, "momentum": r.momentum,
                 "score": r.score, "risk_prev": r.risk_prev, "risk_delta": r.risk_delta}
                for i, r in merged.iterrows()]
        cs = df[df["date"] == as_of]
        def pct(col):
            q = cs[col].quantile([.1, .25, .5, .75, .9]).round(1)
            return {f"P{int(k*100)}": float(v) for k, v in q.items()}
        return {
            "as_of": as_of, "prev": prev, "n_stocks": int(len(cs)),
            "cross_section": {"risk": pct("risk"), "momentum": pct("momentum"),
                              "score": pct("score")},
            "industries": sorted(rows, key=lambda r: -r["momentum"]),
        }

    @guard("sentiment")
    def _senti():
        df = pd.read_csv(ROOT / "soochow/soochow_sentiment_matrix.csv")
        rows = df.sort_values("date")
        latest = json.loads(rows.iloc[-1]["raw"])
        md = latest.get("report_markdown", "")
        m = re.search(r"统计日期：\s*([\d-]+)", md)
        stat_date = m.group(1) if m else rows.iloc[-1]["date"]
        # 两个矩阵表(净申购B/净赎回S): 数每行 B/S 格数, 按基金类型聚合
        counts = {}
        for label, pat in (("B", r"\*\*净申购（B）\*\*(.*?)(?=\*\*净赎回|\Z)"),
                           ("S", r"\*\*净赎回（S）\*\*(.*)")):
            seg = re.search(pat, md, re.S)
            if not seg:
                continue
            for line in seg.group(1).splitlines():
                if not line.strip().startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) < 3 or cells[0] in ("基金类型", ":---------"):
                    continue
                # 行首必须含中文/字母(纯数字行=矩阵续行/序号行, 非基金类型)
                if not re.search(r"[一-龥A-Za-z]", cells[0]):
                    continue
                counts.setdefault(cells[0], {"B": 0, "S": 0})
                counts[cells[0]][label] += sum(1 for c in cells[1:] if label in c)
        digest = [{"fund_type": k, "buy_marks": v["B"], "sell_marks": v["S"],
                   "net_marks": v["B"] - v["S"]}
                  for k, v in counts.items()]
        digest.sort(key=lambda r: -abs(r["net_marks"]))
        return {"date": rows.iloc[-1]["date"], "stat_date": stat_date,
                "digest": digest[:12]}

    @guard("pattern_map")
    def _pattern_map():
        df = pd.read_csv(ROOT / "sw/sw_pattern_map.csv")
        r = df.iloc[-1]
        return {
            "data_date": r["data_date"], "generated_at": str(r.get("generated_at", "")),
            "stocks_scanned": int(r.get("stocks_scanned") or 0),
            "bullish": int(r.get("bullish") or 0), "bearish": int(r.get("bearish") or 0),
            "bullish_confirmed": int(r.get("bullish_confirmed") or 0),
            "bearish_confirmed": int(r.get("bearish_confirmed") or 0),
            "by_pattern": json.loads(r.get("by_pattern_json") or "[]"),
            "by_industry": json.loads(r.get("by_industry_json") or "[]"),
        }

    @guard("sw_macro")
    def _sw_macro():
        df = pd.read_csv(ROOT / "sw/sw_macro_snapshot.csv")
        as_of = sorted(df["data_date"].unique())[-1]
        rows = df[df["data_date"] == as_of]
        return {"data_date": as_of,
                "series": rows.drop(columns=["data_date", "fetched_at"]).to_dict(orient="records")}

    @guard("tech_screen")
    def _tech():
        df = pd.read_csv(ROOT / "sw/sw_tech_screen.csv", dtype={"ts_code": str})
        meta = pd.read_csv(ROOT / "sw/sw_tech_screen_meta.csv")
        dates = sorted(df["data_date"].unique())
        as_of = dates[-1]
        rows = df[df["data_date"] == as_of]
        m = meta[meta["data_date"] == as_of].iloc[-1]
        return {
            "data_date": as_of, "generated_at": m.get("generated_at", ""),
            "stocks_scanned": int(m.get("stocks_scanned") or 0),
            "total_matched": int(m.get("total_matched") or 0),
            "stored_rows": int(len(rows)),
            "funnel": json.loads(m.get("funnel") or "{}"),
            "rows": rows.to_dict(orient="records")[:20],
        }

    return {"pack_version": PACK_VERSION, "built_at": now_cst(),
            "blocks": blocks, "errors": errors}


def _clean(o):
    """NaN/inf → None 递归清洗(allow_nan=False 可序列化; 与 GVRM _json_num 同款纪律)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    pack = _clean(build_pack())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(pack, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    tmp.replace(out)
    size = out.stat().st_size
    print(f"[{now_cst()}] pack {out} {size/1024:.0f}KB blocks={sorted(pack['blocks'])} "
          f"errors={pack['errors'] or '无'}", flush=True)


if __name__ == "__main__":
    main()
