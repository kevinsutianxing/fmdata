#!/usr/bin/env python3
"""Build full fund-manager ranking workbook from fmdata fund tables."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


BASE = Path("/home/ubuntu/fmdata/store/market")
OUT_DIR = BASE / "fund_rank_outputs"
OUT = OUT_DIR / "fund_manager_rankings_full.xlsx"
EXCLUDE_RE = re.compile("货币|现金|短债|中短债|超短债|同业存单|存款|FOF|养老|目标日期|目标风险", re.I)


def code6(value) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip()
    if "." in s:
        s = s.split(".")[0]
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def short_company_name(name: str) -> str:
    if pd.isna(name):
        return ""
    s = str(name).strip()
    for suffix in ["管理股份有限公司", "管理有限公司", "股份有限公司", "有限公司"]:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip()


def load_rank(name: str) -> pd.DataFrame:
    df = pd.read_csv(BASE / f"{name}.csv", dtype={"基金代码": str})
    df["code6"] = df["基金代码"].map(code6)
    df["近3年"] = pd.to_numeric(df["近3年"], errors="coerce")
    return df


def company_non_money() -> pd.DataFrame:
    d = pd.read_csv(BASE / "fund_company_detail.csv")
    d["基金公司简称"] = d["基金公司"].map(short_company_name)
    money = pd.to_numeric(d.get("货币型"), errors="coerce")
    total = pd.to_numeric(d.get("总规模"), errors="coerce")
    parts = [pd.to_numeric(d.get(c), errors="coerce").fillna(0) for c in ["股票型", "混合型", "债券型", "指数型", "QDII"]]
    fallback = sum(parts)
    d["基金公司非货规模"] = (total - money).where(money.notna(), fallback)
    return d[["基金公司", "基金公司简称", "总规模", "货币型", "基金公司非货规模"]]


def build_sheet(category: str, rank_name: str, fund_type: str, basic: pd.DataFrame, managers: pd.DataFrame, ret5: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    rank = load_rank(rank_name)
    rank = rank[["code6", "基金代码", "基金简称", "日期", "近3年"]].copy()
    rank["近3年排名_产品"] = rank["近3年"].rank(ascending=False, method="min", na_option="bottom").astype("Int64")

    b = basic[basic["status"].fillna("") == "L"].copy()
    b = b[b["fund_type"].fillna("") == fund_type]
    b["filter_text"] = b["name"].fillna("") + " " + b["invest_type"].fillna("")
    b = b[~b["filter_text"].str.contains(EXCLUDE_RE, na=False)]
    merged = rank.merge(b, on="code6", how="inner", suffixes=("", "_basic"))
    merged = merged.merge(ret5[ret5["category"] == category], on="code6", how="left")
    merged["近5年排名_产品"] = merged["five_year_return_pct"].rank(ascending=False, method="min", na_option="bottom").astype("Int64")
    merged = merged.merge(managers, left_on="code6", right_on="manager_code6", how="left")
    merged["公司简称"] = merged["所属公司"].fillna(merged["management"]).map(short_company_name)
    merged = merged.merge(comp, left_on="公司简称", right_on="基金公司简称", how="left")

    # Representative product per manager: complete 5Y first, then best 5Y, then best 3Y.
    merged["five_year_complete_sort"] = merged["five_year_complete"].fillna(False).astype(int)
    merged["five_year_return_sort"] = pd.to_numeric(merged["five_year_return_pct"], errors="coerce").fillna(-1e18)
    merged["three_year_return_sort"] = pd.to_numeric(merged["近3年"], errors="coerce").fillna(-1e18)
    merged = merged.sort_values(
        ["姓名", "five_year_complete_sort", "five_year_return_sort", "three_year_return_sort"],
        ascending=[True, False, False, False],
    )
    rep = merged.dropna(subset=["姓名"]).drop_duplicates(["姓名", "所属公司"], keep="first").copy()
    rep["类别"] = category
    rep["管理规模"] = pd.to_numeric(rep["现任基金资产总规模"], errors="coerce")
    out = rep[[
        "类别",
        "所属公司",
        "基金公司非货规模",
        "姓名",
        "管理规模",
        "基金简称",
        "code6",
        "近3年",
        "近3年排名_产品",
        "five_year_return_pct",
        "近5年排名_产品",
        "start_date",
        "end_date",
        "obs",
        "span_days",
        "five_year_complete",
        "fund_type",
        "invest_type",
    ]].rename(columns={
        "所属公司": "基金公司",
        "姓名": "基金经理名字",
        "基金简称": "代表产品",
        "code6": "代表产品代码",
        "近3年": "代表产品近3年收益率",
        "近3年排名_产品": "近3年排名",
        "five_year_return_pct": "代表产品近5年收益率",
        "近5年排名_产品": "近5年排名",
        "start_date": "5年起始日期",
        "end_date": "5年截止日期",
        "obs": "5年观测数",
        "span_days": "5年跨度天数",
        "five_year_complete": "5年数据完整",
        "fund_type": "基金类型",
        "invest_type": "投资类型",
    })
    incomplete = ~out["5年数据完整"].fillna(False).astype(bool)
    out.loc[incomplete, ["代表产品近5年收益率", "近5年排名"]] = pd.NA
    out = out.sort_values(["近5年排名", "近3年排名", "管理规模"], ascending=[True, True, False])
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    basic = pd.read_csv(BASE / "fund_basic_open.csv", dtype=str)
    basic["code6"] = basic["ts_code"].map(code6)
    managers = pd.read_csv(BASE / "fund_manager.csv", dtype={"现任基金代码": str})
    managers["manager_code6"] = managers["现任基金代码"].map(code6)
    ret5 = pd.read_csv(BASE / "fund_5y_return_full.csv", dtype={"code6": str})
    ret5["code6"] = ret5["code6"].map(code6)
    comp = company_non_money()

    sheets = {
        "纯债基金经理": build_sheet("纯债", "fund_rank_bond", "债券型", basic, managers, ret5, comp),
        "股票基金经理": build_sheet("股票", "fund_rank_equity", "股票型", basic, managers, ret5, comp),
        "偏股混基金经理": build_sheet("偏股混", "fund_rank_hybrid", "混合型", basic, managers, ret5, comp),
    }
    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        for sheet, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.book[sheet]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 10), 28)
    print(f"saved {OUT}")
    for sheet, df in sheets.items():
        print(sheet, len(df), "rows", "complete5y", int(df["5年数据完整"].fillna(False).sum()))


if __name__ == "__main__":
    main()
