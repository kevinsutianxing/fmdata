#!/usr/bin/env python3
"""Tier1: 抓美股实时行情总市值(USD) — 2026-11-17 主源切 westock data_kline 同族 data_quote(QG 止血)。

HK43 cache_market_caps.py 经 `ssh sz43 python3 fetch_us_mcap_em.py <syms>` 调用(契约不变)。
- 旧路: 东财 push2 clist 翻页 + QG 代理池(拒收率 ~96%, 日均烧 ~7 IP)
- 新路: westock data_quote 批量(官方认证 API, 零代理消耗), total_market_cap 单位=亿(USD)
  → ×1e8 还原为 USD 原始数, 走原 fmt_mcap 转 T/B/M 字符串
- 未命中标的照旧不输出 → HK43 Tier2 逐只兜底
用法:
  python3 fetch_us_mcap_em.py AAPL,MSFT,NVDA     # argv
  echo "AAPL,MSFT" | python3 fetch_us_mcap_em.py  # stdin
输出: stdout = JSON {symbol: "4.56T"}; stderr = 进度
旧版封存: fetch_us_mcap_em.py.bak.westock-switch-20261117
"""
import sys
import json
import time
import urllib.request

FMDATA = "http://127.0.0.1:1934"
CHUNK = 30          # 每批 quote 标的数
SLEEP = 1.5


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


def westock_quotes(codes, retries=1):
    """一批代码 → {SYM: mcap_usd_raw}。带 body 状态校验与一次重试(2026-11-17 实证:
    fmdata /fetch 会以 200+in-body error 快速失败,且 westock quote 会静默丢标的
    (如 usNVDA)——丢的按未命中处理交 Tier2)。"""
    import json as _json
    for attempt in range(retries + 1):
        try:
            wcodes = [f"us{c}" for c in codes]
            req = urllib.request.Request(
                f"{FMDATA}/fetch/westock_query",
                data=_json.dumps({"params": {"tool": "data_quote",
                                             "codes": ",".join(wcodes)}}).encode(),
                headers={"Content-Type": "application/json"})
            body = _json.loads(urllib.request.urlopen(req, timeout=90).read())
            if body.get("status") != "ok":
                raise RuntimeError(f"fetch status={body.get('status')}")
            with urllib.request.urlopen(f"{FMDATA}/data/westock_query", timeout=30) as r:
                d = _json.loads(r.read())
            break
        except Exception as e:
            if attempt >= retries:
                raise
            sys.stderr.write(f"[Tier1] 批次失败({str(e)[:50]}),5s 后重试\n")
            time.sleep(5)
    out = {}
    for row in d.get("data", []):
        ent = (row.get("entity") or "").replace("us", "", 1).upper()
        if row.get("key") == "total_market_cap":
            v = row.get("value")
            try:
                mc = float(v) * 1e8  # 亿 → USD 原始数
            except (TypeError, ValueError):
                continue
            if mc > 0:
                out[ent] = mc
    dropped = [c for c in codes if c.upper() not in out]
    if dropped:
        sys.stderr.write(f"[Tier1] 批内未命中({len(dropped)}): {','.join(dropped)} → Tier2\n")
    return out


def fetch_us_mcap(targets):
    targets = [s.upper() for s in targets if s]
    found = {}
    for i in range(0, len(targets), CHUNK):
        batch = targets[i:i + CHUNK]
        try:
            found.update(westock_quotes(batch))
        except Exception as e:
            sys.stderr.write(f"[Tier1] westock 批次失败({str(e)[:60]}),跳过\n")
        if i + CHUNK < len(targets):
            time.sleep(SLEEP)
    return {s: fmt_mcap(mc) for s, mc in found.items() if fmt_mcap(mc)}


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
