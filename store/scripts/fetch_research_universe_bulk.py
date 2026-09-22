#!/usr/bin/env python3
"""研究股票池（沪深300∪中证500 历史成分并集 1577 只）批量拉取 tushare 数据。

按 API 分组逐股拉取，输出到 ~/fmdata/store/research_export/{api}.csv。
断点续跑：每个 API 输出文件已含的 ts_code 自动跳过。
限速：调用间 sleep 0.18s；限频错误退避重试。

API 顺序（P0 优先）: daily, adj_factor, daily_basic, stk_limit, suspend_d, namechange,
                     income, balancesheet, cashflow, fina_indicator
"""
import os
import sys
import time
import datetime
import pandas as pd
import tushare as ts

UNIVERSE = os.path.expanduser("~/fmdata/store/market/research_universe_hs300_zz500.csv")
OUTDIR = os.path.expanduser("~/fmdata/store/research_export")
START, END = "20150101", datetime.date.today().strftime("%Y%m%d")

APIS = [
    # (name, kwargs template, date_ranged?)
    ("daily", dict(start_date=START, end_date=END), True),
    ("adj_factor", dict(start_date=START, end_date=END), True),
    ("daily_basic", dict(start_date=START, end_date=END), True),
    ("stk_limit", dict(start_date=START, end_date=END), True),
    ("suspend_d", dict(start_date=START, end_date=END), True),
    ("namechange", dict(), False),
    ("income", dict(fields="ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,total_revenue,revenue,oper_cost,operate_profit,total_profit,n_income,n_income_attr_p"), False),
    ("balancesheet", dict(fields="ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,total_cur_assets,total_assets,total_cur_liab,total_liab,accounts_receiv,inventories,fix_assets,lt_borr,st_borr,total_hldr_eqy_exc_min_int"), False),
    ("cashflow", dict(fields="ts_code,ann_date,f_ann_date,end_date,report_type,n_cashflow_act,n_cashflow_inv_act,free_cashflow,c_pay_acq_const_fiolta,end_bal_cash"), False),
    ("fina_indicator", dict(fields="ts_code,ann_date,end_date,eps,dt_eps,roe,roe_waa,roe_dt,roa,grossprofit_margin,netprofit_margin,debt_to_assets,netprofit_yoy,or_yoy,ocf_yoy,ocf_to_or"), False),
]


def _retry(fn, *args, **kwargs):
    last = None
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 4:
                time.sleep(5 + attempt * 5)
                continue
            raise
    raise last


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    ts.set_token(os.environ.get("TUSHARE_TOKEN", ""))
    pro = ts.pro_api()
    universe = pd.read_csv(UNIVERSE)["code"].tolist()
    log(f"universe: {len(universe)} stocks, range {START}-{END}")

    for api_name, kwargs, _ in APIS:
        out_csv = f"{OUTDIR}/{api_name}.csv"
        fail_log = f"{OUTDIR}/{api_name}.failures"
        done = set()
        if os.path.exists(out_csv):
            done = set(pd.read_csv(out_csv, usecols=["ts_code"]).ts_code.unique())
        todo = [c for c in universe if c not in done]
        log(f"== {api_name}: {len(done)} done, {len(todo)} todo ==")
        fn = getattr(pro, api_name)
        header = not os.path.exists(out_csv)
        fails = []
        for i, code in enumerate(todo):
            try:
                df = _retry(fn, ts_code=code, **kwargs)
                if df is None or not len(df):
                    fails.append(code)
                else:
                    df.to_csv(out_csv, mode="a", header=header, index=False)
                    header = False
            except Exception as e:
                log(f"  ERR {code}: {str(e)[:100]}")
                fails.append(code)
            if i % 50 == 0:
                log(f"  {api_name} {i}/{len(todo)}")
            time.sleep(0.18)
        if fails:
            with open(fail_log, "a") as f:
                f.write("\n".join(fails) + "\n")
            log(f"{api_name} DONE: {len(todo)-len(fails)} ok, {len(fails)} empty/failed")
        else:
            log(f"{api_name} DONE: all {len(todo)} ok")
    log("ALL DONE")


if __name__ == "__main__":
    main()
