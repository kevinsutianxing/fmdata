"""Fetch US stock spot snapshot (含总市值/流通市值) via eastmoney push2his + QG 代理池。

为什么不直接 source:akshare + func:stock_us_spot_em？
  akshare 源码写死打 72.push2.eastmoney.com（实时子域）。东财对实时子域封代理 IP 极严，
  QG 代理/HK43 直连/SZ81 直连三路全 ProxyError 或 RemoteDisconnected（实测 2026-07-07）。

正解（memory: fmdata-recipe-governance-gotchas / recipe_fetcher.eastmoney_get 文档）：
  同一个 clist/get 筛选器接口在 push2his.eastmoney.com（历史子域）上开放且不封代理。
  按akshare源码精确参数自连 push2his，经 eastmoney_get 的 IP 轮换（每页每次换 QG 出口 IP）。
  实证：push2his clist 返回美股 f20总市值/f21流通市值完整（CRNX 88亿、TDTH 1377万）。

输出列对齐 akshare stock_us_spot_em（代码/名称/最新价/涨跌幅/总市值/流通市值/市盈率/市净率...）。
"""
import sys
import time
import pandas as pd
from fmdata.config import STORE_DIR
from fmdata.recipe_fetcher import eastmoney_get

BASE = "https://push2his.eastmoney.com/api/qt/clist/get"
FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23"
FS = "m:105,m:106,m:107"  # 105=NASDAQ 106=NYSE 107=AMEX
PAGE_SIZE = 100  # 东财 clist 单页硬上限 100（实测 pz=200/500/1000 均只回 100）
PAGE_ROUNDS = 1  # 单页 1 轮（eastmoney_get 自带 max_tries 次 IP 轮换）；东财封批量，快失败优先
EM_MAX_TRIES = 6  # eastmoney_get 内部 IP 轮换次数（默认 8，减到 6 快失败）
MAX_PAGES = 15  # top 1500 by 总市值（覆盖 S&P 500 + 大中盘）；东财指纹识别连发请求，页多必被封
PAGE_DELAY = 3.0  # 页间延迟（秒）：东财按请求频率指纹识别批量，必须慢节奏
EARLY_ABORT_STREAK = 4  # 连续 N 页全失败 → 死窗口，abort 保住已有数据
MIN_SAVE_ROWS = 200  # 抓到的行数低于此 → 不覆盖旧 CSV（防坏运行擦好数据）
SORT_FID = "f20"  # 按总市值排序
SORT_PO = "1"  # 降序（最大市值在前）
MCAP_FLOOR = 5e7  # <$50M 视为小微/壳噪音，早停

COL_MAP = {
    "f2": "最新价", "f3": "涨跌幅", "f4": "涨跌额", "f5": "成交量", "f6": "成交额",
    "f7": "振幅", "f8": "换手率", "f9": "市盈率", "f12": "代码", "f13": "市场代码",
    "f14": "名称", "f15": "最高", "f16": "最低", "f17": "今开", "f18": "昨收",
    "f20": "总市值", "f21": "流通市值", "f23": "市净率",
}
MKT_MAP = {"105": "NASDAQ", "106": "NYSE", "107": "AMEX"}
OUT = STORE_DIR / "overseas/us_stock_spot.csv"


def fetch_page(pn):
    """单页 PAGE_ROUNDS 轮重试，每轮 eastmoney_get 自带 10 次 IP 轮换。返回 diff list 或 None。"""
    params = {
        "pn": str(pn), "pz": str(PAGE_SIZE), "po": SORT_PO, "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "invt": "2",
        "fid": SORT_FID, "fs": FS, "fields": FIELDS,
    }
    for rnd in range(PAGE_ROUNDS):
        data = eastmoney_get(BASE, params, max_tries=EM_MAX_TRIES, timeout=20)
        if data:
            return (data.get("data") or {}).get("diff") or []
        time.sleep(2 + rnd * 2)  # 轮间退避，等簇集窗口漂移
    return None


def _mcap_of(item):
    """安全取单条 f20 总市值（可能为 '-'/None）。"""
    v = item.get("f20")
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("inf")  # 无市值的（权证等）排到阈值判断时不触发早停


def fetch_all():
    all_items = []
    failed_pages = []
    fail_streak = 0
    pn = 1
    while pn <= MAX_PAGES:
        diff = fetch_page(pn)
        if diff is None:
            failed_pages.append(pn)
            fail_streak += 1
            print(f"[page {pn}] 失败（连续 {fail_streak}）", file=sys.stderr)
            # 连续多页全失败 = 死窗口/被指纹封，继续也是浪费，abort 保住已有数据
            if fail_streak >= EARLY_ABORT_STREAK and not all_items:
                print(f"连续 {EARLY_ABORT_STREAK} 页全失败且无任何数据，abort（保留旧 CSV）。", file=sys.stderr)
                return all_items, failed_pages
            pn += 1
            time.sleep(PAGE_DELAY)
            continue
        if not diff:
            break  # 自然到尾
        fail_streak = 0  # 成功一页，重置连续失败计数
        all_items.extend(diff)
        last_mv = _mcap_of(diff[-1])
        print(f"[page {pn}] +{len(diff)} (累计 {len(all_items)}，本页最小市值 ${last_mv/1e9:.2f}B)")
        if last_mv < MCAP_FLOOR:
            print(f"市值跌穿 ${MCAP_FLOOR/1e6:.0f}M 地板，早停（已覆盖可投资域）")
            break
        if len(diff) < PAGE_SIZE:
            break  # 最后一页
        pn += 1
        time.sleep(PAGE_DELAY)  # 慢节奏：东财按请求频率指纹识别批量
    return all_items, failed_pages


def main():
    items, failed_pages = fetch_all()
    if not items:
        print("ERROR: 未抓到任何数据（代理池死窗口/东财全封）。本次不写文件，保留旧快照。", file=sys.stderr)
        sys.exit(1)
    if failed_pages:
        cov = 1 - len(failed_pages) / (len(failed_pages) + len(items) / PAGE_SIZE)
        print(f"⚠️ {len(failed_pages)} 页失败（约 {len(failed_pages)*PAGE_SIZE} 只缺失，覆盖率 ~{cov:.0%}）: 页号 {failed_pages[:10]}{'…' if len(failed_pages)>10 else ''}", file=sys.stderr)

    df = pd.DataFrame(items)
    # 只保留映射列，重命名
    keep = [c for c in COL_MAP if c in df.columns]
    df = df[keep].rename(columns=COL_MAP)
    # 市场代码 → 可读
    if "市场代码" in df.columns:
        df["交易所"] = df["市场代码"].astype(str).map(MKT_MAP).fillna("OTHER")
    # 数值列转 numeric（东财 f 码 fltt=2 已是小数，但 f20/f21 仍是分；这里 fltt=2 + invt=2 下 f20 已是元）
    for c in ["最新价", "涨跌幅", "涨跌额", "成交量", "成交额", "振幅", "换手率",
              "市盈率", "最高", "最低", "今开", "昨收", "总市值", "流通市值", "市净率"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    OUT.parent.mkdir(parents=True, exist_ok=True)

    # 旧 CSV 保护：本次抓得少而旧 CSV 行数更多 → 不覆盖（防坏窗口运行擦掉好数据）
    if OUT.exists():
        try:
            old_rows = sum(1 for _ in open(OUT)) - 1
        except Exception:
            old_rows = 0
        if len(df) < MIN_SAVE_ROWS and old_rows > len(df):
            print(f"⚠️ 本次仅 {len(df)} 行 < 阈值 {MIN_SAVE_ROWS}，且旧 CSV 有 {old_rows} 行 → 不覆盖，保留旧数据。", file=sys.stderr)
            sys.exit(2)  # exit 2 = 本次跳过保存，非错误（cron 不该告警）

    df.to_csv(OUT, index=False)

    # ---- 质量检查（铁律：拉完查行数/NaN/样本）----
    n = len(df)
    print(f"\nOK: {n} rows x {len(df.columns)} cols -> {OUT}")
    if "总市值" in df.columns:
        nn = df["总市值"].notna().sum()
        print(f"  总市值非空: {nn}/{n} ({nn/n:.0%})")
        if nn:
            top = df.nlargest(5, "总市值")
            print("  TOP5 总市值:")
            show = [c for c in ["代码", "名称", "交易所", "总市值", "最新价"] if c in df.columns]
            print(top[show].to_string(index=False))
    if "交易所" in df.columns:
        print("  交易所分布:")
        print(df["交易所"].value_counts().to_string())


if __name__ == "__main__":
    main()
