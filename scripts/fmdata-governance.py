#!/usr/bin/env python3
"""
fmdata 数据治理脚本 — 扫描、归档、合并散落数据
===============================================
每日运行（cron 03:15），完成以下工作：

Phase 1: 扫描散落数据（SZ81 + HK43）
  - 搜索常见目录中的 CSV/Parquet/Pickle 金融数据文件
  - 对比 fmdata 已有数据集，标记：重复/可归档/孤立

Phase 2: 归档散落数据
  - 重复数据 → 删除或软链接到 fmdata
  - 新数据  → 注册 recipe 或复制到 fmdata/store/

Phase 3: 刷新已有 recipe（按 update_freq 分频）
  - daily   每夜刷
  - weekly  仅周末刷（Sat/Sun）
  - monthly 仅月初 1-3 日刷
  - --refresh-all 忽略分频强制全刷（手动补救）

Phase 4: 报告
  - 输出治理摘要

用法：
  python3 fmdata-governance.py [--scan-only] [--dry-run] [--merge] [--refresh-all] [--host sz81|hk43|both]
"""

import argparse
import csv
import hashlib
import json
import os
import pickle
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 后台/cron 运行时 stdout 是全缓冲(block-buffered), output 长时间不更新看着"卡死"
# (实则在跑 Phase3 /fetch 慢 recipe)。reconfigure 行缓冲让日志实时 flush, 便于
# journalctl/tail 观察真实进度, 避免误判"卡死"。2026-06-16。
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

FMDATA_URL = "http://127.0.0.1:1934"
HOME = os.path.expanduser("~")

# Phase3 POST /fetch 的 socket 超时（秒）。
# 原来写死 curl_json 默认 30s，但实测有的 recipe 真实抓取远超 30s：
#   tech_signals 277s（tushare 全市场 + pandas_ta 逐股算 RSI/MACD/KDJ/布林/ATR）
#   china_bond_index 37s（akshare bond_zh_cov 经 QG 代理）
# 30s 把它们砍断 → 每夜必报 timeout（fmdata 侧实际能成功）。660s = tech_signals
# 声明 fetch.timeout 600s 上限 + 60s 余量（fmdata 按其 fetch.timeout 杀子进程并返回，
# 治理 socket 留余量确保收到 fmdata 的响应而非自己抢先超时）。fast recipe 不受影响
# （urllib 在 fmdata 返回时即返回，不会傻等满 660s）。
# 注意：此超时治不了 RISK-1 类失败——sw_main_flow_net 东财 push2 端点经 QG 代理必
# RemoteDisconnected，7s 秒退返伪 ok（陈旧数据），bump 超时无效，需换源（单独排查）。
FETCH_HTTP_TIMEOUT = 660

# 2026-11-13: 以下数据集的 /fetch 通路结构性不可用，但均有专属直连 cron 在刷新数据 →
# 治理跳过、不计失败（此前每夜固定 14 条 502 噪音，数据其实一直是新鲜的）：
#   - source=soochow_mcp 的全部 recipe：fmdata 无该 source 的 /fetch 分发分支
#     （curl 实测 "unknown source: soochow_mcp"）；专属 cron 07:40/13:40/19:15 直跑
#     fetch_soochow_mcp.py。注意 sw_main_flow_pct 不在此列——它没有专属 cron，
#     治理就是它唯一的刷新路径（夜间偶发 QG 502 靠下方单次重试兜住）。
#   - sw_tech_screen：/fetch 报 "未知 sw_mcp func: screen_technical"；cron 19:05 直跑脚本。
#   - ashare_index_brief：/fetch 报 "参数校验失败"；cron 07:45/17:05 直跑脚本。
#   - us_equity_kline_em：/fetch 触发脚本 rc=2（部分标的失败）被判 502；cron 06:15/15:25
#     直跑脚本且失败自带告警，治理重复尝试只会重复报错。
#   - cboe_iv_surface（2026-11-13 加）：/fetch 本身能通，但治理的 03:15/19:15 时点落在
#     美股盘中/次日盘前，快照 raw+累计表是"后写胜出"——治理先写会污染当天收盘快照
#     （19:15 CST=11:15 UTC 归属前一交易日，盘前漂移值会覆盖收盘值）。日度快照改由
#     专属 cron 06:07 CST(周二~周六, 收盘后 1h) 唯一写入。
#   - moomoo_iv_history（2026-11-13 加）：生产链整条在 HK43(OpenD 网关本机取数+scp 同步
#     CSV 过来)，SZ81 无 moomoo_api 包，/fetch 结构性不可用；HK43 cron 06:50(周二~周六)
#     唯一写入。
EXTERNALLY_REFRESHED_BY_CRON = {
    "sw_tech_screen",
    "ashare_index_brief",
    "us_equity_kline_em",
    "cboe_iv_surface",
    "moomoo_iv_history",
    # 2026-11-15: gfclaw 数仓由 /data/gfclaw/scripts/daily_sync.sh (cron 23:10) 供给，
    # 治理刷它会触发 26G parquet 全量 sha256 重扫（分钟级浪费），且其锁包装会拒绝并发
    "gfclaw_warehouse",
}


def _load_fmdata_env():
    """加载 ~/fmdata/.env 到 os.environ（不覆盖已有值）。

    为什么需要：治理脚本由 cc-connect cron（fe1b89d7，session_mode=new_per_run）
    每夜 03:15 触发，那是一个全新 agent session 的干净 shell——不继承 ~/.bashrc、
    不 source ~/fmdata/.env，所以 FMDATA_ADMIN_KEY 在 os.environ 里根本不存在。
    而 POST /fetch 对 source:agent / source:remote recipe 强制鉴权，没 key → HTTP 403
    （2026-06-15 首跑实测 23 个 recipe 每夜失败）。故脚本必须自己把 .env 里的 key 读进来。
    """
    env_path = os.path.join(HOME, "fmdata", ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:   # 不覆盖 agent shell 可能已注入的同名 env
                    os.environ[k] = v
    except Exception:
        pass  # .env 读失败不致命：只读端点(/status /recipes)无需 key，最坏退化到旧 403 行为


_load_fmdata_env()   # 模块加载即注入 FMDATA_ADMIN_KEY（cc-connect new_per_run shell 不 source .env）

# ─────────────────────────────────────────────────────────
# 散落数据扫描配置
# ─────────────────────────────────────────────────────────

# 要扫描的目录（相对于 HOME）
SCAN_DIRS = [
    "claude-workspace/data",
    "claude-workspace/industry_rotation",
    "data",
    "Downloads",
]

# 要扫描的根目录级文件模式
ROOT_FILE_PATTERNS = [
    "*.pkl", "*.pickle", "*.csv",
]

# 排除目录
EXCLUDE_DIRS = {
        "fmdata",           # fmdata 自身
        ".cache",           # 缓存
        ".local",           # 本地安装
        ".npm",             # npm
        ".claude",          # claude 配置
        "node_modules",     # node
        ".git",             # git
        "__pycache__",      # python cache
        ".config",          # 系统配置
        ".vscode-server",   # vscode
}

# ─────────────────────────────────────────────────────────
# 已知散落数据 → fmdata 数据集映射
# ─────────────────────────────────────────────────────────

# 如果文件名/路径包含 key，说明它是 fmdata 某 dataset 的散落副本
KNOWN_OVERLAPS = {
    # ~/claude-workspace/data/macro/ 散落副本
    "data/macro/cpi.csv": "cpi",
    "data/macro/ppi.csv": "ppi",
    "data/macro/pmi.csv": "pmi",
    "data/macro/macro_monthly.csv": "macro_monthly",
    "data/macro/macro_state.csv": "macro_state",
    "data/macro/north_money.csv": "north_money",
    "data/macro/money_supply.csv": "macro_monthly",  # 子集
    "data/macro/credit.csv": "macro_monthly",         # 子集
    "data/macro/lpr.csv": "macro_monthly",            # 子集
    "data/macro/shibor.csv": "macro_monthly",         # 子集

    # ~/claude-workspace/data/sw_industry/ 散落副本
    "data/sw_industry/sw_first_level_close.csv": "sw_first_level_close",
    "data/sw_industry/sw_first_level_amount.csv": "sw_first_level_amount",
    "data/sw_industry/sw_pe_history.csv": "sw_pe_history",
    "data/sw_industry/sw_pb_history.csv": "sw_pb_history",
    "data/sw_industry/hs300_daily.csv": "hs300_daily",
    "data/sw_industry/hs300_monthly.csv": "hs300_monthly",
    "data/sw_industry/sw_main_flow_net.csv": "sw_main_flow_net",
    "data/sw_industry/sw_main_flow_pct.csv": "sw_main_flow_pct",
    "data/sw_industry/extended_industry_median.csv": "extended_industry_median",
    "data/sw_industry/sw_fundamentals_v10.csv": "sw_fundamentals_v10",
    "data/sw_industry/sw_fundamentals_v9.csv": "sw_fundamentals_v9",
    "data/sw_industry/sw_extended_industry.csv": "extended_industry_median",
    "data/sw_industry/sw_industry_fundamentals.csv": "sw_fundamentals_v10",
    "data/sw_industry/sw_prosperity_change.csv": None,  # 无对应，可归档

    # 根目录 pickle（回测中间产物，保留但标记）
    "ret_matrix.pkl": None,        # 策略中间产物，非数据集
    "close_matrix.pkl": None,      # 策略中间产物
    "stock_to_sw.pkl": None,       # 映射字典
    "sw_ret_df.pkl": None,         # 策略中间产物
    "hs300_ret.pkl": None,         # 策略中间产物
    "stock_info.pkl": None,        # stock_list 子集
    "mgmt_events.pkl": None,       # 增持事件，可考虑注册

    # workspace 根目录
    "fund_manager_ranking.csv": None,  # 非标准格式，可归档

    # ETF 数据 — 行业轮动策略专用
    "industry_rotation/etf_data/": "etf_data_collection",
}

# 金融数据特征列名（用于自动识别散落的金融数据）
FINANCIAL_COLUMNS = {
    "ts_code", "trade_date", "close", "open", "high", "low", "vol", "amount",
    "pe", "pb", "ps", "dv_ratio", "total_mv", "circ_mv",
    "code", "date", "price", "volume", "turnover",
    "eps", "roe", "net_profit", "revenue", "total_assets",
    "industry", "name", "ann_date", "end_date", "report_date",
    "score", "rank", "yield", "return", "nav",
}


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def curl_json(url, method="GET", data=None, timeout=30):
    """HTTP 请求 fmdata API

    带 X-API-Key：POST /fetch 对 source:agent / source:remote recipe 强制鉴权，
    缺 key 返回 403。GET 端点(/status /recipes)无需 key，带了也无害。
    """
    import urllib.request
    api_key = os.environ.get("FMDATA_ADMIN_KEY", "")
    try:
        if method == "GET":
            req = urllib.request.Request(url)
        else:
            body = json.dumps(data).encode() if data else b""
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Content-Type", "application/json")
        if api_key:
            req.add_header("X-API-Key", api_key)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_fmdata_status():
    """获取 fmdata 所有数据集状态"""
    status = curl_json(f"{FMDATA_URL}/status?summary=true")
    if "error" in status:
        log(f"fmdata 不可用: {status['error']}", "ERROR")
        return None
    return status


def get_fmdata_recipes():
    """获取所有已注册 recipe"""
    return curl_json(f"{FMDATA_URL}/recipes")


# ─────────────────────────────────────────────────────────
# Phase 1: 散落数据扫描
# ─────────────────────────────────────────────────────────

def is_financial_csv(filepath):
    """判断 CSV 是否包含金融数据"""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            header_lower = {h.lower().strip() for h in header}
            overlap = header_lower & FINANCIAL_COLUMNS
            return len(overlap) >= 2
    except Exception:
        return False


def is_financial_pickle(filepath):
    """判断 pickle 是否是金融 DataFrame"""
    try:
        with open(filepath, "rb") as f:
            obj = pickle.load(f)
        if hasattr(obj, "columns"):
            cols = {str(c).lower().strip() for c in obj.columns[:20]}
            overlap = cols & FINANCIAL_COLUMNS
            return len(overlap) >= 2
        return False
    except Exception:
        return False


def scan_stray_data(host="sz81"):
    """扫描散落数据文件，返回分类结果"""
    results = {
        "overlaps": [],     # 与 fmdata 重复
        "new_data": [],     # 可能的新数据集
        "intermediate": [], # 中间产物（pickle等）
        "ignored": [],      # 非金融数据
    }

    log(f"--- Phase 1: 扫描散落数据 ({host}) ---")

    # 1. 扫描已知目录中的 CSV
    for scan_dir in SCAN_DIRS:
        full_dir = os.path.join(HOME, scan_dir)
        if not os.path.isdir(full_dir):
            continue

        for root, dirs, files in os.walk(full_dir):
            # 排除目录
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

            for fname in files:
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, HOME)

                if not fname.endswith((".csv", ".parquet")):
                    continue

                # 检查是否是已知重叠
                matched_dataset = None
                for pattern, dataset in KNOWN_OVERLAPS.items():
                    if pattern in rel_path:
                        matched_dataset = dataset
                        break

                if matched_dataset:
                    fsize = os.path.getsize(fpath)
                    results["overlaps"].append({
                        "file": rel_path,
                        "size_kb": fsize // 1024,
                        "fmdata_dataset": matched_dataset,
                    })
                elif is_financial_csv(fpath):
                    fsize = os.path.getsize(fpath)
                    # 读行数
                    try:
                        with open(fpath) as f:
                            rows = sum(1 for _ in f) - 1
                    except:
                        rows = "?"
                    results["new_data"].append({
                        "file": rel_path,
                        "size_kb": fsize // 1024,
                        "rows": rows,
                    })
                else:
                    results["ignored"].append(rel_path)

    # 2. 扫描根目录 pickle 和 CSV
    for pattern in ROOT_FILE_PATTERNS:
        import glob
        for fpath in glob.glob(os.path.join(HOME, pattern)):
            rel_path = os.path.relpath(fpath, HOME)

            # 检查已知映射
            matched_dataset = KNOWN_OVERLAPS.get(rel_path)
            if matched_dataset:
                results["overlaps"].append({
                    "file": rel_path,
                    "size_kb": os.path.getsize(fpath) // 1024,
                    "fmdata_dataset": matched_dataset,
                })
            elif fpath.endswith((".pkl", ".pickle")):
                if is_financial_pickle(fpath):
                    results["intermediate"].append({
                        "file": rel_path,
                        "size_kb": os.path.getsize(fpath) // 1024,
                    })
                else:
                    results["ignored"].append(rel_path)
            elif is_financial_csv(fpath):
                results["new_data"].append({
                    "file": rel_path,
                    "size_kb": os.path.getsize(fpath) // 1024,
                })

    # 3. 扫描 HK43（如果指定）
    if host in ("hk43", "both"):
        results["hk43"] = scan_remote_host("hk43")

    return results


def scan_remote_host(hostname):
    """扫描远程主机的散落数据"""
    log(f"  扫描远程主机 {hostname}...")
    remote_result = {"overlaps": [], "new_data": [], "intermediate": []}

    try:
        # 扫描常见目录
        for scan_dir in SCAN_DIRS:
            cmd = f'find ~/{scan_dir} -maxdepth 3 -type f \\( -name "*.csv" -o -name "*.pkl" \\) 2>/dev/null'
            result = subprocess.run(
                ["ssh", hostname, cmd],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                rel = line.replace("/home/ubuntu/", "")
                fsize_cmd = f'stat -c %s "{line}" 2>/dev/null'
                size_result = subprocess.run(
                    ["ssh", hostname, fsize_cmd],
                    capture_output=True, text=True, timeout=10
                )
                fsize = int(size_result.stdout.strip()) // 1024 if size_result.stdout.strip() else 0

                # 检查已知映射
                matched = None
                for pattern, dataset in KNOWN_OVERLAPS.items():
                    if pattern in rel:
                        matched = dataset
                        break

                if matched:
                    remote_result["overlaps"].append({
                        "file": rel, "size_kb": fsize,
                        "fmdata_dataset": matched,
                    })
                else:
                    remote_result["new_data"].append({
                        "file": rel, "size_kb": fsize,
                    })
    except subprocess.TimeoutExpired:
        log(f"  SSH 到 {hostname} 超时", "WARN")
    except Exception as e:
        log(f"  扫描 {hostname} 失败: {e}", "WARN")

    return remote_result


# ─────────────────────────────────────────────────────────
# Phase 2: 归档散落数据
# ─────────────────────────────────────────────────────────

def _fmdata_symlink_targets():
    """扫描 fmdata/store 下所有 symlink，返回它们指向的真实路径集合。

    散落数据文件有时本身就是 fmdata 数据源的本体（fmdata/store/xxx.csv 是
    指向它的 symlink）。删除这类散落文件 = 悬空 fmdata 数据源，fetch 会通过
    symlink 又写回 → 删除循环（2026-06-22 hs300_daily 实证）。archive 前调此函数
    拿到受保护路径集合，命中则跳过删除。
    """
    protected = set()
    store_root = os.path.join(HOME, "fmdata", "store")
    if not os.path.isdir(store_root):
        return protected
    for root, dirs, files in os.walk(store_root):
        for name in files + dirs:
            p = os.path.join(root, name)
            if os.path.islink(p):
                real = os.path.realpath(p)
                if os.path.exists(real):
                    protected.add(real)
    return protected


def archive_stray_data(scan_results, dry_run=False):
    """归档/清理散落数据"""
    log("--- Phase 2: 归档散落数据 ---")
    archived = 0
    freed_kb = 0

    # 受 symlink 保护的散落文件（删除会悬空 fmdata 数据源，见 _fmdata_symlink_targets）
    protected = _fmdata_symlink_targets()
    if protected:
        log(f"  🔒 {len(protected)} 个散落文件被 fmdata symlink 指向，删除将豁免")

    # 处理与 fmdata 重复的文件
    for item in scan_results.get("overlaps", []):
        filepath = os.path.join(HOME, item["file"])
        dataset = item["fmdata_dataset"]

        if not dataset:
            # 无对应数据集，标记为可归档
            log(f"  📦 可归档（无对应fmdata数据集）: {item['file']} ({item['size_kb']}KB)")
            continue

        if not os.path.exists(filepath):
            continue

        # 豁免：散落文件本身是 fmdata symlink 的目标 → 删除会悬空数据源，fetch 又写回
        if os.path.realpath(filepath) in protected:
            log(f"  🔒 跳过(symlink保护): {item['file']} 是 fmdata 数据源本体，删除会循环")
            continue

        # 检查 fmdata 中是否已有该数据
        status = get_fmdata_status()
        if status and dataset in status:
            fmdata_rows = status[dataset].get("rows", 0)
            fmdata_updated = status[dataset].get("last_updated", "")

            if fmdata_rows > 0:
                action = "DELETE" if not dry_run else "WOULD DELETE"
                log(f"  🗑️  {action}: {item['file']} (fmdata已有 {dataset}={fmdata_rows}行, 更新于{fmdata_updated})")
                if not dry_run:
                    os.remove(filepath)
                    archived += 1
                    freed_kb += item["size_kb"]
            else:
                log(f"  ⚠️  fmdata 数据集 {dataset} 为空，保留散落文件: {item['file']}")
        else:
            log(f"  📋 fmdata 中无 {dataset}，考虑注册 recipe: {item['file']}")

    # 处理可能的新数据
    for item in scan_results.get("new_data", []):
        log(f"  🆕 发现潜在新数据集: {item['file']} ({item.get('rows', '?')}行, {item['size_kb']}KB)")
        # 不自动注册，只是报告

    # 处理中间产物
    for item in scan_results.get("intermediate", []):
        age_days = 999
        filepath = os.path.join(HOME, item["file"])
        if os.path.exists(filepath):
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            age_days = (datetime.now() - mtime).days

        if age_days > 30:
            action = "STALE" if not dry_run else "WOULD MARK STALE"
            log(f"  ⏰ {action}: {item['file']} ({age_days}天未更新, {item['size_kb']}KB)")
        else:
            log(f"  📎 中间产物（{age_days}天前）: {item['file']}")

    log(f"  归档完成: {archived} 文件, 释放 {freed_kb}KB")
    return archived, freed_kb


# ─────────────────────────────────────────────────────────
# Phase 3: 刷新已有 Recipe
# ─────────────────────────────────────────────────────────

def refresh_recipes(force=False):
    """刷新需要更新的 recipe（按 update_freq 分频，避免 weekly/monthly 每夜重复重抓）

    force=True 忽略分频，刷新全部 daily/weekly/monthly（手动补救/排查用，=67 个）。
    nightly cron 跑 `--merge`（不带 --refresh-all）→ force=False → 分频：
      daily   每夜
      weekly  仅周末（weekday Sat/Sun，A股周五收市后周六晨数据最全；两日窗口容错单夜失败）
      monthly 仅月初 1-3 日（三日窗口容错）
    """
    log("--- Phase 3: 刷新 Recipe（分频）---" if not force
        else "--- Phase 3: 刷新 Recipe（全量 --refresh-all）---")

    recipes = get_fmdata_recipes()
    if not recipes or "error" in recipes:
        log("无法获取 recipe 列表", "ERROR")
        return

    today = datetime.now().date()
    weekday = today.weekday()   # Mon=0 ... Sat=5, Sun=6
    dom = today.day
    log(f"  今日: {today} (weekday={weekday}, dom={dom})")

    refreshed = 0
    failed = 0
    skipped_freq = 0   # 因频率未到而跳过（非 daily，且不在其时间窗口内）
    skipped_external = 0   # 专属 cron 维护、/fetch 通路不通的（见 EXTERNALLY_REFRESHED_BY_CRON）

    for name, info in recipes.items():
        # update_freq is a TOP-LEVEL field (server.py /recipes serves it at top level,
        # registry.py reads recipe.get("update_freq")). The old code read
        # fetch.update_freq, which /recipes never serves -> every recipe was skipped.
        freq = info.get("update_freq", "")
        if freq not in ("daily", "weekly", "monthly"):
            continue

        # 专属 cron 维护的放最前判断（不受分频门影响，避免每周/每月窗口期漏跳再报错）
        if info.get("source") == "soochow_mcp" or name in EXTERNALLY_REFRESHED_BY_CRON:
            skipped_external += 1
            continue

        # 分频 gate：减少 weekly/monthly 的重复拉取（force 时全刷）
        if not force:
            if freq == "weekly" and weekday not in (5, 6):
                skipped_freq += 1
                continue
            if freq == "monthly" and dom not in (1, 2, 3):
                skipped_freq += 1
                continue

        result = curl_json(f"{FMDATA_URL}/fetch/{name}", method="POST", timeout=FETCH_HTTP_TIMEOUT)
        if "error" in result:
            # 2026-11-13: 夜间 QG 代理偶发 502 会造成秒退假失败（sw_main_flow_pct 03:42
            # 实测失败、白天同参数成功）→ 单次重试再定生死
            time.sleep(5)
            result = curl_json(f"{FMDATA_URL}/fetch/{name}", method="POST", timeout=FETCH_HTTP_TIMEOUT)
        if "error" in result:
            log(f"  ❌ {name}: {result['error']}", "WARN")
            failed += 1
        else:
            rows = result.get("rows", "?")
            log(f"  ✅ {name} ({freq}): {rows} rows")
            refreshed += 1

    log(f"  刷新完成: {refreshed} OK, {failed} failed, {skipped_freq} 跳过(频率未到), "
        f"{skipped_external} 跳过(专属cron维护)")


# ─────────────────────────────────────────────────────────
# Phase 4: 报告
# ─────────────────────────────────────────────────────────

def generate_report(scan_results, archived, freed_kb):
    """生成治理报告"""
    log("--- Phase 4: 治理报告 ---")

    n_overlaps = len(scan_results.get("overlaps", []))
    n_new = len(scan_results.get("new_data", []))
    n_intermediate = len(scan_results.get("intermediate", []))
    n_ignored = len(scan_results.get("ignored", []))

    log(f"  扫描结果: {n_overlaps} 重复, {n_new} 新数据, {n_intermediate} 中间产物, {n_ignored} 忽略")
    log(f"  归档: {archived} 文件删除, 释放 {freed_kb}KB")

    if scan_results.get("hk43"):
        hk = scan_results["hk43"]
        log(f"  HK43: {len(hk.get('overlaps', []))} 重复, {len(hk.get('new_data', []))} 新数据")

    # 数据集健康度
    status = get_fmdata_status()
    if status:
        total = len(status)
        with_recipe = sum(1 for v in status.values() if v.get("has_recipe"))
        stale = 0
        now = datetime.now()
        all_recipes = get_fmdata_recipes()  # hoist out of loop (was N HTTP calls per dataset)
        for name, info in status.items():
            freq = all_recipes.get(name, {}).get("update_freq", "")
            if freq in ("daily",):
                updated = info.get("last_updated")
                if updated:
                    try:
                        upd_dt = datetime.strptime(updated, "%Y-%m-%d")
                        if (now - upd_dt).days > 2:
                            stale += 1
                    except:
                        pass

        log(f"  数据集总览: {total} 总计, {with_recipe} 有recipe, {stale} stale")

    log("=== 数据治理完成 ===")


# ─────────────────────────────────────────────────────────
# Phase 4c: 因子库日检（2026-11-15，用户令：因子库对研究极重要每日必检）
# 日练使用路径（FactorStore 六源各抽1因子实查 + PIT围栏负向测试 + 锚包在场
# + factor_query HTTP 通路）——防"catalog 在、真查全挂"的假绿（2026-11-15
# etf_monthly_close 5 个月假绿教训：健康检查必须打真实取数路径）。
# ─────────────────────────────────────────────────────────

FACTOR_CATALOG_BASELINE = 410  # 419 基线留 9 容差，跌破=丢源


def audit_factor_library(send_alert=True):
    log("--- Phase 4c: 因子库日检 ---")
    issues = []
    rows = []

    # ① catalog 规模与源完整性
    try:
        r = curl_json(f"{FMDATA_URL}/data/factor_catalog")
        rows = r.get("data", []) or []
    except Exception as e:
        issues.append(f"factor_catalog 不可读: {str(e)[:80]}")
    srcs = {}
    for x in rows:
        srcs[x.get("source", "?")] = srcs.get(x.get("source", "?"), 0) + 1
    if rows and len(rows) < FACTOR_CATALOG_BASELINE:
        issues.append(f"factor_catalog 只剩 {len(rows)} 因子(基线419): {srcs}")
    if rows:
        log(f"  catalog: {len(rows)} 因子 {srcs}")

    # ② 六源各抽1因子实弹查（探针月=3个月前，避开 sw T+38 服务端滞后）
    ym_probe = (datetime.now() - timedelta(days=95)).strftime("%Y-%m")
    try:
        sys.path.insert(0, os.path.join(HOME, "fmdata"))
        from fmdata.factor_store import FactorStore
        fs = FactorStore()
        picks = {}
        for x in rows:
            s = x.get("source", "")
            fid = x.get("factor_id") or x.get("name")
            # 跳过 pending 快照因子(如 htsc.llm_fadt: catalog 有登记项但未物化,
            # FactorStore 按 design 抛 KeyError——探针查它会天天误报)
            if x.get("query_method", "").startswith("仅快照") or "pending" in str(x.get("category_cn", "")):
                continue
            if s not in picks and s in ("ts", "sw2", "cons", "cjpy", "htsc") and fid:
                picks[s] = fid
        for src, fid in sorted(picks.items()):
            try:
                if src in ("cjpy", "htsc"):
                    df = fs.cross_section_any(fid, ym_probe, allow_nonpit=True)
                else:
                    df = fs.cross_section_any(fid, ym_probe)
                n = 0 if df is None else len(df)
                has_val = df is not None and n > 0 and df["value"].notna().any()
                log(f"  {'✅' if has_val else '❌'} {fid} @{ym_probe}: {n} 行")
                if not has_val:
                    issues.append(f"{fid}@{ym_probe} 截面空/全NaN")
            except Exception as e:
                log(f"  ❌ {fid} @{ym_probe}: {str(e)[:80]}", "WARN")
                issues.append(f"{fid} 查询失败: {str(e)[:80]}")

        # ③ PIT 围栏负向测试：cjpy/htsc 不带 allow_nonpit 必须拒绝
        fence_fid = picks.get("cjpy") or picks.get("htsc")
        if fence_fid:
            try:
                fs.cross_section_any(fence_fid, ym_probe)
                issues.append(f"PIT围栏失效: {fence_fid} 未带 allow_nonpit 竟放行")
            except PermissionError:
                log(f"  ✅ PIT围栏: {fence_fid} 默认拒绝(预期)")
            except Exception:
                pass  # 其他异常（如该月无数据）不判定围栏本身
    except Exception as e:
        issues.append(f"FactorStore 不可用: {str(e)[:80]}")

    # ④ R26 黄金锚包在场（回测复现的冻结正本）
    golden = os.path.join(HOME, "claude-workspace", "data", "idxenh_r26_golden")
    if not os.path.isdir(golden) or not os.listdir(golden):
        issues.append(f"R26黄金锚包缺失/空: {golden}")

    # ⑤ factor_query HTTP 通路（registry 指针烂的老巢，GET 层即验）
    try:
        r = curl_json(f"{FMDATA_URL}/data/factor_query")
        if not r or r.get("error") or int(r.get("rows", 0) or 0) <= 0:
            issues.append(f"factor_query 通路异常(rows={r.get('rows') if isinstance(r, dict) else '?'})")
        else:
            log(f"  ✅ factor_query 通路: {r.get('rows')} rows")
    except Exception as e:
        issues.append(f"factor_query 通路异常: {str(e)[:80]}")

    log(f"  因子库日检: {len(issues)} 问题(探针月 {ym_probe})")
    if issues:
        for i in issues[:10]:
            log(f"  🔴 {i}", "WARN")
        if send_alert:
            try:
                detail = "\n".join(issues[:10])
                subprocess.run(
                    ["bash", os.path.join(HOME, ".openclaw", "scripts", "send_feishu_alert.sh"),
                     "fmdata因子库日检", f"🔴 {len(issues)} 项异常:\n{detail}", "warning"],
                    capture_output=True, timeout=60)
                log(f"  已发飞书告警({len(issues)} 问题)")
            except Exception as e:
                log(f"  飞书告警失败: {e}", "WARN")
    return issues
# 此前只数 recipe registry last_updated>2d 的个数：不报名单、不告警、
# 且 fetch 成功但 0 行的静默烂检测不到。现改为：
#   ① 全部 update_freq=daily 的 recipe（含专属 cron 喂的）按数据文件 mtime 判
#   ② 无 recipe 的脚本喂源（gfclaw 等）按成功心跳文件判
#   ③ stale 报名单 + 飞书告警
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Phase 4b: 日更数据新鲜度审计（2026-11-15 增强）
# 此前只数 recipe registry last_updated>2d 的个数：不报名单、不告警、
# 且 fetch 成功但 0 行的静默烂检测不到。现改为：
#   ① 全部 update_freq=daily 的 recipe（含专属 cron 喂的）按数据文件 mtime 判
#   ② 无 recipe 的脚本喂源（gfclaw 等）按成功心跳文件判
#   ③ stale 报名单 + 飞书告警
# ─────────────────────────────────────────────────────────

STALE_HOURS = 48  # 固定回退阈值（拿不到交易日历时用）

# 无 recipe、由专属 cron 脚本喂的数据源: (成功心跳文件, 最大容忍时龄 h)。
# 心跳由各自 wrapper 在 rc=0 时 touch；心跳缺失=比过期更严重（cron 断档）。
# 幂等 fetcher（无新数据不写盘）的族也放这里查"生产者心跳"而非数据文件 mtime
# （soochow senti/pool 实测：数据全有但 mtime 停在上一期，file-mtime 判假阳性）。
SCRIPT_FED_HEARTBEATS = {
    "gfclaw(COS数仓91表)": ("/data/gfclaw/logs/.last_success", 36),
    "soochow族(cron 19:15/07:40)": (os.path.join(HOME, "fmdata", "logs", "fetch_soochow.log"), 48),
}


def _freshness_floor_epoch():
    """日更数据 mtime 下限（epoch）：最近一个"已过刷新傍晚"的交易日的前一天 12:00。

    语义：交易日当晚（governance 19:15 / 各 cron 傍晚针）刷新过的文件必然晚于
    该下限。周中查：昨晚没刷→stale；周日查：周五晚的文件合规；周一凌晨查：仍以
    周五为准（今天盘前不该要求刷新）；周一晚 20 点后：切到今天，周一晚必刷。
    拿不到交易日历时回退 96h（宁漏报勿误报）。"""
    try:
        cal = curl_json(f"{FMDATA_URL}/reference/calendar")
        rows = cal.get("data") if isinstance(cal, dict) else cal
        today_s = datetime.now().strftime("%Y%m%d")
        opens = [str(r["cal_date"]) for r in rows
                 if str(r.get("is_open")) == "1" and str(r["cal_date"]) <= today_s]
        if not opens:
            raise ValueError("empty calendar")
        cand = opens[-1]
        # 今天是交易日且还没到傍晚刷新带 → 用上一个交易日（今天的数据今晚才该有）
        if cand == today_s and datetime.now().hour < 20 and len(opens) >= 2:
            cand = opens[-2]
        d = datetime.strptime(cand, "%Y%m%d")
        return (d - timedelta(hours=12)).timestamp()
    except Exception:
        return time.time() - 96 * 3600


def audit_daily_freshness(send_alert=True):
    """审计所有日更数据源的新鲜度，返回 stale 列表 [(name, age_hours_or_None)]。"""
    log("--- Phase 4b: 日更数据新鲜度审计 ---")
    recipes = get_fmdata_recipes()
    if not recipes or "error" in recipes:
        log("  无法获取 recipe 列表，跳过审计", "WARN")
        return []

    store = os.path.join(HOME, "fmdata", "store")
    now = time.time()
    floor = _freshness_floor_epoch()
    log(f"  新鲜度门槛: mtime ≥ {datetime.fromtimestamp(floor).strftime('%m-%d %H:%M')}"
        f"（交易日感知）")
    stale = []
    checked = 0
    # freq 真值在 /recipes（yaml 顶层 update_freq），file 真值在 /status——
    # 注意 /status?summary=true 不含 file，必须拉全量 /status（2026-11-15 实测）
    status = curl_json(f"{FMDATA_URL}/status")
    if not status or "error" in status:
        log("  无法获取全量 status，跳过审计", "WARN")
        return []
    no_file = 0
    for name, info in recipes.items():
        if info.get("update_freq") != "daily":
            continue
        # soochow 族走生产者心跳（见 SCRIPT_FED_HEARTBEATS 注释），跳过 file-mtime 判定
        if info.get("source") == "soochow_mcp":
            continue
        rel = status.get(name, {}).get("file", "")
        if not rel:
            no_file += 1
            continue
        path = os.path.join(store, rel)
        checked += 1
        if not os.path.exists(path):
            stale.append((name, None))  # 声称日更但文件都没有
            continue
        if os.path.getmtime(path) < floor:
            stale.append((name, (now - os.path.getmtime(path)) / 3600))

    for label, (hb, max_h) in SCRIPT_FED_HEARTBEATS.items():
        checked += 1
        if not os.path.exists(hb):
            stale.append((label + "(心跳缺失)", None))
            continue
        age_h = (now - os.path.getmtime(hb)) / 3600
        if age_h > max_h:
            stale.append((label + f"(心跳{age_h:.0f}h)", age_h))

    if stale:
        lines = [f"  🔴 stale: {n}" + (f" ({h:.0f}h 前更新)" if h else " (文件缺失)") for n, h in stale[:20]]
        if len(stale) > 20:
            lines.append(f"  ...及另外 {len(stale) - 20} 个")
        log("\n".join(lines), "WARN")
    log(f"  日更审计: {len(stale)} stale / {checked} 日更源检查(交易日感知门槛)"
        + (f", {no_file} 个daily recipe无file字段跳过" if no_file else ""))

    if stale and send_alert:
        detail = "\n".join(n + (f"({h:.0f}h)" if h else "(缺失)") for n, h in stale[:15])
        try:
            subprocess.run(
                ["bash", os.path.join(HOME, ".openclaw", "scripts", "send_feishu_alert.sh"),
                 "fmdata日更审计", f"🔴 {len(stale)} 个日更数据源过期(>{STALE_HOURS}h):\n{detail}", "warning"],
                capture_output=True, timeout=60)
            log(f"  已发飞书告警({len(stale)} stale)")
        except Exception as e:
            log(f"  飞书告警发送失败: {e}", "WARN")
    return stale


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Phase 2b: 自动合并散落数据到 fmdata
# ─────────────────────────────────────────────────────────

def merge_stray_to_fmdata(scan_results, dry_run=False):
    """把散落的新数据合并到 fmdata/store/ 并触发 registry 重建"""
    log("--- Phase 2b: 合并散落数据到 fmdata ---")
    merged_count = 0

    for item in scan_results.get("new_data", []):
        filepath = os.path.join(HOME, item["file"])
        if not os.path.exists(filepath):
            continue

        fname = os.path.basename(item["file"])
        name = os.path.splitext(fname)[0]

        # 判断应该归入哪个 category
        category = guess_category(item["file"])
        target_dir = os.path.join(HOME, "fmdata", "store", category)
        target_path = os.path.join(target_dir, fname)

        if os.path.exists(target_path):
            log(f"  ⏭️  已存在: {category}/{fname}")
            continue

        action = "MERGE" if not dry_run else "WOULD MERGE"
        log(f"  📥 {action}: {item['file']} → fmdata/store/{category}/{fname} ({item.get('rows', '?')}行)")

        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            import shutil
            shutil.copy2(filepath, target_path)
            merged_count += 1

    # 触发 registry 重建
    if merged_count > 0 and not dry_run:
        rebuild_registry()

    log(f"  合并完成: {merged_count} 个数据集")
    return merged_count


def guess_category(rel_path):
    """根据文件路径猜测 fmdata category"""
    path_lower = rel_path.lower()
    if any(k in path_lower for k in ["macro", "cpi", "ppi", "pmi", "m2", "money", "credit", "lpr", "shibor"]):
        return "macro"
    if any(k in path_lower for k in ["sw_industry", "sw_", "申万"]):
        return "market"
    if any(k in path_lower for k in ["consensus", "earnings", "report_rc", "cninfo", "analyst"]):
        return "fundamentals"
    if any(k in path_lower for k in ["fina", "财报", "fundamental"]):
        return "fundamentals"
    if any(k in path_lower for k in ["etf", "daily", "stock", "hs300", "close", "price"]):
        return "market"
    if any(k in path_lower for k in ["factor"]):
        return "factors"
    if any(k in path_lower for k in ["strategy", "rotation", "monthly_return"]):
        return "strategy"
    return "market"  # 默认


def rebuild_registry():
    """强制重建 fmdata registry"""
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "-c", "from fmdata.registry import init_registry_from_store; init_registry_from_store()"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log("  🔄 Registry 已重建")
        else:
            log(f"  ⚠️ Registry 重建失败: {result.stderr}", "WARN")
    except Exception as e:
        log(f"  ⚠️ Registry 重建异常: {e}", "WARN")


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="fmdata 数据治理")
    parser.add_argument("--scan-only", action="store_true", help="只扫描不归档")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行不实际删除")
    parser.add_argument("--merge", action="store_true", help="自动合并新数据到 fmdata")
    parser.add_argument("--refresh-all", action="store_true",
                        help="强制刷新全部 daily/weekly/monthly recipe（忽略分频，手动补救用）")
    parser.add_argument("--host", choices=["sz81", "hk43", "both"], default="both",
                        help="扫描的主机")
    parser.add_argument("--audit-only", action="store_true",
                        help="只跑 Phase 4b 日更新鲜度审计（不刷新不归档，手动巡检用）")
    args = parser.parse_args()

    # 检查 fmdata
    status = get_fmdata_status()
    if not status:
        log("fmdata 不可用，退出", "ERROR")
        sys.exit(1)

    log(f"fmdata 在线: {len(status)} 个数据集")

    # 只审计：不扫散落/不归档/不刷新，Phase 4b/4c 独立跑（手动巡检/验证告警链路）
    if args.audit_only:
        audit_daily_freshness()
        audit_factor_library()
        return

    # Phase 1: 扫描
    scan_results = scan_stray_data(host=args.host)

    # Phase 2: 归档（删除与 fmdata 重复的散落文件）
    archived, freed_kb = 0, 0
    if not args.scan_only:
        archived, freed_kb = archive_stray_data(scan_results, dry_run=args.dry_run)

    # Phase 2b: 合并新发现的散落数据到 fmdata
    merged = 0
    if args.merge and not args.scan_only:
        merged = merge_stray_to_fmdata(scan_results, dry_run=args.dry_run)

    # Phase 3: 刷新（nightly --merge 默认分频；--refresh-all 强制全刷）
    if not args.scan_only:
        refresh_recipes(force=args.refresh_all)

    # Phase 4: 报告
    generate_report(scan_results, archived, freed_kb)

    # Phase 4b: 日更新鲜度审计（stale 会列名单+飞书告警）
    audit_daily_freshness()

    # Phase 4c: 因子库日检（六源实弹+PIT围栏+锚包+factor_query通路）
    audit_factor_library()


if __name__ == "__main__":
    main()
