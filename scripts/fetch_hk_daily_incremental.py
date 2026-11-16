#!/usr/bin/env python3
"""HK 日线增量统一供给(00700/09988/02800/09618) — 2026-11-09 归拢版。

取代三条旧路:refresh_09988_daily.sh(ak+tushare兜底) / refresh_hk_tencent_batch.sh(ak批量) /
tushare hk_daily 直连(配额 5次/天,已全部迁出)。

主源: ifzq.gtimg.cn hkfqkline(腾讯公开,免费无认证,字段序 date,O,C,H,L,vol(股),_,_,amount(万HKD×1e4))
备源: westock data_kline fq=bfq(未复权;token 由 .env WESTOCK_*,失效自动 fskey 续期)
口径: 与 tushare hk_daily 同构(未复权 OHLC + vol 股 + amount HKD;2026-11-09 逐字段实测精确吻合)。
表: tencent_hk_daily.csv(00700) / _09988.csv(09988) / _batch.csv(00700+02800+09618,GVRM sync 消费)。
规则: 只追加新完成交易日棒(HK 16:30 前不收当日);幂等 (ts_code,trade_date) keep=last 升序;
守门: 单标的近 25 棒取回 <10 根或最晚日期落后表内 max 即报警退出(防上游静默变脸)。
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

HK_TZ = timezone(timedelta(hours=8))
IFZQ = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
TS_CODE = {"hk00700": "00700.HK", "hk09988": "09988.HK",
           "hk02800": "02800.HK", "hk09618": "09618.HK"}
FILE_MAP = {
    "overseas/tencent_hk_daily.csv": ["hk00700"],
    "overseas/tencent_hk_daily_09988.csv": ["hk09988"],
    "overseas/tencent_hk_daily_batch.csv": ["hk00700", "hk02800", "hk09618"],
}


def ifzq_bars(code: str, n=25):
    r = requests.get(f"{IFZQ}?param={code},day,,,{n},qfq",
                     headers={"Referer": "https://gu.qq.com/"}, timeout=20).json()
    node = r.get("data", {}).get(code, {})
    key = next((k for k, v in node.items() if k.endswith("day") and isinstance(v, list)), None)
    rows = node.get(key) or []
    out = []
    for b in rows:
        out.append({"date": b[0], "open": float(b[1]), "close": float(b[2]),
                    "high": float(b[3]), "low": float(b[4]), "vol": float(b[5]),
                    "amount": float(b[8]) * 10000 if len(b) > 8 and b[8] not in ("", None) else None})
    return out


def westock_bars(code: str, n=25):
    tok = ""
    for line in open("/data/fmdata/.env"):
        if line.startswith("WESTOCK_ACCESS_TOKEN="):
            tok = line.strip().split("=", 1)[1]
    if not tok:
        raise RuntimeError("WESTOCK_ACCESS_TOKEN 未配置")
    r = requests.post("https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp",
                      headers={"Authorization": f"Bearer {tok}",
                               "Content-Type": "application/json",
                               "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "data_kline",
                                       "arguments": {"code": code, "period": "day",
                                                     "limit": n, "fq": "bfq"}}}, timeout=60)
    for line in r.content.decode("utf-8").split("\n"):
        if line.startswith("data:"):
            res = json.loads(line[5:])
            txt = "".join(c.get("text", "") for c in res.get("result", {}).get("content", []))
            nodes = json.loads(txt).get("data", {}).get("nodes", [])
            return [{"date": n_["date"], "open": n_["open"], "close": n_["last"],
                     "high": n_["high"], "low": n_["low"], "vol": n_["volume"],
                     "amount": n_["amount"]} for n_ in nodes]
    raise RuntimeError("westock 无 data: 行")


def fetch_bars(code: str):
    try:
        return "ifzq", ifzq_bars(code)
    except Exception as e:
        print(f"WARN ifzq 失败({e}),切 westock", file=sys.stderr)
        return "westock", westock_bars(code)


def process_file(store: Path, rel: str, symbols: list, today: str, allow_today: bool, dry_run: bool = False):
    path = store / rel
    df = pd.read_csv(path, dtype={"ts_code": str})
    appended, skipped = [], []
    for sym in symbols:
        ts_code = TS_CODE[sym]
        sub = df[df["ts_code"] == ts_code]
        if not len(sub):
            raise RuntimeError(f"{rel} 缺 {ts_code} 段")
        last_done = str(int(sub["trade_date"].max()))
        last_iso = f"{last_done[:4]}-{last_done[4:6]}-{last_done[6:]}"
        src, bars = fetch_bars(sym)
        bars = sorted(bars, key=lambda b: b["date"])
        if len(bars) < 10:
            raise RuntimeError(f"{ts_code} 仅取回 {len(bars)} 棒,上游疑似变脸,拒写")
        if bars[-1]["date"] < last_iso:
            raise RuntimeError(f"{ts_code} 源端最晚 {bars[-1]['date']} 落后表内 {last_iso},拒写")
        new = [b for b in bars if b["date"] > last_iso and (allow_today or b["date"] < today)]
        prev_close = None
        older = [b for b in bars if b["date"] <= last_iso]
        if older:
            prev_close = older[-1]["close"]
        if prev_close is None:
            prev_close = float(sub[sub["trade_date"] == int(last_done)]["close"].iloc[0])
        rows = []
        for b in new:
            pc = prev_close if prev_close is not None else b["open"]
            chg = b["close"] - pc
            rows.append({"ts_code": ts_code, "trade_date": int(b["date"].replace("-", "")),
                         "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"],
                         "pre_close": pc, "change": round(chg, 3),
                         "pct_chg": round(chg / pc * 100, 2) if pc else None,
                         "vol": b["vol"], "amount": b["amount"]})
            prev_close = b["close"]
        appended += rows
        skipped += [b["date"] for b in bars if b["date"] >= today and not allow_today]
    if appended and not dry_run:
        df = pd.concat([df, pd.DataFrame(appended)], ignore_index=True)
        df = df.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        tmp = path.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        tmp.rename(path)
    return {"file": rel, "appended": len(appended),
            "new": [f"{r['ts_code']}:{r['trade_date']}" for r in appended],
            "intraday_skipped": sorted(set(skipped))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔过滤,默认全部")
    ap.add_argument("--files", default="", help="按文件名子串过滤,默认全部")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--store-dir", default="/home/ubuntu/fmdata/store")
    args = ap.parse_args()
    store = Path(args.store_dir)
    now_hk = datetime.now(HK_TZ)
    today = now_hk.strftime("%Y-%m-%d")
    allow_today = now_hk.hour > 16 or (now_hk.hour == 16 and now_hk.minute >= 30)
    want = set(args.symbols.split(",")) if args.symbols else None
    files = args.files.split(",") if args.files else None
    summary = []
    for rel, symbols in FILE_MAP.items():
        if files and not any(f in rel for f in files):
            continue
        use = [s for s in symbols if not want or s in want]
        if not use:
            continue
        res = process_file(store, rel, use, today, allow_today, args.dry_run)
        if args.dry_run:
            res["appended_would"] = res.pop("appended")
        summary.append(res)
    print(json.dumps(summary, ensure_ascii=False))
    if args.dry_run:
        print("(dry-run,未写盘)")


if __name__ == "__main__":
    main()
