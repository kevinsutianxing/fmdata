#!/usr/bin/env python3
"""华泰智研 MCP 宏观/情绪/港股资金面 → fmdata recipe 落地管线 (2026-11-14 接入).

4 个数据集 (全部 append-only 首写冻结 = 取数即冻结; 服务端为研报衍生序列可能回写, 已存日期永不重写):
  global : htsc_global_macro_factors ← query_table_data(全球宏观因子_全球宏观因子序列,
           资产配置数据库, 619行周频 2015-01..now, 7列: date+增长/通胀/流动性/法币信用/风险偏好/AI科技)
  quadrant: htsc_macro_quadrant ← 5张中美宏观象限表(月频) melt 成长表:
           table_name/date/indicator/value (中国信贷脉冲/中国增长_通胀/库存周期_中国_ppi/
           盈利_流动性周期_中国/美国信贷脉冲)
  emotion: htsc_market_emotion ← get_market_emotion(market=A, 2020-01..now 日频, %)
  hkshort: htsc_hk_short_selling ← get_hk_short_selling_ratio(2021..now 周频, %)

🔴全部 research_only, 禁回测 (研报衍生序列, PIT 未验证, 服务端滚动窗可能重写)。

用法: python3 fetch_htsc_mcp.py {global|quadrant|emotion|hkshort|all}
cron : 每周二 08:27 CST (flock 防重入), 失败走 send_feishu_alert.sh
密钥 : /data/fmdata/.env 的 HTSC_MCP_URL / HTSC_APP_KEY
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ENV_PATH = Path("/data/fmdata/.env")
STORE = Path("/data/fmdata/store/data/htsc")
PAGE = 500          # query_table_data page_size 上限(实测 619/700 返回空)
MIN_INTERVAL = 1.0  # 每次调用间隔; 限速未知保守 1s

QUADRANT_TABLES = [
    "中美宏观象限_中国信贷脉冲",
    "中美宏观象限_中国增长_通胀",
    "中美宏观象限_库存周期_中国_ppi",
    "中美宏观象限_盈利_流动性周期_中国",
    "中美宏观象限_美国信贷脉冲",
]
EMOTION_START_YEAR = 2020   # 实测 2019 及更早=暂无数据
HKSHORT_START_YEAR = 2021   # 实测 2020 及更早=暂无数据


def now_cst():
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cst()}] {msg}", flush=True)


def load_env():
    env = {}
    for line in open(ENV_PATH):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    if not env.get("HTSC_APP_KEY"):
        raise SystemExit("HTSC_APP_KEY 缺失于 /data/fmdata/.env")
    return env


class HTSC:
    """无状态 streamable-http MCP 客户端(直接 tools/call, SSE data: 行拼接)。"""

    def __init__(self, env):
        self.url = env.get("HTSC_MCP_URL", "https://inst.htsc.com/mcp/v1/ris/htsc_research_mcp")
        self.key = env["HTSC_APP_KEY"]
        self._last = 0.0

    def _pace(self):
        wait = MIN_INTERVAL - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def call(self, name, args, retries=3):
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": name, "arguments": args}}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "HTSC_APP_KEY": self.key})
        for attempt in range(retries):
            self._pace()
            try:
                raw = urllib.request.urlopen(req, timeout=120).read().decode()
                data = [l[5:] for l in raw.splitlines() if l.startswith("data:")]
                obj = json.loads("".join(data) if data else raw)
                res = obj.get("result", {})
                texts = [c.get("text", "") for c in res.get("content", [])
                         if c.get("type") == "text"]
                md = ""
                if texts:
                    try:
                        md = json.loads(texts[0]).get("data", "")
                    except (ValueError, TypeError):
                        md = texts[0]
                return md
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries - 1:
                    log(f"  {name} HTTP 429, 60s 退避重试({attempt + 1}/{retries})")
                    time.sleep(60)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                if attempt < retries - 1:
                    log(f"  {name} 第{attempt + 1}次失败: {e}, 60s 后重试")
                    time.sleep(60)
                    continue
                raise
        raise RuntimeError(f"{name} 重试耗尽")


DATE_CELL = re.compile(r"^20\d\d-\d\d-\d\d$")


def md_to_df(md):
    """通用 markdown 表 → DataFrame。只保留首列为日期的行(表头 date/日期/序号 等自动剔除)。"""
    rows = []
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not cells or not DATE_CELL.match(cells[0]):
                continue
            rows.append(cells)
    if not rows:
        return pd.DataFrame()
    n = max(len(r) for r in rows)
    return pd.DataFrame([r + [""] * (n - len(r)) for r in rows])


def append_csv(df: pd.DataFrame, rel: str, key_cols):
    """append-only + key 去重(已有 key 行不重写 = 首写冻结)。"""
    path = STORE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_csv(path, dtype=str)
        have = set(map(tuple, old[key_cols].astype(str).values))
        df = df[~df[key_cols].astype(str).apply(tuple, axis=1).isin(have)]
        if df.empty:
            return 0
        df.to_csv(path, mode="a", index=False, header=False)
    else:
        df.to_csv(path, index=False)
    return len(df)


# ---------- global: 全球宏观因子序列 (分页全量, 宽表冻结新增日期) ----------

def fetch_global(htsc):
    frames = []
    page = 1
    while True:
        md = htsc.call("query_table_data", {
            "table_name": "全球宏观因子_全球宏观因子序列",
            "sort_by": "date", "sort_desc": False, "page": page, "page_size": PAGE})
        if not md.strip():
            break
        df = md_to_df(md)
        if df.empty or not re.match(r"20\d\d-", str(df.iloc[0, 0])):
            break
        frames.append(df)
        if len(df) < PAGE:
            break
        page += 1
    if not frames:
        log("global: 无数据")
        return 0
    full = pd.concat(frames, ignore_index=True).drop_duplicates()
    full.columns = ["date"] + [f"c{i}" for i in range(1, len(full.columns))]
    # 列名从 get_table_info 重新确认(防列序漂移)
    info = htsc.call("get_table_info", {"table_name": "全球宏观因子_全球宏观因子序列"})
    cols = re.findall(r"\| \d+ \| (\S+) \| \w+ \|", info)
    cols = [c for c in cols if c != "✓"]
    if cols and len(cols) == len(full.columns):
        full.columns = cols
    else:
        names = ["date", "增长", "通胀", "流动性", "法币信用", "风险偏好", "AI科技"]
        if len(names) == len(full.columns):
            full.columns = names
    n = append_csv(full, "htsc_global_macro_factors.csv", ["date"])
    log(f"global: 服务端{len(full)}行({full.date.min()}..{full.date.max()}), 新增冻结 {n} 行")
    return n


# ---------- quadrant: 中美宏观象限 5 表 melt 长表 ----------

def fetch_quadrant(htsc):
    total = 0
    for t in QUADRANT_TABLES:
        frames, page = [], 1
        while True:
            md = htsc.call("query_table_data", {
                "table_name": t, "sort_by": "date", "sort_desc": False,
                "page": page, "page_size": PAGE})
            if not md.strip():
                break
            df = md_to_df(md)
            if df.empty or not re.match(r"20\d\d-", str(df.iloc[0, 0])):
                break
            frames.append(df)
            if len(df) < PAGE:
                break
            page += 1
        if not frames:
            log(f"quadrant: {t} 无数据")
            continue
        wide = pd.concat(frames, ignore_index=True).drop_duplicates()
        info = htsc.call("get_table_info", {"table_name": t})
        cols = [c for c in re.findall(r"\| \d+ \| (\S+) \| \w+ \|", info) if c != "✓"]
        if cols and len(cols) == len(wide.columns):
            wide.columns = cols
        first = wide.columns[0]
        if first != "date":
            wide = wide.rename(columns={first: "date"})
        long = wide.melt(id_vars="date", var_name="indicator", value_name="value")
        long = long[long.value != ""]
        long.insert(0, "table_name", t)
        n = append_csv(long, "htsc_macro_quadrant.csv", ["table_name", "date", "indicator"])
        log(f"quadrant: {t} {len(wide)}行x{len(wide.columns) - 1}指标, 新增长表 {n} 行")
        total += n
    return total


# ---------- emotion / hkshort: 日期-数值 两列序列 (逐年块回拉) ----------

def _fetch_series(htsc, tool, args_fn, start_year, rel, unit="%"):
    have = set()
    if (STORE / rel).exists():
        have = set(pd.read_csv(STORE / rel, usecols=["date"], dtype=str)["date"])
    year = datetime.utcnow().year
    frames = []
    while year >= start_year:
        md = htsc.call(tool, args_fn(year))
        df = md_to_df(md)
        if not df.empty and re.match(r"20\d\d-", str(df.iloc[0, 0])):
            frames.append(df)
        year -= 1
    if not frames:
        log(f"{rel}: 无数据")
        return 0
    full = pd.concat(frames, ignore_index=True)
    full = full[full.iloc[:, 0].str.match(r"20\d\d-\d\d-\d\d")]
    full.columns = ["date", "value"][: len(full.columns)]
    full["value"] = pd.to_numeric(full["value"], errors="coerce")
    full["unit"] = unit
    full = full.dropna(subset=["value"]).drop_duplicates(subset=["date"])
    n = append_csv(full, rel, ["date"])
    log(f"{rel}: 服务端{len(full)}行({full.date.min()}..{full.date.max()}), 新增冻结 {n} 行")
    return n


def fetch_emotion(htsc):
    return _fetch_series(
        htsc, "get_market_emotion",
        lambda y: {"market": "A", "start_date": f"{y}-01-01", "end_date": f"{y}-12-31"},
        EMOTION_START_YEAR, "htsc_market_emotion.csv")


def fetch_hkshort(htsc):
    return _fetch_series(
        htsc, "get_hk_short_selling_ratio",
        lambda y: {"start_date": f"{y}-01-01", "end_date": f"{y}-12-31"},
        HKSHORT_START_YEAR, "htsc_hk_short_selling.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["global", "quadrant", "emotion", "hkshort", "all"])
    args = ap.parse_args()
    STORE.mkdir(parents=True, exist_ok=True)
    htsc = HTSC(load_env())
    jobs = {"global": fetch_global, "quadrant": fetch_quadrant,
            "emotion": fetch_emotion, "hkshort": fetch_hkshort}
    keys = list(jobs) if args.cmd == "all" else [args.cmd]
    for k in keys:
        try:
            jobs[k](htsc)
        except Exception as e:
            log(f"{k} 失败: {e}")
            if args.cmd != "all":
                raise
    log("done.")


if __name__ == "__main__":
    main()
