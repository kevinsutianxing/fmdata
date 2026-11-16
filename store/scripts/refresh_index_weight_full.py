#!/usr/bin/env python3
"""index_weight_000{300,905,852}_full.csv 月度增量刷新（fmdata 生产脚本，直连 tushare 例外适用）。

复用 fetch_index_weight_hs300_zz500.py / fetch_index_weight_000852.py 的拉取模式
（tushare pro.index_weight + TUSHARE_TOKEN + 限频退避 + 原子写），差别 = 增量：
  - 目标月 ym（缺省=运行日上月，TZ=Asia/Shanghai）已在文件 → 校验行数后 no-op 退出 0（幂等）
  - 否则从（文件内最大 trade_date 次日）拉到今天，按 (con_code, trade_date) 去重追加（旧行优先）
断言（失败非零退出，由 index_weight_refresh.sh 发飞书告警）：
  - 每个指数 ym 快照恰 300/500/1000 行（历史 142 个月快照全部精确等于成分数）
  - 三文件最新月份 == ym
cons7 月度重导（每月 7 日 07:17）依赖本产物：本脚本 cron 在 6 日 07:07 先行补齐上月权重。
用法：python3 refresh_index_weight_full.py [YYYYMM]
环境：TUSHARE_TOKEN（必填）；INDEX_WEIGHT_DIR（缺省 store/market，沙盒测试用）
"""
import os
import sys
import time

import pandas as pd

OUT = {  # index_code -> (文件名, 快照行数)
    "000300.SH": ("index_weight_000300_full.csv", 300),
    "000905.SH": ("index_weight_000905_full.csv", 500),
    "000852.SH": ("index_weight_000852_full.csv", 1000),
}
COLS = ["index_code", "con_code", "trade_date", "weight"]


def die(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def prev_month(ym):
    y, m = int(ym[:4]), int(ym[4:]) - 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y}{m:02d}"


def _retry(fn, *args, **kwargs):
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if any(k in msg for k in ("频率", "limit", "每分钟", "积分", "credit", "20000")) and attempt < 4:
                time.sleep(5 + attempt * 5)
                continue
            raise


def fetch_window(pro, index_code, start, end):
    """start..end 按 ~2 月窗口分块拉取（单次上限 ~7000 行防御）。"""
    frames = []
    s = start
    while s <= end:
        e = min(pd.Timestamp(s) + pd.DateOffset(months=2) - pd.Timedelta(days=1),
                pd.Timestamp(end)).strftime("%Y%m%d")
        df = _retry(pro.index_weight, index_code=index_code, start_date=s, end_date=e)
        if df is not None and len(df):
            print(f"  {index_code} {s}-{e}: {len(df)} rows", file=sys.stderr)
            frames.append(df)
        time.sleep(0.3)
        s = (pd.Timestamp(e) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    return frames


def main():
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    ym = sys.argv[1] if len(sys.argv) > 1 else prev_month(time.strftime("%Y%m"))
    print(f"[index-weight-refresh] target ym={ym}")
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        die("TUSHARE_TOKEN 未设置（.sh 层从 ~/fmdata/.env 提取）")
    import tushare as ts
    pro = ts.pro_api(token)

    mkt = os.path.expanduser(os.environ.get("INDEX_WEIGHT_DIR", "~/fmdata/store/market"))
    today = time.strftime("%Y%m%d")
    changed = []
    for idx, (fn, size) in OUT.items():
        path = os.path.join(mkt, fn)
        if not os.path.exists(path):
            die(f"{fn} 不存在——首次建库走 fetch_index_weight_hs300_zz500.py / fetch_index_weight_000852.py")
        old = pd.read_csv(path, dtype={"con_code": str, "trade_date": str})
        months = old.trade_date.str[:6]
        if ym in set(months):
            n = int((months == ym).sum())
            if n != size:
                die(f"{fn} {ym} 快照已有 {n} 行 != {size}")
            print(f"[NO-OP] {fn} {ym} 已在 ({n} 行) — 幂等跳过")
            continue
        last = old.trade_date.max()
        start = (pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y%m%d")
        if start > today:
            die(f"{fn} 文件最新 {last} 在今天之后，数据异常")
        frames = fetch_window(pro, idx, start, today)
        if not frames:
            die(f"{idx} {start}-{today} tushare 无返回行（目标 {ym} 缺权重）")
        new = pd.concat(frames, ignore_index=True)[COLS]  # 列缺失/改名 → KeyError 显式失败
        merged = (pd.concat([old[COLS], new[COLS]], ignore_index=True)
                  .drop_duplicates(subset=["con_code", "trade_date"], keep="first")  # 旧行优先
                  .sort_values(["trade_date", "weight"], ascending=[True, False]))
        n = int((merged.trade_date.str[:6] == ym).sum())
        if n != size:
            die(f"{fn} 刷新后 {ym} 快照={n} 行 != {size}（tushare 当月快照未齐/未发布）")
        tmp = path + ".tmp"
        merged.to_csv(tmp, index=False)
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
        changed.append(f"{fn}: +{len(merged) - len(old)} 行 → 止于 {merged.trade_date.max()}")

    # 终检：三文件最新月份 == ym
    for fn, _ in OUT.values():
        d = pd.read_csv(os.path.join(mkt, fn), usecols=["trade_date"], dtype=str)
        latest = d.trade_date.max()[:6]
        if latest != ym:
            die(f"{fn} 最新月份={latest} != {ym}")
    if changed:
        print("[OK] " + "; ".join(changed))
    else:
        print(f"[OK] 三文件 {ym} 均已在 — 全程 no-op")


if __name__ == "__main__":
    main()
