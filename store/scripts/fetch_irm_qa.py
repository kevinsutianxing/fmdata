#!/usr/bin/env python3
"""互动易问答数据抓取 (cninfo) — 增量版 2026-08-18 改造

旧版三大死因(6/18 起 stale):
  1) 每次全市场全量重爬(~5600只 × 全部历史), 75min+ 必超 1h 预算
  2) 300万行全文堆内存(~1.25GB), 3.6GB 主机 OOM 区
  3) 最后一次性写盘, 超时被杀 = 零输出

新版设计:
  - 直调 cninfo API 带 startDay 增量窗口(akshare 包装不透传日期参数, 但底层支持)
  - 游标断点续爬: state 记 {run_start_day, cursor, cycle_start}, 单次调用默认最多
    --limit 只(1200, ~8min < 治理客户端 660s), 每日一跑 → 全市场 ~5 天一轮, 被杀进度不丢
  - append 模式: 只追加 QUESTION_ID 未见过的新问题, 零全量重写, 内存 O(单次新增)
  - orgId 本地缓存(砍一半每只往返)
  - 窗口 = max(3, 距周期起点天数+2): 轮内第 5 天爬的股票窗口≥7d, 不漏问;
    断档两个月首跑自动宽窗。注: startDay 按提问时间过滤, 晚到>窗口的回答会漏,
    研究语料场景可接受。

用法: python3 fetch_irm_qa.py [--limit N]   # --limit 0 = 一次跑完全市场(手动补爬用)
列 schema 与旧 CSV 完全一致(11 列, 无 SECURITY_CODE — 旧脚本加了也被列过滤丢弃)。
"""
import argparse
import json
import os
import socket
import sys
import time
from datetime import timedelta

import pandas as pd
import requests

# akshare 的 _fetch_org_id 内部 requests 不带 timeout — 服务端掐连接不回包会永久挂死
# (2026-08-18 实测: 4000 只后 cninfo 限流, 爬虫卡死 47min)。全局 socket 超时兜底所有
# 未显式设 timeout 的调用; fetch_window 自带 timeout=15 不受影响。
socket.setdefaulttimeout(20)

STOCK_LIST_CSV = "/home/ubuntu/fmdata/store/reference/stock_list.csv"
OUTPUT_CSV = "/home/ubuntu/fmdata/store/fundamentals/irm_qa.csv"
STATE_JSON = "/home/ubuntu/fmdata/store/fundamentals/irm_qa_state.json"
ORGID_CACHE = "/home/ubuntu/fmdata/store/reference/irm_orgid_cache.json"
API = "https://irm.cninfo.com.cn/newircs/company/question"

BATCH_DELAY = 0.15      # 增量 payload 极小, 比旧版 0.3 轻
FLUSH_EVERY = 200       # 每 200 只 append 落盘一次, 超时也保住进度
MAX_PAGES = 10          # 与 akshare 上限一致(窗口模式下几乎不会翻页)
CONSEC_FAIL_ABORT = 50  # 连续 50 只失败 → 判定端点异常, 保状态退出

SOURCE_MAP = {"2": "APP", "5": "公众号", "4": "网站"}
# API internal → 输出列(镜像 akshare 内部映射 + 旧脚本中文→英文的净效果)
COLS = ["SECURITY_CODE_SRC", "SECURITY_NAME", "INDUSTRY", "QUESTION", "QUESTIONER",
        "SOURCE", "QUESTION_DATE", "UPDATE_DATE", "QUESTION_ID", "ANSWER_CONTENT", "ANSWERER"]


def load_stock_codes():
    df = pd.read_csv(STOCK_LIST_CSV, dtype=str)
    code_col = "ts_code" if "ts_code" in df.columns else df.columns[0]
    codes = df[code_col].str.replace(r"\.\w+$", "", regex=True).tolist()
    # 互动易是深交所平台: 沪市(6/9 开头)公司问答在上证e互动, 本 API 恒返 0 行
    # (2026-08-18 实测: 600519/688981 合法 orgid 也为空; 老数据 6 开头占比≈0)
    codes = [c for c in codes if len(c) == 6 and c[0] in "023"]
    return sorted(set(codes))


def load_state():
    if os.path.exists(STATE_JSON):
        try:
            return json.load(open(STATE_JSON))
        except Exception:
            pass
    return {}


def save_state(st):
    tmp = STATE_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_JSON)


def load_orgid_cache():
    if os.path.exists(ORGID_CACHE):
        try:
            return json.load(open(ORGID_CACHE))
        except Exception:
            pass
    return {}


def save_orgid_cache(cache):
    tmp = ORGID_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, ORGID_CACHE)


ORGID_URL = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"


def get_orgid(code, cache):
    """本地缓存优先; 未命中直调 queryKeyboardInfo。
    不用 akshare 的 _fetch_org_id — 它 requests 不带 timeout, 服务端掐连接后永久挂死
    (2026-08-18 两次实测各挂 47min/13min, socket.setdefaulttimeout 也拦不住 urllib3
    的 None-timeout 路径)。
    ★必须校验返回条目的 stockCode == 请求代码: queryKeyboardInfo 是模糊搜索, 盲取
    data[0] 会拿到别的公司(如 689xxx 模糊命中 301500), 用错 orgid 拉回的全是别家
    问答 → 与已入库行重复污染 (2026-08-18 实测污染 10013 行)。失败不缓存 → 下轮重试。"""
    if code in cache:
        return cache[code]
    oid = ""
    for _ in range(2):
        try:
            r = requests.post(ORGID_URL, params={"_t": "1691144074"},
                              data={"keyWord": code}, timeout=10)
            entries = r.json().get("data") or []
            exact = next((e for e in entries
                          if str(e.get("stockCode") or "") == code), None)
            if exact:
                oid = str(exact.get("secid") or "")
                if oid:
                    break
        except Exception:
            pass
        time.sleep(1)
    if oid:
        cache[code] = oid
    return oid


def epoch_ms_to_str(ms):
    if ms is None or ms == "":
        return None
    try:
        ts = pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert("Asia/Shanghai")
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def normalize(r, code):
    trade = r.get("trade")
    industry = trade[0] if isinstance(trade, list) and trade else trade
    src = SOURCE_MAP.get(str(r.get("pubClient") or ""), "网站")
    return {
        "SECURITY_CODE_SRC": r.get("stockCode") or code,
        "SECURITY_NAME": r.get("companyShortName"),
        "INDUSTRY": industry,
        "QUESTION": r.get("mainContent"),
        "QUESTIONER": r.get("authorName"),
        "SOURCE": src,
        "QUESTION_DATE": epoch_ms_to_str(r.get("pubDate")),
        "UPDATE_DATE": epoch_ms_to_str(r.get("updateDate")),
        "QUESTION_ID": str(r.get("indexId") or ""),
        "ANSWER_CONTENT": r.get("attachedContent"),
        "ANSWERER": r.get("attachedAuthor"),
    }


def fetch_window(code, orgid, start_day):
    """带 startDay 拉单只, 返回原始 rows 列表。单只失败重试一次后放弃(窗口≥3d 兜底)。"""
    params = {"stockcode": code, "orgId": orgid, "pageSize": "1000", "pageNum": "1",
              "keyWord": "", "startDay": start_day, "endDay": ""}
    rows, page, total_page = [], 1, 1
    while page <= min(total_page, MAX_PAGES):
        params["pageNum"] = str(page)
        try:
            j = requests.post(API, params=params, timeout=15).json()
        except Exception:
            time.sleep(2)
            try:
                j = requests.post(API, params=params, timeout=15).json()
            except Exception:
                return rows, True
        total_page = int(j.get("totalPage") or 1)
        rows.extend(j.get("rows") or [])
        page += 1
        if page <= min(total_page, MAX_PAGES):
            time.sleep(0.2)
    return rows, False


def append_rows(buffer):
    df = pd.DataFrame(buffer)[COLS]
    need_header = not os.path.exists(OUTPUT_CSV)
    df.to_csv(OUTPUT_CSV, mode="a", header=need_header, index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1200, help="本次最多处理只数; 0=不限(手动补爬)")
    args = ap.parse_args()

    codes = load_stock_codes()
    n = len(codes)
    state = load_state()
    today = pd.Timestamp.now(tz="Asia/Shanghai").normalize()

    # 续跑: 复用上次 run_start_day + cursor; 新周期: 窗口按周期起点算
    cursor = int(state.get("cursor", 0))
    if cursor >= n or not state.get("run_start_day"):
        cursor = 0
        cycle_start = today
        gap_days = 999
        if os.path.exists(OUTPUT_CSV):
            meta = pd.read_csv(OUTPUT_CSV, usecols=["QUESTION_DATE"])
            qd = pd.to_datetime(meta["QUESTION_DATE"], errors="coerce").max()
            if pd.notna(qd):
                gap_days = (today - qd.tz_localize("Asia/Shanghai")).days
        window = max(3, gap_days + 2, 4)  # 全量兜底时 gap 大 → 宽窗
        run_start_day = (today - timedelta(days=window)).strftime("%Y-%m-%d")
    else:
        run_start_day = state["run_start_day"]
        cycle_start = pd.Timestamp(state["cycle_start"]).tz_localize("Asia/Shanghai")
        # 轮内窗口须盖住已过天数(轮内第 k 天爬的股票窗口 ≥ k+2)
        elapsed = (today - cycle_start).days
        window = max(int(state.get("window", 3)), elapsed + 2, 3)
        run_start_day = (today - timedelta(days=window)).strftime("%Y-%m-%d")

    todo = codes[cursor:] if args.limit == 0 else codes[cursor:cursor + args.limit]
    print(f"[irm_qa] stocks={n} cursor={cursor} todo={len(todo)} "
          f"window={window}d startDay={run_start_day}", flush=True)

    # 已见 QUESTION_ID 集(去重); 1.25GB 单列扫描 ~1min, 只在周期首跑做
    if cursor == 0 and os.path.exists(OUTPUT_CSV):
        ids = pd.read_csv(OUTPUT_CSV, usecols=["QUESTION_ID"], dtype=str)["QUESTION_ID"]
        seen = set(ids.dropna())
    else:
        seen = set()
    print(f"[irm_qa] seen_ids={len(seen)}", flush=True)

    org_cache = load_orgid_cache()
    buffer, added, failed, consec = [], 0, 0, 0
    t0 = time.time()
    for i, code in enumerate(todo):
        oid = get_orgid(code, org_cache)
        if not oid:
            failed += 1
            continue
        rows, err = fetch_window(code, oid, run_start_day)
        if err:
            failed += 1
            consec += 1
            if consec >= CONSEC_FAIL_ABORT:
                print(f"[irm_qa] ABORT: {consec} consecutive failures at {code}, "
                      f"progress saved (cursor={cursor + i})", flush=True)
                break
            continue
        consec = 0
        for r in rows:
            qid = str(r.get("indexId") or "")
            if qid and qid not in seen:
                seen.add(qid)
                buffer.append(normalize(r, code))
        if (i + 1) % FLUSH_EVERY == 0:
            # 无论有无新增都落 state + 打进度(否则零问答段会静默无输出, 无法区分卡死)
            if buffer:
                append_rows(buffer)
                added += len(buffer)
                buffer = []
            save_orgid_cache(org_cache)
            save_state({"run_start_day": run_start_day, "cursor": cursor + i + 1,
                        "cycle_start": str(cycle_start.date()), "window": window})
            print(f"  [{i+1}/{len(todo)}] added={added} failed={failed} "
                  f"elapsed={time.time()-t0:.0f}s", flush=True)
        time.sleep(BATCH_DELAY)

    if buffer:
        append_rows(buffer)
        added += len(buffer)

    new_cursor = cursor + min(len(todo), i + 1 if todo else 0)
    done_cycle = new_cursor >= n
    if done_cycle:
        save_state({})  # 周期完成 → 清状态, 下次新窗口
    else:
        save_state({"run_start_day": run_start_day, "cursor": new_cursor,
                    "cycle_start": str(cycle_start.date()), "window": window})
    save_orgid_cache(org_cache)
    print(f"[irm_qa] DONE: added={added} failed={failed} cursor={new_cursor}/{n} "
          f"cycle={'COMPLETE' if done_cycle else 'in-progress'} "
          f"elapsed={time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
