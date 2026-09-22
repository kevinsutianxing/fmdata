#!/usr/bin/env python3
"""US stock spot (含总市值/流通市值) — 在 HK43 上运行，直连 eastmoney push2his，不走代理。

为什么跑在 HK43：
  akshare stock_us_spot_em 打 push2（实时子域），东财对其实时子域封代理 IP 极严——
  SZ81 需 QG 代理且批量抓取慢/不稳（死窗口 0% 成功率时段）；HK43 直连 push2his
  （历史子域）不限速、稳定。照 sp500 recipe 的 host:hk43 模式。

数据流：recipe_fetcher SSH 到 hk43 跑本脚本 → stdout 输出纯 CSV → parser:raw 回传 SZ41 存盘。
  （日志走 stderr，stdout 必须是纯 CSV，否则 pd.read_csv 解析失败。）

字段：f12代码 f14名称 f2最新价 f3涨跌幅 f20总市值 f21流通市值 f9市盈率 f23市净率 等，
对齐 akshare stock_us_spot_em 输出列名。按总市值降序拉取，<$1M 早停（切壳/深度微米噪音）。
"""
import sys
import time
import requests
import pandas as pd

BASE = "https://push2his.eastmoney.com/api/qt/clist/get"
FS = "m:105,m:106,m:107"  # 105=NASDAQ 106=NYSE 107=AMEX
FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23"
PAGE_SIZE = 100  # 东财 clist 单页硬上限 100
MAX_PAGES = 140
MCAP_FLOOR = 1e6  # 总市值 <$1M 视为壳/深度微米，早停

COL_MAP = {
    "f2": "最新价", "f3": "涨跌幅", "f4": "涨跌额", "f5": "成交量", "f6": "成交额",
    "f7": "振幅", "f8": "换手率", "f9": "市盈率", "f12": "代码", "f13": "市场代码",
    "f14": "名称", "f15": "最高", "f16": "最低", "f17": "今开", "f18": "昨收",
    "f20": "总市值", "f21": "流通市值", "f23": "市净率",
}
MKT_MAP = {"105": "NASDAQ", "106": "NYSE", "107": "AMEX"}
NUM_COLS = ["最新价", "涨跌幅", "涨跌额", "成交量", "成交额", "振幅", "换手率",
            "市盈率", "最高", "最低", "今开", "昨收", "总市值", "流通市值", "市净率"]


def fetch_page(pn):
    p = {"pn": str(pn), "pz": str(PAGE_SIZE), "po": "1", "np": "1",
         "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "invt": "2",
         "fid": "f20", "fs": FS, "fields": FIELDS}
    for rnd in range(3):
        try:
            r = requests.get(BASE, params=p, timeout=15)
            return (r.json().get("data") or {}).get("diff") or []
        except Exception:
            time.sleep(1 + rnd)
    return None


def main():
    all_items = []
    for pn in range(1, MAX_PAGES + 1):
        diff = fetch_page(pn)
        if diff is None:
            print(f"[page {pn}] 3 轮重试全失败，跳过", file=sys.stderr)
            continue
        if not diff:
            break
        all_items.extend(diff)
        try:
            last_mv = float(diff[-1].get("f20"))
        except (TypeError, ValueError):
            last_mv = float("inf")
        if last_mv < MCAP_FLOOR:
            print(f"[page {pn}] 市值跌穿 ${MCAP_FLOOR/1e6:.0f}M 地板，早停。累计 {len(all_items)} 只", file=sys.stderr)
            break
        if pn % 20 == 0:
            print(f"[page {pn}] 累计 {len(all_items)} 只，本页最小市值 ${last_mv/1e9:.2f}B", file=sys.stderr)
        time.sleep(0.15)

    if not all_items:
        print("ERROR: 未抓到任何数据", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(all_items)
    keep = [c for c in COL_MAP if c in df.columns]
    df = df[keep].rename(columns=COL_MAP)
    if "市场代码" in df.columns:
        df["交易所"] = df["市场代码"].astype(str).map(MKT_MAP).fillna("OTHER")
    for c in NUM_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # stdout = 纯 CSV（recipe_fetcher 捕获回传）；统计走 stderr
    df.to_csv(sys.stdout, index=False)
    nn = df["总市值"].notna().sum() if "总市值" in df.columns else 0
    print(f"OK: {len(df)} rows ({nn} 有市值)，交易所分布: {df['交易所'].value_counts().to_dict() if '交易所' in df.columns else '?'}", file=sys.stderr)


if __name__ == "__main__":
    main()
