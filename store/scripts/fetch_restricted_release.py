#!/usr/bin/env python3
"""限售解禁数据 (tushare，2026-08-31 弃用东财裸连源)

替换原因：原 akshare stock_restricted_release_detail_em 裸连东方财富
（IP 封禁风险），tushare share_float 接口 fmdata token 已有权限。

粒度处理：share_float 原始是逐持有人粒度，且 IPO 战略配售解禁会出现
单票数千行年金/资管子账户（实测 603468.SH 单日 5993 行）。本脚本聚合到
股票级 (code, date, share_type)：RELEASE_SHARES=求和, FLOAT_RATIO=取最大,
HOLDER_COUNT=行数（追加列）。输出行数量级与东财版 (~824) 可比。

拉取方式：逐日 float_date=<day> 精确查询。单日返回 ≥5990 视为撞 6000 行
上限：此时对该日已见代码逐只 ts_code 补拉（绕开日级截断），仍可能漏掉
截断掉未见代码的小额解禁，仅告警不阻断。

单位：float_share 实测为股（tushare 文档称万股是错的——交叉验证：920575.BJ
60436000 股=21.5%流通比→股本 2.8 亿 ✓；本层不做换算）。float_ratio 百分比
数值 -> 小数 (/100)，与东财版口径一致。ACTUAL_* / PREV_CLOSE 东财专有，留空列保 schema。
"""
import os
import time
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta

OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/restricted_release.csv"
STOCK_LIST = "/home/ubuntu/fmdata/store/reference/stock_list.csv"
DAYS_FORWARD = 180
DAY_SLEEP = 0.35
CAP_SUSPECT = 5990

COLUMNS = ["SECURITY_CODE", "SECURITY_NAME", "RELEASE_DATE", "SHARE_TYPE",
           "RELEASE_SHARES", "ACTUAL_RELEASE_SHARES", "ACTUAL_RELEASE_VALUE",
           "FLOAT_RATIO", "PREV_CLOSE", "HOLDER_COUNT"]


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


def main():
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()

    frames, day = [], datetime.now()
    last = day + timedelta(days=DAYS_FORWARD)
    while day <= last:
        ds = day.strftime("%Y%m%d")
        df = _retry(pro.query, "share_float", float_date=ds)
        if df is not None and len(df):
            if len(df) >= CAP_SUSPECT:
                print(f"[restricted_release] {ds} hit {len(df)} rows (cap) - per-code recovery", flush=True)
                rec = []
                for tc in df.ts_code.unique():
                    per = _retry(pro.query, "share_float", ts_code=tc)
                    if per is not None and len(per):
                        rec.append(per[per.float_date == ds])
                    time.sleep(DAY_SLEEP)
                frames.extend(rec)
            else:
                frames.append(df)
        time.sleep(DAY_SLEEP)
        day += timedelta(days=1)

    if not frames:
        print("[restricted_release] WARNING: no data", flush=True)
        pd.DataFrame(columns=COLUMNS).to_csv(OUTPUT_CSV, index=False)
        return

    df = pd.concat(frames, ignore_index=True)
    # 真自然键去重（补拉与日拉重叠）：同股同日同类型同持有人，保留最新公告
    df = df.dropna(subset=["float_date"])
    df = df.sort_values("ann_date").drop_duplicates(
        subset=["ts_code", "float_date", "share_type", "holder_name"], keep="last")
    # 物理不可能行清洗：解禁占流通比 >100% 的行是源脏数据（实测 002116 出现
    # ratio=14846% 连带股数虚高），剔除；ratio 缺失的行保留。
    ratio = pd.to_numeric(df["float_ratio"], errors="coerce")
    bad = ratio.notna() & (ratio > 100)
    if bad.any():
        print(f"[restricted_release] dropped {bad.sum()} rows with float_ratio>100%", flush=True)
        df = df[~bad]

    agg = df.groupby(["ts_code", "float_date", "share_type"], as_index=False).agg(
        RELEASE_SHARES=("float_share", lambda s: pd.to_numeric(s, errors="coerce").sum()),
        FLOAT_RATIO=("float_ratio", lambda s: pd.to_numeric(s, errors="coerce").max() / 100),
        HOLDER_COUNT=("holder_name", "size"),
    )

    try:
        names = pd.read_csv(STOCK_LIST, dtype=str)[["ts_code", "name"]]
        agg = agg.merge(names, on="ts_code", how="left")
    except Exception:
        agg["name"] = ""

    out = pd.DataFrame({
        "SECURITY_CODE": agg["ts_code"].str[:6],
        "SECURITY_NAME": agg["name"].fillna(""),
        "RELEASE_DATE": agg["float_date"],
        "SHARE_TYPE": agg["share_type"].fillna(""),
        "RELEASE_SHARES": agg["RELEASE_SHARES"],
        "ACTUAL_RELEASE_SHARES": "",
        "ACTUAL_RELEASE_VALUE": "",
        "FLOAT_RATIO": agg["FLOAT_RATIO"],
        "PREV_CLOSE": "",
        "HOLDER_COUNT": agg["HOLDER_COUNT"],
    })
    out = out.sort_values(["RELEASE_DATE", "SECURITY_CODE"]).reset_index(drop=True)

    tmp = OUTPUT_CSV + ".tmp"
    out.to_csv(tmp, index=False)
    os.replace(tmp, OUTPUT_CSV)
    print(f"[restricted_release] DONE: {len(out)} stock-day events -> {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
