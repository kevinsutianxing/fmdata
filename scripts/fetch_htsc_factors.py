#!/usr/bin/env python3
"""华泰智研 MCP 金工因子 → fmdata 统一因子库 (2026-11-14 接入).

4 个金工因子(get_quant_factor, 日频全A截面), 按 2026-11-14 准入门判定:
  深度>=24月度观测 且 CSI1800 覆盖>=95% (截面=2026-10-30 vs spine_1800 2026-10):
    composite_value    综合价值因子     PASS (server史~2005, 覆盖99.89%)
    composite_growth   综合成长因子     PASS (server史~2005, 覆盖99.89%)
    vp_fullband_fusion 全频段量价融合   PASS (server史~2017-01, 覆盖99.44%)
    llm_fadt           LLM-FADT因子    FAIL 覆盖34.11%(838只,深度OK) → 仅日度快照积累

物化口径: 月末截面(该月最后一个交易日, 与 FactorStore ts202 同口径), 窗口 2021-01..当前
  (对齐 cjpy70/spine_1800 网格; server 有更深历史, 需要时 --start 扩窗重拉)。
  parquet: store/factors/htsc/<slug>.parquet 长表 ym(str YYYYMM)/ts_code/value — 取数即冻结,
  已有 ym 键永不重写(增量只补缺月)。
  覆盖审计: coverage.csv (factor × ym → CSI1800 覆盖率), manifest.json 登记因子元数据。

🔴PIT 未验证: 服务端可能重写历史 → catalog pit_status=non_pit_unverified,
  FactorStore htsc.* 命名空间走 cjpy 同款非PIT围栏(默认抛错, allow_nonpit=True 放行+审计)。

用法:
  python3 fetch_htsc_factors.py backfill [--start 2021-01]   # 全史物化(幂等,只补缺月)
  python3 fetch_htsc_factors.py monthly                      # 月度增量(cron, 只补已收月缺月)
  python3 fetch_htsc_factors.py pending                      # llm_fadt 日度快照冻结(cron)
  python3 fetch_htsc_factors.py coverage                     # 重建 coverage.csv+manifest.json
cron: 每月3日 07:43 monthly + 工作日 08:13 pending (TZ Asia/Shanghai, flock)
密钥: /data/fmdata/.env 的 HTSC_MCP_URL / HTSC_APP_KEY
"""
import argparse
import calendar
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import urllib.request
import urllib.error

ENV_PATH = Path("/data/fmdata/.env")
STORE = Path("/data/fmdata/store/factors/htsc")
PENDING = Path("/data/fmdata/store/factors/htsc_pending")
CST_NOW = datetime.utcnow() + timedelta(hours=8)  # 钉 Asia/Shanghai

# slug → (中文因子名, server 史起始(探查值), 准入门)
FACTORS = {
    "composite_value": ("综合价值因子", "2005", "PASS(2026-10覆盖99.89%)"),
    "composite_growth": ("综合成长因子", "2005", "PASS(2026-10覆盖99.89%)"),
    "vp_fullband_fusion": ("全频段量价融合因子", "2017-01", "PASS(2026-10覆盖99.44%)"),
}
PENDING_FACTORS = {"llm_fadt": ("LLM-FADT因子", "2019-08", "FAIL覆盖34.11%<95%(838只)")}
WINDOW_START = "2021-01"  # 物化窗起点(对齐 spine_1800/cjpy70)

MIN_INTERVAL = 1.0  # 每次调用间隔(秒); 限速未知, 保守 1s


def log(msg):
    print(f"[{(datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')}] {msg}",
          flush=True)


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
    """无状态 streamable-http MCP 客户端(可跳过 initialize, 直接 tools/call)。"""

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


ROW_RE = re.compile(
    r"^\|\s*(?:(20\d\d-\d\d-\d\d)\s*\|)?\s*([^|]+?)\s*\|\s*(\d{6})\s*\|\s*([-+0-9.eE]+|nan|NaN)\s*\|$")


def parse_quant_md(md):
    """金工因子 markdown → [(date_or_None, name, code6, value)]。
    无日期调用=3列(取返回头的数据日期); 区间调用=4列(日期在首列)。"""
    rows, cur_date = [], None
    for line in md.splitlines():
        m = re.match(r"^\*\*数据日期\*\*: (20\d\d-\d\d-\d\d)", line.strip())
        if m:
            cur_date = m.group(1)
        m = ROW_RE.match(line.strip())
        if m and m.group(1) != "股票名称":
            d = m.group(1) or cur_date
            if d:
                try:
                    v = float(m.group(4))
                except ValueError:
                    continue
                rows.append((d, m.group(3), v))
    return rows


def to_ts_code(code6):
    """裸6位 → ts_code: 6→.SH, 0/3→.SZ, 4/8/92→.BJ (北交所保留行, CSI1800 读取时自然过滤)。"""
    if code6.startswith("6"):
        return code6 + ".SH"
    if code6.startswith(("0", "3")):
        return code6 + ".SZ"
    return code6 + ".BJ"


def month_end_window(ym):
    """'202609' → ('2026-09-25','2026-09-30') 区间(月末±6天, 必含≥1交易日, 国庆/春节月末安全)。"""
    y, m = int(ym[:4]), int(ym[4:])
    last = calendar.monthrange(y, m)[1]
    end = datetime(y, m, last)
    return (end - timedelta(days=5)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def month_cross_section(htsc, cn_name, ym):
    """单月月末截面 → DataFrame(ts_code, value)。区间内取该月最大日期行=月末最后交易日。"""
    s, e = month_end_window(ym)
    md = htsc.call("get_quant_factor", {"start_date": s, "end_date": e,
                                        "factor_names": [cn_name]})
    rows = [(d, c, v) for d, c, v in parse_quant_md(md) if d[:7].replace("-", "") == ym]
    if not rows:
        return pd.DataFrame(columns=["ts_code", "value"])
    last_day = max(d for d, _, _ in rows)
    sub = [(to_ts_code(c), v) for d, c, v in rows if d == last_day]
    df = pd.DataFrame(sub, columns=["ts_code", "value"])
    return df.groupby("ts_code", as_index=False)["value"].last().sort_values("ts_code")


def ym_range(start_ym, end_ym):
    out, y, m = [], int(start_ym[:4]), int(start_ym[4:])
    ey, em = int(end_ym[:4]), int(end_ym[4:])
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def write_parquet_atomic(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def sync_factor(htsc, slug, cn_name, start_ym, end_ym):
    """幂等物化: 读已有 parquet → 只补缺 ym → 键去重(已有冻结) → 原子写回。"""
    path = STORE / f"{slug}.parquet"
    have = {}
    if path.exists():
        old = pd.read_parquet(path, columns=["ym"])
        have = set(old.ym.unique())
        old_df = pd.read_parquet(path)
    todo = [y for y in ym_range(start_ym, end_ym) if y not in have]
    log(f"{slug}: 已有{len(have)}月, 待补{len(todo)}月 ({todo[0] if todo else '-'}.."
        f"{todo[-1] if todo else '-'})")
    frames = [old_df] if path.exists() else []
    got = 0
    for ym in todo:
        df = month_cross_section(htsc, cn_name, ym)
        if df.empty:
            log(f"  {ym}: 无数据(可能非交易日/未来月), 跳过")
            time.sleep(0.5)
            continue
        df.insert(0, "ym", ym)
        frames.append(df)
        got += 1
        if got % 12 == 0:
            log(f"  已补 {got}/{len(todo)} 月")
    if not frames:
        return len(have)
    full = pd.concat(frames, ignore_index=True)
    full = full.drop_duplicates(subset=["ym", "ts_code"], keep="first")  # 已有月冻结
    full = full.sort_values(["ym", "ts_code"], kind="mergesort").reset_index(drop=True)
    write_parquet_atomic(full, path)
    months = full.ym.nunique()
    log(f"{slug}: 落盘 {len(full)} 行 / {months} 月 -> {path}")
    return months


def closed_month_end():
    """已收月的最后 ym(当月未完不算; 例 2026-11-14 → '202610')。"""
    now = CST_NOW
    y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f"{y:04d}{m:02d}"


def cmd_backfill(htsc, start):
    end = closed_month_end()
    for slug, (cn, _, _) in FACTORS.items():
        sync_factor(htsc, slug, cn, start.replace("-", ""), end)
    cmd_pending(htsc)
    build_meta()


def cmd_monthly(htsc):
    end = closed_month_end()
    for slug, (cn, _, _) in FACTORS.items():
        sync_factor(htsc, slug, cn, WINDOW_START.replace("-", ""), end)
    build_meta()


def cmd_pending(htsc):
    """不达标因子(llm_fadt)日度快照: 取数即冻结, 已有日期不重写。"""
    for slug, (cn, _, _) in PENDING_FACTORS.items():
        d = PENDING / slug
        d.mkdir(parents=True, exist_ok=True)
        # 最新一期(无日期调用) + 近5日补洞(区间)
        md = htsc.call("get_quant_factor", {"factor_names": [cn]})
        rows = parse_quant_md(md)
        seen_files = {f.stem for f in d.glob("*.parquet")}
        if rows:
            latest = max(r[0] for r in rows)
            md2 = htsc.call("get_quant_factor", {"start_date": latest, "end_date": latest,
                                                 "factor_names": [cn]})
            rows2 = parse_quant_md(md2) or rows
            df = pd.DataFrame([(to_ts_code(c), v) for _, c, v in rows2],
                              columns=["ts_code", "value"])
            fp = d / f"{latest}.parquet"
            if latest not in seen_files:
                write_parquet_atomic(df, fp)
                log(f"pending {slug}: {latest} +{len(df)} 行 冻结")
            else:
                log(f"pending {slug}: {latest} 已冻结, 跳过")


def build_meta():
    """重建 manifest.json + coverage.csv (coverage=CSI1800 spine 当月覆盖比例)。"""
    import pyarrow.parquet as pq
    spine_path = None
    import glob
    hits = sorted(glob.glob("/home/ubuntu/claude-workspace/csi1800-enhancement/data/spine_1800_*.parquet"))
    if hits:
        spine = pd.read_parquet(hits[0], columns=["ym", "ts_code"])
        spine_sets = spine.groupby("ym").ts_code.apply(set).to_dict()
    else:
        spine_sets = {}
    manifest = {"factors": [], "pending": [], "window_start": WINDOW_START,
                "convention": "月末最后交易日截面; 取数即冻结(已有ym键不重写); PIT未验证(服务端或重写历史)"}
    cov_rows = {}
    for slug, (cn, srv_start, gate) in {**FACTORS, **PENDING_FACTORS}.items():
        path = STORE / f"{slug}.parquet"
        if path.exists():
            df = pd.read_parquet(path)
            months = sorted(df.ym.unique())
            per = {}
            for ym, g in df.groupby("ym"):
                per[ym] = round(len(set(g.ts_code) & spine_sets.get(ym, set())) /
                                max(len(spine_sets.get(ym, set())), 1), 4) if spine_sets.get(ym) else None
            cov_rows[slug] = per
            entry = {"factor": slug, "cn_name": cn, "server_history_start": srv_start,
                     "materialized_start": months[0], "materialized_end": months[-1],
                     "months_done": len(months), "rows": len(df), "gate": gate}
        else:
            entry = {"factor": slug, "cn_name": cn, "server_history_start": srv_start,
                     "materialized": False, "gate": gate}
        (manifest["pending"] if slug in PENDING_FACTORS else manifest["factors"]).append(entry)
    pd.DataFrame([{"factor": s, "ym": y, "coverage": c}
                  for s, per in cov_rows.items() for y, c in per.items()]
                 ).to_csv(STORE / "coverage.csv", index=False)
    json.dump(manifest, open(STORE / "manifest.json", "w"), ensure_ascii=False, indent=1)
    log(f"manifest+coverage 重建: {len(manifest['factors'])} 因子物化, "
        f"{len(manifest['pending'])} pending")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backfill", "monthly", "pending", "coverage"])
    ap.add_argument("--start", default=WINDOW_START)
    args = ap.parse_args()
    STORE.mkdir(parents=True, exist_ok=True)
    htsc = HTSC(load_env())
    if args.cmd == "backfill":
        cmd_backfill(htsc, args.start)
    elif args.cmd == "monthly":
        cmd_monthly(htsc)
    elif args.cmd == "pending":
        cmd_pending(htsc)
    else:
        build_meta()
    log("done.")


if __name__ == "__main__":
    main()
