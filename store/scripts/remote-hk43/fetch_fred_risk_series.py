#!/usr/bin/env python3
"""FRED 风险监测序列包 — 多序列长表抓取(跑在 hk43, recipe fred_risk_series 调用)。

输出 CSV 长表 series_id,date,value 到 stdout, 由 fmdata parser:fred 落盘
store/market/fred_risk_series.csv。

序列清单(2026-11-07 FRED API 逐个验活):
  BAMLH0A0HYM2  ICE BofA 高收益 OAS 利差(核心)   Percent  FRED 端仅近3年(ICE 授权滚动窗口)
  BAMLC0A0CM    ICE BofA 投资级 OAS              Percent  同上仅近3年
  SOFR          担保隔夜融资利率                  Percent  2018-04 起全史
  T10Y2Y        2s10s 期限利差                    Percent  全史
  T10Y3M        3M10Y 期限利差                    Percent  全史
  DGS2          2 年期国债收益率                  Percent  全史(与 us_10y DGS10 组成曲线)

不入包: VIXCLS(vix 数据集已有,仅交叉验证); TEDRATE(2022-01-21 停更,LIBOR 退场,
替代口径=SOFR 担保隔夜 + BAML OAS 信用利差, 无单一官方续作)。

单序列失败即 exit 1(fail-loud, 让 refresh-overseas/mtime-watchdog 报警, 不静默缺列)。
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.stlouisfed.org/fred"
SERIES = [
    "BAMLH0A0HYM2",
    "BAMLC0A0CM",
    "SOFR",
    "T10Y2Y",
    "T10Y3M",
    "DGS2",
    # 2026-11-08 监控二期补: 10Y 收益率/盈亏平衡通胀/金融条件
    # (DGS10 与 us_10y_treasury 同源同值, 入包统一走 macro_series 通道)
    "DGS10",
    "T10YIE",
    "NFCI",   # 周更·周五观测·约4-5天发布滞后, Index 单位非 Percent
]
TIMEOUT = 30
BACKOFF = [2, 5, 10]


def fetch_series(api_key: str, series_id: str, start: str, end: str | None) -> list[dict]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }
    if end:
        params["observation_end"] = end
    url = f"{BASE_URL}/series/observations?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(len(BACKOFF) + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fmdata-fred-risk/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode())
            if "error_message" in payload:
                raise RuntimeError(f"FRED API error: {payload['error_message']}")
            rows = [
                {"series_id": series_id, "date": ob["date"], "value": ob["value"]}
                for ob in payload.get("observations", [])
                if ob.get("value") not in (".", "")
            ]
            return rows
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < len(BACKOFF):
                time.sleep(BACKOFF[attempt])
    raise RuntimeError(f"{series_id}: {last_err}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-11-01", help="observation_start (default 2016-11-01, 近10年)")
    ap.add_argument("--end", default=None, help="observation_end (optional)")
    args = ap.parse_args()

    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise SystemExit("Missing FRED_API_KEY (source ~/.fred_env first)")

    writer = csv.writer(sys.stdout)
    writer.writerow(["series_id", "date", "value"])
    for sid in SERIES:
        rows = fetch_series(api_key, sid, args.start, args.end)
        if not rows:
            print(f"{sid}: no observations since {args.start}", file=sys.stderr)
            raise SystemExit(1)
        for r in rows:
            writer.writerow([r["series_id"], r["date"], r["value"]])
        print(f"{sid}: {len(rows)} rows", file=sys.stderr)


if __name__ == "__main__":
    main()
