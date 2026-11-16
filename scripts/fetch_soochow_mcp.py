#!/usr/bin/env python3
"""东吴宏观 Macro Fans MCP → fmdata 落地管线 (2026-11-09 接入).

9 个数据集, 全部 append-only + 首写冻结(PIT 语义: 已存日期永不重写, 服务端滚动窗重写不影响本地):
  money  : soochow_etf_flow / soochow_stock_flow / soochow_industry_flow / soochow_etf_flow_agg
           ← 原始 Excel(list_raw_excel_dates 仅留 10 个交易日, 每日收盘后新增 1 期)
           列策略: 个股/ETF 大表只留 身份列 + 近1日* + 规模/市值/融资余额快照(表头日期后缀剥掉);
                 行业/指数/分类小表全列保留(含 5d/20d 滚动窗, 🔴滚动窗禁回测拼接).
  timing : soochow_timing_section ← hist_section(fmt=json) 按 max_risk 阶梯翻页(top_n≤500), 10 口径全截面
  senti  : soochow_sentiment_matrix ← query_sentiment_by_date 原始 JSON 逐日一行
  qdii   : soochow_qdii_status ← get_overview(取统计日) + query_funds(全量基金状态)
  weekly : soochow_weekly_reports ← 解禁/增减持/再融资周报 JSON(list_*_dates 近5期回补)
  model  : soochow_macro_model ← macro_model_get 两个模型 JSON 月度快照(含其内部历史)
  brief  : soochow_daily_review(游资复盘md全文) / soochow_multi_news(多资产新闻日览md全文)
           / soochow_signal_pool(择时信号池概览md) ← GVRM 晨会看板素材 (2026-11-10 加)

用法: python3 fetch_soochow_mcp.py {money|timing|senti|qdii|weekly|model|review|news|pool|brief|daily} [--lookback N] [--dates d1,d2]
cron : daily 19:15 CST (money/qdii/timing/senti + brief 增量) + 周一~六 07:40 自愈 + 周一 08:30 weekly + 每月8日 09:00 model.
密钥 : /data/fmdata/.env 的 SOOCHOW_MCP_URL / SOOCHOW_API_KEY (Bearer=邮箱,东吴对口销售发放).
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

ENV_PATH = Path("/data/fmdata/.env")
STORE = Path("/data/fmdata/store/data/soochow")
CST = timezone(timedelta(hours=8))
BOARDS = ["全A", "港股", "全体ETF", "申万一级行业", "申万二级行业",
          "主要宽基指数", "风格指数", "港股指数", "商品指数", "期货指数"]
PAGE = 500  # hist_section top_n 服务端上限


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cst()}] {msg}", flush=True)


def load_env():
    env = {}
    for line in open(ENV_PATH):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    if not env.get("SOOCHOW_API_KEY"):
        raise SystemExit("SOOCHOW_API_KEY 缺失于 /data/fmdata/.env")
    return env


class MCP:
    """Streamable-HTTP MCP 客户端(会话制, SSE/JSON 双格式兼容).

    全局限速 MIN_INTERVAL(实测 429: 无间隔连打 ~15 次即触发), 429 指数退避.
    """

    MIN_INTERVAL = 1.0  # 两次调用最小间隔(秒)

    def __init__(self, env):
        self.base = env.get("SOOCHOW_MCP_URL", "http://101.133.169.44/api/all/mcp")
        self.h = {"Authorization": "Bearer " + env["SOOCHOW_API_KEY"],
                  "Content-Type": "application/json",
                  "Accept": "application/json, text/event-stream"}
        self._last = 0.0
        r = requests.post(self.base, headers=self.h, timeout=60, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "fmdata-fetch", "version": "1.0"}}})
        r.raise_for_status()
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.h["mcp-session-id"] = sid
        requests.post(self.base, headers=self.h, timeout=30,
                      json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _pace(self):
        wait = self.MIN_INTERVAL - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def call(self, name, args=None, retries=4):
        args = args or {}
        for attempt in range(retries):
            self._pace()
            try:
                r = requests.post(self.base, headers=self.h, timeout=300, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
                if r.status_code in (404, 410) or ("mcp-session-id" not in self.h and r.status_code == 400):
                    raise RuntimeError(f"session 失效(HTTP {r.status_code}), 需重启进程重初始化")
                r.raise_for_status()
                ct = r.headers.get("Content-Type", "")
                body = r.text
                if "text/event-stream" in ct:
                    data_lines = [l[5:] for l in body.splitlines() if l.startswith("data:")]
                    if not data_lines:
                        raise RuntimeError(f"{name} SSE 空响应")
                    d = json.loads(data_lines[-1])
                else:
                    d = json.loads(body)
                res = d.get("result", {})
                if res.get("isError"):
                    raise RuntimeError(f"{name} 工具报错: {res.get('content')}")
                texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
                if not texts:
                    return {}
                try:
                    return json.loads(texts[0])
                except (ValueError, TypeError):
                    return {"_raw_text": texts[0]}
            except (requests.RequestException, RuntimeError, ValueError) as e:
                if attempt == retries - 1:
                    raise
                backoff = 20 * (attempt + 1) if "429" in str(e) else 5
                log(f"  {name} 第{attempt+1}次失败: {e}, {backoff}s 后重试")
                time.sleep(backoff)
        return {}


# ---------- 通用 CSV append-only 工具 ----------

def append_csv(df: pd.DataFrame, rel: str, key_cols: list):
    """append-only + key 去重(已有 key 的行不重写 = 首写冻结)."""
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


def existing_dates(rel: str, date_col="data_date"):
    path = STORE / rel
    if not path.exists():
        return set()
    return set(pd.read_csv(path, usecols=[date_col], dtype=str)[date_col])


DATE_SUFFIX = re.compile(r"\(20\d{6}\)")

ETF_KEEP_HINTS = ("近1个交易日", "基金规模(亿元)", "基金份额(亿份)", "总规模")
STK_KEEP_HINTS = ("近1个交易日", "总市值(亿元)", "融资余额(亿元)")
ETF_IDENTITY = ("证券代码", "证券名称", "基金管理人", "上市日期", "跟踪指数代码", "跟踪指数名称",
                "投资类型(一级分类)", "投资类型(权益分类)")
STK_IDENTITY = ("证券代码", "证券名称", "上市日期", "申万一级行业", "申万二级行业")


def pick_columns(headers, identity, hints):
    keep = []
    for h in headers:
        h_clean = DATE_SUFFIX.sub("", str(h)).strip()
        if h in identity or h_clean in identity or any(k in h_clean for k in hints):
            keep.append(h)
    return keep


def norm_headers(headers):
    return [DATE_SUFFIX.sub("", str(h)).strip() for h in headers]


# ---------- money: 原始 Excel ----------

def fetch_money(mc: MCP, want_dates=None, lookback=10):
    """下载原始资金流 Excel(仅 10 个交易日窗口), 解析 6 个 sheet, 4 张表落地."""
    info = mc.call("list_raw_excel_dates", {})
    avail = info.get("available_dates", [])
    if want_dates:
        avail = [d for d in avail if d in set(want_dates)]
    avail = sorted(avail)[-lookback:]
    have = existing_dates("soochow_etf_flow.csv")
    todo = [d for d in avail if d not in have]
    log(f"money: 可用{len(avail)}期, 本地已有{len(have)}, 待拉{len(todo)}")
    stats = {}
    for d in todo:
        url_info = mc.call("get_raw_excel_download_url", {"trade_date": d})
        url = url_info.get("download_url")
        if not url:
            log(f"  {d} 无下载URL, 跳过")
            continue
        tmp = Path(f"/tmp/soochow_net_inflow_{d}.xlsx")
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        tmp.write_bytes(r.content)
        stats[d] = parse_money_excel(tmp, d)
        tmp.unlink(missing_ok=True)
        time.sleep(1)
    return stats


def parse_money_excel(path: Path, data_date: str):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    # ETF 逐券
    ws = wb["ETF_证券代码"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = list(rows[0])
    keep = pick_columns(hdr, ETF_IDENTITY, ETF_KEEP_HINTS)
    keep_names = [DATE_SUFFIX.sub("", str(h)).strip() for h in keep]
    df = pd.DataFrame(rows[1:], columns=norm_headers(hdr))[keep_names]
    df = df.dropna(subset=["证券代码"])
    df.insert(0, "data_date", data_date)
    out["etf"] = append_csv(df, "soochow_etf_flow.csv", ["data_date", "证券代码"])
    # A股逐券
    ws = wb["A股_证券代码"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = list(rows[0])
    keep = pick_columns(hdr, STK_IDENTITY, STK_KEEP_HINTS)
    df = pd.DataFrame(rows[1:], columns=norm_headers(hdr))[[DATE_SUFFIX.sub("", str(h)).strip() for h in keep]]
    df = df.dropna(subset=["证券代码"])
    df.insert(0, "data_date", data_date)
    out["stock"] = append_csv(df, "soochow_stock_flow.csv", ["data_date", "证券代码"])
    # 行业 L1+L2 (小表, 全列保留)
    frames = []
    for sheet, level in (("A股_申万一级行业", "L1"), ("A股_申万二级行业", "L2")):
        rows = list(wb[sheet].iter_rows(values_only=True))
        hdr = norm_headers(rows[0])
        d2 = pd.DataFrame(rows[1:], columns=hdr).dropna(subset=[hdr[0]])
        d2.insert(1, "level", level)
        frames.append(d2)
    df = pd.concat(frames, ignore_index=True)
    df.insert(0, "data_date", data_date)
    out["industry"] = append_csv(df, "soochow_industry_flow.csv", ["data_date", "证券代码"])
    # ETF 聚合: 跟踪指数 + 投资类型(小表全列)
    frames = []
    for sheet, agg in (("ETF_跟踪指数", "index"), ("ETF_投资类型(一级分类)", "type_l1"),
                       ("ETF_投资类型(权益分类)", "type_equity")):
        rows = list(wb[sheet].iter_rows(values_only=True))
        hdr = norm_headers(rows[0])
        d2 = pd.DataFrame(rows[1:], columns=hdr).dropna(subset=[hdr[0]])
        d2.insert(1, "agg_level", agg)
        frames.append(d2)
    df = pd.concat(frames, ignore_index=True)
    df.insert(0, "data_date", data_date)
    out["agg"] = append_csv(df, "soochow_etf_flow_agg.csv", ["data_date", "agg_level", df.columns[2]])
    wb.close()
    log(f"  {data_date}: etf+{out['etf']} stock+{out['stock']} industry+{out['industry']} agg+{out['agg']}")
    return out


# ---------- timing: 择时历史库全截面 ----------

def section_full(mc: MCP, date, board,
                 extra_fields="code,name,risk,momentum,score,chg,turnover,mktcap,amount,close,ma_short,ma_mid,signal,ind1,ind2"):
    """max_risk 阶梯翻页取全截面(risk 连续值无 NaN, 分页确定性).
    🔴 fields 指定后响应只含所列字段, code/name/ma/signal 必须显式列入, 否则行被丢弃."""
    """max_risk 阶梯翻页取全截面(risk 连续值无 NaN, 分页确定性)."""
    rows, seen = [], set()
    max_risk = None
    use_fields = bool(extra_fields)
    for _page in range(60):  # 60*500=3万 行硬上限保护
        args = {"date": date, "board": board, "sort_by": "risk", "order": "desc",
                "top_n": PAGE, "fmt": "json"}
        if _page == 0 and use_fields:
            args["fields"] = extra_fields
        if max_risk is not None:
            args["max_risk"] = max_risk
        try:
            r = mc.call("hist_section", args)
        except RuntimeError as e:
            if use_fields and "fields" in str(e):
                use_fields = False  # fields 参数不被接受, 回退默认字段
                continue
            raise
        batch = r.get("rows", [])
        if _page == 1 and not batch:
            return []
        new = [x for x in batch if x.get("code") and x["code"] not in seen]
        for x in new:
            seen.add(x["code"])
        rows.extend(new)
        if len(batch) < PAGE or not new:
            break
        max_risk = batch[-1].get("risk")
        if max_risk is None:
            break
        time.sleep(0.2)
    return rows


def fetch_timing(mc: MCP, want_dates=None):
    info = mc.call("hist_dates", {"limit": 100})
    m = re.search(r"共 (\d+) 个交易日（([\d-]+) ~ ([\d-]+)）", info.get("_raw_text", "") or json.dumps(info, ensure_ascii=False))
    dates = []
    if isinstance(info, dict) and info.get("_raw_text"):
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", info["_raw_text"])
    if not dates:
        dates = [k for k in (info or {}) if re.match(r"\d{4}-\d{2}-\d{2}$", k)]
    if want_dates:
        dates = [d for d in dates if d in set(want_dates)]
    have = existing_dates("soochow_timing_section.csv", date_col="date")
    empty_marker = STORE / "soochow_timing_section.empty"
    emptied = set(empty_marker.read_text().split()) if empty_marker.exists() else set()
    todo = sorted(set(dates) - have - emptied)
    log(f"timing: 服务端{len(set(dates))}期({m.group(2) if m else '?'}~{m.group(3) if m else '?'}), 本地已有{len(have)}, 空{len(emptied)}, 待拉{len(todo)}")
    total = 0
    for d in todo:
        day_rows = []
        for b in BOARDS:
            rs = section_full(mc, d, b)
            day_rows.extend(rs)
        if day_rows:
            df = pd.DataFrame(day_rows)
            n = append_csv(df, "soochow_timing_section.csv", ["date", "board", "code"])
            total += n
            log(f"  {d}: +{n} 行 (累计 {total})")
        else:
            with open(empty_marker, "a") as f:
                f.write(d + "\n")
            log(f"  {d}: 空截面, 记入 skip 标记")
    return total


# ---------- senti: 机构情绪矩阵 ----------

def fetch_senti(mc: MCP, want_dates=None):
    info = mc.call("list_available_sentiment_dates", {})
    dates = info.get("available_dates", [])
    if want_dates:
        dates = [d for d in dates if d in set(want_dates)]
    have = existing_dates("soochow_sentiment_matrix.csv", date_col="date")
    todo = sorted(set(dates) - have)
    log(f"senti: 可用{len(set(dates))}期, 已有{len(have)}, 待拉{len(todo)}")
    n = 0
    for d in todo:
        r = mc.call("query_sentiment_by_date", {"trade_date": d})
        rec = pd.DataFrame([{"date": d, "fetched_at": now_cst(),
                             "raw": json.dumps(r, ensure_ascii=False)}])
        n += append_csv(rec, "soochow_sentiment_matrix.csv", ["date"])
        time.sleep(0.3)
    log(f"senti: +{n} 期")
    return n


# ---------- qdii ----------

def fetch_qdii(mc: MCP):
    ov = mc.call("get_overview", {})
    stat_date = str(ov.get("数据统计日期", ""))
    if not stat_date:
        log("qdii: 总览无统计日期, 跳过")
        return 0
    d = datetime.strptime(stat_date, "%Y%m%d").strftime("%Y-%m-%d")
    have = existing_dates("soochow_qdii_status.csv")
    if d in have:
        log(f"qdii: {d} 已存在, 跳过")
        return 0
    funds = mc.call("query_funds", {})
    rows = funds.get("基金明细") or funds.get("results") or funds.get("funds") or []
    if not rows:
        log(f"qdii: query_funds 返回空 keys={list(funds)[:8]}, 跳过")
        return 0
    df = pd.DataFrame(rows)
    df.insert(0, "data_date", d)
    n = append_csv(df, "soochow_qdii_status.csv", ["data_date", df.columns[1]])
    log(f"qdii: {d} +{n} 行 (字段: {list(df.columns[1:8])})")
    return n


# ---------- weekly: 解禁/增减持/再融资 ----------

WEEKLY = [("unlock", "list_unlock_dates", "get_unlock_report"),
          ("holdings", "list_holdings_dates", "get_holdings_report"),
          ("refinancing", "list_refinancing_dates", "get_refinancing_report")]


def fetch_weekly(mc: MCP):
    n = 0
    for typ, list_tool, get_tool in WEEKLY:
        try:
            dl = mc.call(list_tool, {})
            dates = dl.get("available_dates") or dl.get("dates") or []
            if isinstance(dl, dict) and dl.get("_raw_text"):
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", dl["_raw_text"])
            for d in (dates or [None])[:5]:
                args = {"date": d} if d else {}
                r = mc.call(get_tool, args)
                period = (r.get("result") or {}).get("报告期") or (r.get("data_date") if isinstance(r, dict) else None) or str(d)
                rec = pd.DataFrame([{"type": typ, "period": period, "fetched_at": now_cst(),
                                     "raw": json.dumps(r, ensure_ascii=False)}])
                if append_csv(rec, "soochow_weekly_reports.csv", ["type", "period"]):
                    n += 1
                    log(f"  weekly {typ}: {period} 入库")
                time.sleep(0.3)
        except Exception as e:
            log(f"  weekly {typ} 失败: {e}")
    return n


# ---------- model: 宏观择时模型 ----------

def fetch_model(mc: MCP):
    lst = mc.call("macro_model_list", {})
    n = 0
    for f in lst.get("文件", []):
        name = f.get("name", "")
        if not name.endswith(".json"):
            continue
        r = mc.call("macro_model_get", {"name": name})
        month = now_cst()[:7]
        content = r if isinstance(r, dict) else {"_raw_text": str(r)[:200000]}
        rec = pd.DataFrame([{"month": month, "file": name, "updated": f.get("更新时间"),
                             "fetched_at": now_cst(),
                             "content": json.dumps(content, ensure_ascii=False)}])
        if append_csv(rec, "soochow_macro_model.csv", ["month", "file"]):
            n += 1
            log(f"  model: {name} ({month}) 入库")
    return n


# ---------- brief: 游资复盘 / 多资产新闻日览 / 择时信号池 (2026-11-10 GVRM 晨会看板) ----------
# 三个数据集 raw 列 = md 全文逐日一行(append-only 首写冻结); 服务端仅留近 10 个交易日窗口,
# 每日拉最新 + 回补窗口内缺日, 错过窗口即永久丢(与 Excel 10 日窗同款风险, cron 必须跑).


def _norm_date(d):
    return datetime.strptime(str(d).replace("-", ""), "%Y%m%d").strftime("%Y-%m-%d")


def _store_md(mc: MCP, list_tool: str, get_tool: str, csv_name: str, md_field: str,
              want_dates=None, list_key="available_dates"):
    info = mc.call(list_tool, {})
    dates = info.get(list_key, []) or []
    if isinstance(dates, dict):  # list_multi_news_dates 返回 {日期: 摘要}
        dates = sorted(dates.keys(), reverse=True)
    if want_dates:
        dates = [d for d in dates if _norm_date(d) in set(want_dates)]
    have = existing_dates(csv_name, date_col="date")
    todo = sorted(set(dates), reverse=True)  # 新在前, 最新一期优先入库
    todo = [d for d in todo if _norm_date(d) not in have]
    log(f"{csv_name}: 窗口内可用{len(set(dates))}日, 已有{len(have)}, 待拉{len(todo)}")
    n = 0
    for d in todo:
        try:
            r = mc.call(get_tool, {"date": d})
        except Exception as e:
            log(f"  {d} 拉取失败: {e}")
            continue
        md = r.get(md_field) or ""
        rd = _norm_date(r.get("review_date") or r.get("date") or d)
        if not md:
            log(f"  {d} md 为空, 跳过")
            continue
        rec = pd.DataFrame([{"date": rd, "fetched_at": now_cst(), "raw": md}])
        n += append_csv(rec, csv_name, ["date"])
        time.sleep(0.3)
    log(f"{csv_name}: +{n} 期")
    return n


def fetch_review(mc: MCP, want_dates=None):
    return _store_md(mc, "list_available_dates", "get_review",
                     "soochow_daily_review.csv", "review_markdown", want_dates)


def fetch_news(mc: MCP, want_dates=None):
    return _store_md(mc, "list_multi_news_dates", "get_multi_news",
                     "soochow_multi_news.csv", "report_markdown", want_dates)


def fetch_pool(mc: MCP):
    """择时信号池概览(md 文本, 无历史列表工具)——按文中'数据日期'去重, 每日一拉."""
    r = mc.call("signal_pool_overview", {"market": "all"})
    text = r.get("_raw_text") or (r.get("result") if isinstance(r.get("result"), str) else "")
    if not text:
        log(f"pool: 返回无文本 keys={list(r)[:6]}, 跳过")
        return 0
    m = re.search(r"数据日期 \*\*(\d{4}-\d{2}-\d{2})\*\*", text)
    d = m.group(1) if m else now_cst()[:10]
    have = existing_dates("soochow_signal_pool.csv", date_col="date")
    if d in have:
        log(f"pool: {d} 已存在, 跳过")
        return 0
    rec = pd.DataFrame([{"date": d, "fetched_at": now_cst(), "raw": text}])
    n = append_csv(rec, "soochow_signal_pool.csv", ["date"])
    log(f"pool: {d} +{n} 期")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["money", "timing", "senti", "qdii", "weekly", "model",
                                    "review", "news", "pool", "brief", "daily", "all"])
    ap.add_argument("--lookback", type=int, default=10)
    ap.add_argument("--dates", default="")
    args = ap.parse_args()
    want = [x.strip() for x in args.dates.split(",") if x.strip()] or None
    STORE.mkdir(parents=True, exist_ok=True)
    mc = MCP(load_env())
    jobs = {"money": lambda: fetch_money(mc, want, args.lookback),
            "timing": lambda: fetch_timing(mc, want),
            "senti": lambda: fetch_senti(mc, want),
            "qdii": lambda: fetch_qdii(mc),
            "weekly": lambda: fetch_weekly(mc),
            "model": lambda: fetch_model(mc),
            "review": lambda: fetch_review(mc, want),
            "news": lambda: fetch_news(mc, want),
            "pool": lambda: fetch_pool(mc)}
    brief = ("review", "news", "pool")
    if args.cmd == "brief":
        for k in brief:
            try:
                jobs[k]()
            except Exception as e:
                log(f"{k} 失败: {e}")
    elif args.cmd in jobs:
        jobs[args.cmd]()
    elif args.cmd == "daily":
        for k in ("money", "qdii", "timing", "senti") + brief:
            try:
                jobs[k]()
            except Exception as e:
                log(f"{k} 失败: {e}")
    else:
        for k, fn in jobs.items():
            try:
                fn()
            except Exception as e:
                log(f"{k} 失败: {e}")
    log("done.")


if __name__ == "__main__":
    main()
