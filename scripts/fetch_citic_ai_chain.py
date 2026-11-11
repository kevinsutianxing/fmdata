#!/usr/bin/env python3
"""中信前瞻『海外AI产业链跟踪数据库』→ fmdata 落地 (2026-11-11 接入).

源站: https://h97m3ln5.qwenwork.host (机构订阅: 国寿安保基金; 登记机构+姓名即可)
数据端点: GET /api/chart-data (公开 JSON, ~350KB, 每日 ~11:49 更新)
内容: 155 组 AI 产业链序列(LLM token 价格/ARR/GPU 租赁/CDS/Z-Spread/期限结构等)
      + bonds(云厂商长债表现) + tables + commentary + generated_at

落地(SZ81 store/data/citic/):
  citic_ai_chain_raw.json   当日原始 JSON(全量存档, 按需再挖)
  citic_ai_credit.csv       信用块长表: date, kind, entity, tenor, value_bps, source
  citic_ai_bonds.csv        债券表现: asof, co, isin, maturity, coupon, ytd, latest, change
  citic_ai_chain_meta.csv   generated_at + 各块行数(新鲜度账)

礼节: 每日 1 次 GET(公开端点); 幂等=generated_at 当日已存即跳过。
用法: python3 fetch_citic_ai_chain.py
cron : 12:10 (源站 ~11:49 更新后), 失败飞书告警。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

CST = timezone(timedelta(hours=8))
STORE = Path("/data/fmdata/store/data/citic")
URL = "https://h97m3ln5.qwenwork.host/api/chart-data"

# 信用块映射: data键 → (kind, entity, tenor)
CREDIT_MAP = {}
for _n in ("amzn", "googl", "meta", "msft", "orcl", "crwv"):
    CREDIT_MAP[f"cds.{_n}"] = ("cds5y", _n.upper(), "5Y")
for _n in ("amzn", "googl", "meta", "msft"):
    CREDIT_MAP[f"credit.zsp_{_n}"] = ("zspread", _n.upper(), "")
for _n in ("orcl", "meta", "crwv"):
    for _t in ("1y", "3y", "5y", "10y"):
        CREDIT_MAP[f"credit.{_n}_{_t}"] = ("cds_cme", _n.upper(), _t.upper())
for _n in ("amzn", "googl", "meta", "msft", "orcl"):
    CREDIT_MAP[f"credit.cds5y_{_n}"] = ("cds_cme5y", _n.upper(), "5Y")


def now_cst():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"[{now_cst()}] {msg}", flush=True)


def main():
    STORE.mkdir(parents=True, exist_ok=True)
    r = requests.get(URL, timeout=60)
    r.raise_for_status()
    d = r.json()
    gen = str(d.get("generated_at", ""))
    gen_day = gen[:10]

    meta_p = STORE / "citic_ai_chain_meta.csv"
    if meta_p.exists():
        import csv
        with open(meta_p) as f:
            rows = list(csv.DictReader(f))
        if rows and rows[-1]["generated_at"][:10] == gen_day:
            log(f"{gen_day} 已存在(generated_at={gen[:19]}), 跳过")
            return

    (STORE / "citic_ai_chain_raw.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    # 日期化原始存档(PIT 溯源: 每日一份当日视图, ~350KB/日)
    (STORE / f"citic_ai_chain_raw_{gen_day}.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")

    # 信用长表
    out = []
    data = d.get("data") or {}
    smeta = {s["key"]: s for s in (d.get("series") or [])}
    for key, (kind, entity, tenor) in CREDIT_MAP.items():
        s = data.get(key)
        if not s:
            continue
        src = (smeta.get(key) or {}).get("source", "")
        dates = s.get("dates") or []
        vals = s.get("values") or s.get("vals") or []
        for dt, v in zip(dates, vals):
            if v is None:
                continue
            out.append((dt, kind, entity, tenor, round(float(v), 2), src))
    out.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    with open(STORE / "citic_ai_credit.csv", "w", newline="", encoding="utf-8") as f:
        f.write("date,kind,entity,tenor,value_bps,source\n")
        for row in out:
            f.write(",".join(str(c) for c in row) + "\n")

    # 债券表现
    bonds = d.get("bonds") or []
    with open(STORE / "citic_ai_bonds.csv", "w", newline="", encoding="utf-8") as f:
        f.write("asof,co,isin,maturity,coupon,ytd,latest,change\n")
        for b in bonds:
            f.write(",".join(str(b.get(k, "")) for k in
                    ("asof", "co", "isin", "maturity", "coupon", "ytd", "latest", "change")) + "\n")

    # 全量序列铺平: 元数据表 + 长表(155 组, 任何 agent 可查)
    with open(STORE / "citic_ai_series_meta.csv", "w", newline="", encoding="utf-8") as f:
        f.write("key,name,unit,freq,source,chart,style,sort_i\n")
        for s in (d.get("series") or []):
            f.write(",".join(str(s.get(k, "")).replace(",", ";") for k in
                    ("key", "name", "unit", "freq", "source", "chart", "style", "sort_i")) + "\n")
    n_long = 0
    with open(STORE / "citic_ai_series_long.csv", "w", newline="", encoding="utf-8") as f:
        f.write("key,date,value\n")
        for k, s in sorted(data.items()):
            if not isinstance(s, dict):
                continue
            dates_l = s.get("dates") or []
            vals_l = s.get("values") or s.get("vals") or []
            for dt, v in zip(dates_l, vals_l):
                if v is None:
                    continue
                f.write(f"{k},{dt},{v}\n")
                n_long += 1

    need_header = not meta_p.exists() or meta_p.stat().st_size == 0
    with open(meta_p, "a", encoding="utf-8") as f:
        if need_header:
            f.write("fetched_at,generated_at,credit_rows,bond_rows\n")
        f.write(f"{now_cst()},{gen},{len(out)},{len(bonds)}\n")
    log(f"{gen_day}: 信用 {len(out)} 行, 债券 {len(bonds)} 行落地")

    # GVRM 专用小包(~30KB): cds5y 全序列+均值+CRWV + zspread最新 + 期限结构最新 + 债券 + 披露
    def series_of(key):
        s = data.get(key) or {}
        return {"dates": s.get("dates") or [], "values": s.get("values") or s.get("vals") or []}

    pack = {
        "pack_version": "citic-credit-v1",
        "generated_at": gen,
        "fetched_at": now_cst(),
        "cds5y": {n.upper(): series_of(f"cds.{n}") for n in ("amzn", "googl", "meta", "msft", "orcl", "crwv")},
        "zspread_latest": {},
        "term_latest": {},
        "bonds": bonds,
        "gs_anchors": {  # 高盛 2026-11-09 信贷报告离散读数(华尔街见闻 3785628 转述), 非日频序列
            "chip_basket_bps": {"2026-08-03": 80.0, "2026-11-06": 160.0},
            "note": "高盛: 芯片商(AVGO/AMD/NVDA/INTC)CDS篮子8月初~80bp→11月~160bp, 首次上穿云厂商篮子",
        },
        "disclosure": ("CDS/Z-Spread=中信前瞻海外AI产业链跟踪数据库(LSEG/CME CMAN/ICE源, 机构订阅); "
                       "芯片篮子仅高盛报告离散锚点; 信用利差为市场违约风险定价, 非投资建议; "
                       "CRWV(CoreWeave)量级远高于投资级, 图表右轴单独呈现。"),
    }
    for n in ("amzn", "googl", "meta", "msft"):
        s = series_of(f"credit.zsp_{n}")
        pack["zspread_latest"][n.upper()] = s["values"][-1] if s["values"] else None
    for n in ("orcl", "meta", "crwv"):
        pack["term_latest"][n.upper()] = {t.upper(): (series_of(f"credit.{n}_{t}")["values"] or [None])[-1]
                                          for t in ("1y", "3y", "5y", "10y")}
    (STORE / "citic_credit_pack.json").write_text(
        json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    log(f"GVRM 包 citic_credit_pack.json 写出(cds5y {len(pack['cds5y'])} 序列)")


if __name__ == "__main__":
    main()
