#!/usr/bin/env python3
"""经东财取港股总市值,按 HKD→USD 换算(给 OTC ADR 用,如 TCEHY→00700.HK 腾讯)。

用途: OTC 粉单 ADR(如 TCEHY)不在 stockanalysis/东财美股(105/106/107)/妙想覆盖范围,
但其母公司在港交所主上市,总市值与 ADR 等价(ADR 仅是份额表示)。
故: ADR 美元市值 = 港股母公司 HKD 总市值 ÷ USDHKD。

HK43 cache_market_caps.py Tier4 经 `ssh sz81 python3 fetch_hk_mcap_usd.py <codes>` 调用。
- secid = 116.{hkcode}(港股主板)
- 港股 f20(总市值)返 None,用 price(f43) × 总股本(f84) 算总市值(真值),f116(流通市值)兜底
- USDHKD=7.80(联系汇率中值,1983 起钉 7.75-7.85,误差<0.6%,T/B 显示无感)

用法:
  python3 fetch_hk_mcap_usd.py 00700          # argv
  echo "00700" | python3 fetch_hk_mcap_usd.py # stdin
  python3 fetch_hk_mcap_usd.py 00700,09988    # 逗号分隔多个
输出: stdout = JSON {"00700": "553.02B"}; stderr = 进度
"""
import sys, os, json
from pathlib import Path

_env = Path.home() / "fmdata" / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

sys.path.insert(0, str(Path.home() / "fmdata"))
from fmdata.recipe_fetcher import eastmoney_get  # noqa: E402

URL = "https://push2.eastmoney.com/api/qt/stock/get"
FIELDS = "f57,f58,f43,f84,f116"  # code,name,price,总股本,流通市值
USDHKD = 7.80  # 联系汇率中值(7.75-7.85 band),T/B 显示误差<0.6%


def fmt_usd(mc):
    if not isinstance(mc, (int, float)) or mc <= 0:
        return None
    if mc >= 1e12:
        return f"{mc / 1e12:.2f}T"
    if mc >= 1e9:
        return f"{mc / 1e9:.2f}B"
    if mc >= 1e6:
        return f"{mc / 1e6:.2f}M"
    return f"{mc:.0f}"


def fetch_hk_mcap_usd(codes):
    """codes: 港股代码列表(如 ['00700'])。返 {code: '553.02B'}。"""
    out = {}
    for code in codes:
        code = str(code).strip()
        secid = f"116.{code}"  # 港股 secid 带前导零(00700),勿 lstrip
        d = eastmoney_get(URL, {"secid": secid, "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                                "fltt": 2, "fields": FIELDS}, max_tries=4, timeout=8)
        da = (d or {}).get("data") or {}
        if not da:
            sys.stderr.write(f"[HK] {code}: 无数据(secid={secid})\n")
            continue
        price = da.get("f43")
        shares = da.get("f84")     # 总股本
        float_mc = da.get("f116")  # 流通市值(HKD)
        # 总市值 HKD = price × 总股本(真值); 缺总股本则用流通市值兜底
        if isinstance(price, (int, float)) and price > 0 and isinstance(shares, (int, float)) and shares > 0:
            mc_hkd = price * shares
        elif isinstance(float_mc, (int, float)) and float_mc > 0:
            mc_hkd = float_mc
        else:
            sys.stderr.write(f"[HK] {code}: {da.get('f58')} 无 price/股本/流通市值\n")
            continue
        mc_usd = mc_hkd / USDHKD
        s = fmt_usd(mc_usd)
        if s:
            out[code] = s
            sys.stderr.write(f"[HK] {code} {da.get('f58')}: HKD {mc_hkd/1e9:.1f}B / {USDHKD} = ${s}\n")
    return out


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
    codes = [c.strip() for c in arg.replace("\n", ",").split(",") if c.strip()]
    if not codes:
        print("{}")
        return
    out = fetch_hk_mcap_usd(codes)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
