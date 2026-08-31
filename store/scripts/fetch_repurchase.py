#!/usr/bin/env python3
"""全市场回购数据 (tushare，2026-08-31 弃用东财裸连源)

替换原因：原 akshare stock_repurchase_em 裸连东方财富（IP 封禁风险），
tushare repurchase 接口 fmdata token 已有权限，且支持 ann_date 窗口全市场拉取。

tushare 单次返回有行数上限（实测 2000 截断），故按 180 天窗口分段拉全历史
（2016 至今），命中疑似截断(>=1900行)时递归对半细分窗口。

输出 schema 与东财版完全一致（缺源字段留空列），下游消费者零改动。
快照化：tushare 是逐公告事件流，折叠为一票一行最新状态（对齐东财版语义）。
单位坑：官方文档称 vol/amount 为万股/万元，但实测部分"预案"行 amount 单位
是元（中国核电 2e8=2亿元，妙想交叉验证），行间不一致。统一按万元 x1e4 映射，
个别行金额可能虚高 1e4 倍，消费方做金额分析前需按量级过滤。
"""
import os
import time
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta

OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/repurchase.csv"
STOCK_LIST = "/home/ubuntu/fmdata/store/reference/stock_list.csv"
HISTORY_START = datetime(2016, 1, 1)
WINDOW_DAYS = 180
SPLIT_FLOOR_DAYS = 20   # 窗口细到 20 天仍疑似截断就接受并告警
CAP_SUSPECT = 1900      # >=1900 行视为可能被 2000 行上限截断

COLUMNS = ["SEQ", "SECURITY_CODE", "SECURITY_NAME", "CLOSE",
           "PLAN_PRICE_RANGE", "PLAN_AMOUNT_MIN", "PLAN_AMOUNT_MAX",
           "RATIO_MIN", "RATIO_MAX", "START_DATE", "STATUS",
           "EXECUTED_SHARES", "EXECUTED_AMOUNT", "ANNOUNCE_DATE"]


def _retry(fn, *args, **kwargs):
    """tushare 限频退避：遇到 rate/credit 异常睡 5s 重试，最多 4 次。"""
    for attempt in range(4):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 3:
                time.sleep(5 + attempt * 5)
                continue
            raise


def _pull_window(pro, start, end, depth=0):
    """拉一个 ann_date 窗口；疑似截断则对半细分递归。返回 DataFrame 列表。"""
    df = _retry(pro.query, "repurchase",
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    n = 0 if df is None else len(df)
    if n >= CAP_SUSPECT and depth < 4:
        mid = start + (end - start) / 2
        print(f"[repurchase] window {start:%Y%m%d}~{end:%Y%m%d} hit {n} rows (cap suspect), splitting", flush=True)
        left = _pull_window(pro, start, mid, depth + 1)
        right = _pull_window(pro, mid + timedelta(days=1), end, depth + 1)
        return left + right
    if n >= CAP_SUSPECT:
        print(f"[repurchase] WARNING: window {start:%Y%m%d}~{end:%Y%m%d} still {n} rows at max depth, possible truncation", flush=True)
    return [df] if n else []


def main():
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()

    end = datetime.now()
    frames, cur = [], HISTORY_START
    while cur <= end:
        wend = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
        frames.extend(_pull_window(pro, cur, wend))
        cur = wend + timedelta(days=1)

    if not frames:
        print("[repurchase] WARNING: no data", flush=True)
        pd.DataFrame(columns=COLUMNS).to_csv(OUTPUT_CSV, index=False)
        return

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["ts_code", "ann_date", "proc", "vol", "amount"])
    # 快照化：与东财版语义对齐（一票一行最新状态）。tushare 原始是逐公告事件流
    # (~6万行)，按 ann_date 排序取每票最后一行 (~3700 行)。
    df = df.sort_values("ann_date").drop_duplicates(subset=["ts_code"], keep="last")
    df["ann_date"] = df["ann_date"].fillna("")

    # 名称关联
    try:
        names = pd.read_csv(STOCK_LIST, dtype=str)[["ts_code", "name"]]
        df = df.merge(names, on="ts_code", how="left")
    except Exception:
        df["name"] = ""

    def price_range(row):
        lo, hi = row.get("low_limit"), row.get("high_limit")
        if pd.notna(lo) and pd.notna(hi):
            return f"{lo}-{hi}"
        return ""

    out = pd.DataFrame({
        "SEQ": range(1, len(df) + 1),
        "SECURITY_CODE": df["ts_code"].str[:6],
        "SECURITY_NAME": df["name"].fillna(""),
        "CLOSE": "",
        "PLAN_PRICE_RANGE": df.apply(price_range, axis=1),
        "PLAN_AMOUNT_MIN": "",
        "PLAN_AMOUNT_MAX": "",
        "RATIO_MIN": "",
        "RATIO_MAX": "",
        "START_DATE": "",
        "STATUS": df["proc"].fillna(""),
        "EXECUTED_SHARES": pd.to_numeric(df["vol"], errors="coerce") * 1e4,
        "EXECUTED_AMOUNT": pd.to_numeric(df["amount"], errors="coerce") * 1e4,
        "ANNOUNCE_DATE": df["ann_date"],
    })
    out = out.sort_values("ANNOUNCE_DATE", ascending=False).reset_index(drop=True)
    out["SEQ"] = range(1, len(out) + 1)

    tmp = OUTPUT_CSV + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, OUTPUT_CSV)
    print(f"[repurchase] DONE: {len(out)} rows -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
