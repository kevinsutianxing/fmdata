#!/usr/bin/env python3
"""Tier1: 经 QG 代理池抓东财美股实时行情总市值(USD)。

HK43 cache_market_caps.py 经 `ssh sz43 python3 fetch_us_mcap_em.py <syms>` 调用。
- 走 recipe_fetcher.eastmoney_get(IP 轮换+重试,东财 push2 实时子域限速严必须换 IP)
- f20 = 总市值(USD 原始数,e.g. 4.563e12),转 T/B/M 字符串与 _parse_mcap_str 对齐
- clist 按市值降序翻页,取大盘主体;小盘尾(symbol 不在前 ~50 页或 mcap<$50M)交给 Tier2

东财 clist 东财硬顶 pz=100/页,按 fid=f20 降序翻页。

用法:
  python3 fetch_us_mcap_em.py AAPL,MSFT,NVDA     # argv
  echo "AAPL,MSFT" | python3 fetch_us_mcap_em.py  # stdin
输出: stdout = JSON {symbol: "4.56T"}; stderr = 进度
"""
import sys, os, json, time
from pathlib import Path

# 加载 fmdata .env (QG keys) — 脚本自包含,不依赖 caller 已 source
_env = Path.home() / "fmdata" / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

sys.path.insert(0, str(Path.home() / "fmdata"))
from fmdata.recipe_fetcher import eastmoney_get  # noqa: E402

URL = "https://72.push2.eastmoney.com/api/qt/clist/get"
PARAMS_BASE = {
    "po": 1, "np": 1, "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": 2, "invt": 2, "fid": "f20",
    "fs": "m:105,m:106,m:107",  # NYSE/NASDAQ/AMEX
    "fields": "f12,f13,f14,f20",
}
FLOOR_MC = 5e8  # $500M 以下不再翻(小盘尾交 Tier2 逐只取更划算)


def fmt_mcap(mc):
    """USD 原始数 → T/B/M/K 字符串。"""
    if not isinstance(mc, (int, float)) or mc <= 0:
        return None
    if mc >= 1e12:
        return f"{mc / 1e12:.2f}T"
    if mc >= 1e9:
        return f"{mc / 1e9:.2f}B"
    if mc >= 1e6:
        return f"{mc / 1e6:.2f}M"
    if mc >= 1e3:
        return f"{mc / 1e3:.2f}K"
    return f"{mc:.0f}"


def fetch_us_mcap(targets, max_pages=10, deadline_s=70):
    """经 QG 代理翻东财 clist 取美股总市值。

    push2 实时子域有死窗(东财侧限流,0% 穿不过,所有子域同时受影响,持续秒~分钟)。
    策略: page1 用 max_tries=3 探测,死窗快退(避免每页 25s 浪费)交 Tier2;
    活则续翻到 $500M floor 或 max_pages。活窗时单 IP 短期不封(实测连打3次 OK)。"""
    targets = {s.upper() for s in targets if s}
    found = {}
    t0 = time.time()
    for pn in range(1, max_pages + 1):
        if len(found) >= len(targets):
            break
        if time.time() - t0 > deadline_s:
            sys.stderr.write(f"[Tier1] deadline hit at page {pn}\n")
            break
        params = dict(PARAMS_BASE, pn=pn, pz=100)
        mt = 3 if pn == 1 else 6  # page1 探测死窗快退,后续 6 试跳坏 IP
        d = eastmoney_get(URL, params, max_tries=mt, timeout=8)
        if not d or not d.get("data") or not d["data"].get("diff"):
            if pn == 1:
                sys.stderr.write("[Tier1] page1 探测失败(死窗?),快退交 Tier2\n")
                return found
            sys.stderr.write(f"[Tier1] page {pn} 空,跳过\n")
            continue
        rows = d["data"]["diff"]
        if not rows:
            break
        last_mc = 0
        for r in rows:
            sym = (r.get("f12") or "").upper()
            mc = r.get("f20")
            if isinstance(mc, (int, float)) and mc > 0:
                last_mc = mc
            if sym in targets and sym not in found:
                s = fmt_mcap(mc)
                if s:
                    found[sym] = s
        if last_mc and last_mc < FLOOR_MC:
            sys.stderr.write(f"[Tier1] page {pn} 最低 ${last_mc/1e6:.0f}M < $500M,停止\n")
            break
    return found


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
    targets = [s.strip() for s in arg.replace("\n", ",").split(",") if s.strip()]
    if not targets:
        print("{}")
        return
    t0 = time.time()
    found = fetch_us_mcap(targets)
    sys.stderr.write(f"[Tier1] {len(found)}/{len(targets)} 命中, 耗时 {time.time()-t0:.1f}s\n")
    print(json.dumps(found, ensure_ascii=False))


if __name__ == "__main__":
    main()
