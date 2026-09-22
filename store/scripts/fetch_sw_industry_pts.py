#!/usr/bin/env python3
"""拉取申万行业 point-in-time 映射（L1/L2/L3 全层级，带 in_date/out_date 窗口）。

数据源：tushare index_classify（行业树）+ index_member（成分股进出事件流）。
- index_classify(src='SW2021'): 511 条，含 index_code/industry_name/level/parent_code。
- index_member(index_code=X): 该指数成分股的 (con_code, in_date, out_date) 事件流。

输出策略：L3 驱动为主（最细，覆盖 ~99% 票），L2/L1 逐级 fallback 补漏；
每只票只保留其可用的最细级别，避免同票多级别行混淆 loader 的去重。
最终 CSV：stock_code, in_date, out_date, l1_code, l1_name, l2_code, l2_name, l3_code, l3_name
（无该级别时对应字段空）。loader 按 effective_date 过滤 in_date<=eff<out_date 复原任一时点归属。

刷新频率：申万行业调整每年只有几次（半年评审），月刷新足够；recipe update_freq=monthly。
"""
import os
import sys
import time
import datetime as dt
import pandas as pd
import tushare as ts

OUT_CSV = os.path.expanduser("~/fmdata/store/fundamentals/sw_industry_pts.csv")


def _retry(pro_func, *args, **kwargs):
    """tushare 限频退避：遇到 rate/credit 异常睡 5s 重试，最多 4 次。"""
    for attempt in range(4):
        try:
            return pro_func(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 3:
                time.sleep(5 + attempt * 5)
                continue
            raise


def main():
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()

    print("[1/3] fetch index_classify (SW2021 行业树)...", file=sys.stderr)
    classify = _retry(pro.index_classify, src="SW2021")
    print(f"  rows={len(classify)} level分布={classify['level'].value_counts().to_dict()}", file=sys.stderr)

    name_map = dict(zip(classify["index_code"], classify["industry_name"]))
    # 注意：parent_code 用的是父级的 industry_code（非 index_code），必须按 industry_code 反查父 row
    classify["parent_code"] = classify["parent_code"].astype(str)
    classify["industry_code"] = classify["industry_code"].astype(str)
    by_idx = {r.index_code: r for r in classify.itertuples()}             # index_code -> row
    by_icode = {r.industry_code: r for r in classify.itertuples()}        # industry_code -> row

    def walk(idx_code: str, level: str):
        """返回 (l1_code,l1_name,l2_code,l2_name,l3_code,l3_name)。按 industry_code 链上溯。"""
        row = by_idx.get(idx_code)
        l1c = l1n = l2c = l2n = l3c = l3n = ""
        if row is None:
            return l1c, l1n, l2c, l2n, l3c, l3n
        if level == "L3":
            l3c, l3n = idx_code, row.industry_name
            l2 = by_icode.get(str(row.parent_code))
            if l2 is not None:
                l2c, l2n = l2.index_code, l2.industry_name
                l1 = by_icode.get(str(l2.parent_code))
                if l1 is not None:
                    l1c, l1n = l1.index_code, l1.industry_name
        elif level == "L2":
            l2c, l2n = idx_code, row.industry_name
            l1 = by_icode.get(str(row.parent_code))
            if l1 is not None:
                l1c, l1n = l1.index_code, l1.industry_name
        else:  # L1
            l1c, l1n = idx_code, row.industry_name
        return l1c, l1n, l2c, l2n, l3c, l3n

    # 按 level 分组，从最细开始拉
    levels = ["L3", "L2", "L1"]
    seen_stocks = set()  # 已被更细级别覆盖的票，fallback 时跳过
    out_rows = []
    stats = {}

    for level in levels:
        idx_codes = classify.loc[classify["level"] == level, "index_code"].tolist()
        stats[level] = {"indices": len(idx_codes), "rows": 0, "stocks_new": 0}
        print(f"[2/3] fetch index_member for {level}: {len(idx_codes)} indices...", file=sys.stderr)
        for i, idx_code in enumerate(idx_codes):
            try:
                m = _retry(pro.index_member, index_code=idx_code)
            except Exception as e:
                print(f"  WARN {idx_code}: {e}", file=sys.stderr)
                continue
            if m is None or len(m) == 0:
                continue
            l1_code, l1_name, l2_code, l2_name, l3_code, l3_name = walk(idx_code, level)

            for _, r in m.iterrows():
                stock = str(r["con_code"]).zfill(6)[:6]
                # fallback 级别只收新票
                if stock in seen_stocks:
                    continue
                out_rows.append({
                    "stock_code": stock,
                    "in_date": str(r.get("in_date", "")),
                    "out_date": "" if pd.isna(r.get("out_date")) else str(r.get("out_date")),
                    "l1_code": l1_code, "l1_name": l1_name,
                    "l2_code": l2_code, "l2_name": l2_name,
                    "l3_code": l3_code, "l3_name": l3_name,
                })
                seen_stocks.add(stock)
                stats[level]["rows"] += 1
            stats[level]["stocks_new"] = len(seen_stocks) - sum(stats[k]["stocks_new"] for k in levels if k != level and levels.index(k) < levels.index(level))
            if (i + 1) % 25 == 0:
                print(f"  {level} progress: {i+1}/{len(idx_codes)} (cumu stocks={len(seen_stocks)})", file=sys.stderr)
            time.sleep(0.18)  # tushare 限频保护

    if not out_rows:
        print("ERROR: no data fetched", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(out_rows)
    # 去重（同票同窗口同级别，理论上不存在但防御）
    df = df.drop_duplicates(["stock_code", "in_date", "out_date"]).reset_index(drop=True)
    df = df.sort_values(["stock_code", "in_date"]).reset_index(drop=True)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"[3/3] saved {len(df)} rows ({df['stock_code'].nunique()} stocks) to {OUT_CSV}", file=sys.stderr)
    print(f"  L3覆盖={stats['L3']['rows']} L2补漏={stats['L2']['rows']} L1补漏={stats['L1']['rows']}", file=sys.stderr)
    print(f"OK rows={len(df)} stocks={df['stock_code'].nunique()}")


if __name__ == "__main__":
    main()
