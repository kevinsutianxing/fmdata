#!/usr/bin/env python3
"""ondemand recipe 周度活性探针（2026-11-16，用户令加，尽量 robust）。

背景: ondemand 类 recipe(按需触发)健康度=被调用时才知道——没人调就一直没体检。
周一度逐个打"最小真查询"，走 fmdata POST /fetch 真实使用路径（顺带验证
threadpool 修复后的 fetch 链路）。

robust 原则:
  ① curated 只探 8 个"活服务"(上游健康=服务可用性), 一次性快照/静态表/重活不探
  ② 每探独立超时+独立 try, 单探失败不拖垮其余
  ③ SW 探针必须带小窗口 override(其默认 fetch=3600s 全量物化, 探它=烧服务)
  ④ 失败明细进飞书告警, 全绿零输出静默
  ⑤ 近似只读: westock/mx/ts为只读点查; SW探针小窗口幂等合并进主表(keep=last去重,无净变更)

cron: 12 9 * * 1 (SZ81, flock), 失败飞书告警
用法: python3 probe_ondemand_recipes.py [--only westock_query]   # 排障单探
"""
import json
import os
import subprocess
import sys
import datetime as dt

FMDATA = "http://127.0.0.1:1934"
ENV_PATH = "/data/fmdata/.env"
LOG = f"/data/fmdata/logs/ondemand_probe_{dt.date.today():%Y%m%d}.log"

# (recipe, POST params override, curl超时s, 最小行数, 验证什么)
PROBES = [
    ("westock_query", {"tool": "data_quote", "codes": "sh600000"}, 60, 1,
     "腾讯自选股MCP桥+OAuth token(100h过期链)"),
    ("mx_query", None, 100, 1, "东财妙想MCP桥+EM_API_KEY(recipe默认=茅台最新价小查)"),
    ("sw_factor_series", {"factors": ["估值"], "ts_codes": ["600519.SH"],
                          "start": "2026-09-01", "end": "2026-09-30"}, 60, 1,
     "申万MCP远程服务(🔴小窗口override,默认fetch是3600s全量物化禁探)"),
    ("ts_factor_value", None, 130, 1, "tushare付费因子库按需通道(默认size@20260812=1次配额)"),
    ("tencent_hk_daily_ak", None, 60, 5, "HK43 remote通道+akshare直连腾讯(ak备源链)"),
    ("us_stock_spot", None, 180, 100, "QG代理池+东财美股快照(前科:12天静默失败)"),
    ("macro_gdp", None, 90, 8, "akshare经QG代理通道·代表①(东财宏观)"),
    ("macro_usdcny_boc", None, 90, 100, "akshare经QG代理通道·代表②(汇率BoC历史;2026-11-16修复fx_boc_rate_sina改名)"),
]


def _admin_key():
    for line in open(ENV_PATH):
        if line.startswith("FMDATA_ADMIN_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def probe(name, params, timeout_s, min_rows, what):
    body = json.dumps({"params": params}) if params else "{}"
    cmd = ["curl", "-s", "-m", str(timeout_s), "-w", "\n%{http_code}",
           "-X", "POST", f"{FMDATA}/fetch/{name}",
           "-H", "Content-Type: application/json",
           "-H", f"X-API-Key: {_admin_key()}", "-d", body]
    import time as _t
    last = ""
    for attempt in (1, 2):  # 网络类瞬断单次重试(20s退避), 二次仍败才判死
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=timeout_s + 15).stdout
            body_txt, _, code = out.rpartition("\n")
            if code == "200":
                r = json.loads(body_txt)
                rows = r.get("rows", 0) or 0
                if rows >= min_rows:
                    return True, f"rows={rows}" + (" (重试后过)" if attempt == 2 else "")
                last = f"rows={rows} < {min_rows}: {str(r)[:150]}"
            else:
                last = f"HTTP {code}: {body_txt[:150]}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if attempt == 1:
            _t.sleep(20)
    return False, last


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    lines = [f"=== ondemand probe {dt.datetime.now():%F %T} ==="]

    fails = []
    for name, params, tmo, min_rows, what in PROBES:
        if only and name != only:
            continue
        ok, detail = probe(name, params, tmo, min_rows, what)
        mark = "✅" if ok else "🔴"
        lines.append(f"{mark} {name}: {detail}  [{what}]")
        if not ok:
            fails.append((name, detail))

    lines.append(f"=== done: {len(fails)} fail / {len(PROBES) if not only else 1} probes ===")
    text = "\n".join(lines)
    print(text)
    with open(LOG, "a") as f:
        f.write(text + "\n")

    if fails:
        detail = "\n".join(f"- {n}: {d}" for n, d in fails)
        subprocess.run(
            ["bash", "/home/ubuntu/.openclaw/scripts/send_feishu_alert.sh",
             "fmdata ondemand周探",
             f"🔴 {len(fails)}/{len(PROBES)} 个按需源活性探针失败:\n{detail}\n日志: {LOG}",
             "warning"],
            capture_output=True, timeout=60)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
