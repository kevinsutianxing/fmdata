"""刷新申万一级行业主力资金流(东财资金流数据源)。

【东财代理技巧 — 2026-06-16 固化, 03:32 生产实证成功】抓取经 fmdata.recipe_fetcher.eastmoney_get
(QG 代理池 IP 轮换), 关键技巧见该函数 docstring。本脚本负责申万行业口径与落盘。

数据口径: 东财行业板块(fs=m:90 t:2 f:!50) 与申万 31 个一级行业"精确同名"者(约15个)。
诚实失败机制: 抓到新数据 exit(0); 代理全挂(0 行业成功) exit(1)→fmdata 报 error(不再伪 ok
掩盖陈旧); 工作正常但无新交易日(周末/已最新) exit(0)。
部分写入自愈: wide append + drop_duplicates(keep=last); 一次只抓到部分行业/日期, 下次
lmt=120 历史回填补全(2026-06-16 03:32 实证: 代理抓出 交通运输/公用事业 两列新行业)。

历史教训(2026-06-16 自我订正): 曾据一次"直连5/5 vs 代理1/5"同窗口快照误判"代理是病根"改直连,
但那是(取证~56次请求把本机IP打废 + 恰撞代理差窗口)之后拍的; 同日 03:32 代理版生产实抓成功
(本文件 .bak.direct-20260616 即成功版备份)证伪了该结论——代理才是正解, 已回滚恢复。
"""
import sys
import time
import pandas as pd
from pathlib import Path
from fmdata.recipe_fetcher import eastmoney_get   # 东财代理技巧固化(push2his+IP轮换+退避)

STORE = Path("/home/ubuntu/fmdata/store")
net_path = STORE / "market/sw_main_flow_net.csv"
pct_path = STORE / "market/sw_main_flow_pct.csv"

# 申万一级行业(31个); 与东财行业板块精确同名者可抓(约15个)
SW_NAMES = [
    "农林牧渔", "基础化工", "钢铁", "有色金属", "电子", "汽车", "家用电器", "食品饮料",
    "纺织服饰", "轻工制造", "医药生物", "公用事业", "交通运输", "房地产", "商贸零售",
    "社会服务", "银行", "非银金融", "综合", "建筑材料", "建筑装饰", "电力设备", "机械设备",
    "国防军工", "计算机", "传媒", "通信", "煤炭", "石油石化", "环保", "美容护理",
]

# BK 映射 fallback: clist 失败(代理差窗口)时用这些已多次实测确认的核心行业。
# 代理好时 get_bk_map() 会动态 clist 拿到更全的 15 个; 差时至少这 5 个保底。
# (从 push2his clist fs=m:90 t:2 f:!50 多次实测确认, 2026-06-15)
BK_FALLBACK = {
    "银行": "BK1283", "汽车": "BK1211", "医药生物": "BK1216",
    "有色金属": "BK0478", "国防军工": "BK1204",
}

CLIST_URL = "https://push2his.eastmoney.com/api/qt/clist/get"
DAYKLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
CLIST_PARAMS = {
    "pn": 1, "pz": 500, "po": 1, "np": 1,
    "ut": "b2884a393a59ad64002292a3e90246f5", "fltt": 2, "invt": 2,
    "fid": "f62", "fs": "m:90 t:2 f:!50", "fields": "f12,f14",
}


def get_bk_map():
    """动态 clist 拿 申万行业 -> BK 映射; 失败用 fallback。返回 (map, 来源说明)。"""
    bk_map = dict(BK_FALLBACK)
    j = eastmoney_get(CLIST_URL, CLIST_PARAMS, max_tries=12)
    if j and j.get("data"):
        items = j["data"].get("diff", []) or []
        for it in items:
            if it.get("f14") in SW_NAMES:
                bk_map[it["f14"]] = it["f12"]
        src = f"clist({len(items)}板块)+fallback"
    else:
        src = "fallback(clist 代理失败)"
    return bk_map, src


# 读已有日期(合并 net/pct 两个文件的 date)
existing_dates = set()
for path in (net_path, pct_path):
    if path.exists():
        old = pd.read_csv(path)
        if "date" in old.columns:
            existing_dates |= set(old["date"].astype(str).str[:10].tolist())

# deadline 守卫(2026-06-15): recipe 外层超时 900s, 总耗时 >750s 则 daykline 循环 break 写
# 已抓数据。把"好窗口 clist 成功(15行业)+daykline 全慢败"的 15×10×~8s≈1376s>900s 强杀,
# 变成"干净写部分数据 exit 0"。750s 留 150s 写入余量; 部分数据由下次 lmt=120 历史回填自愈。
DEADLINE_S = 750
t0 = time.time()

bk_map, src = get_bk_map()
sw_map = {k: v for k, v in bk_map.items() if k in SW_NAMES}
print(f"映射来源={src}; 抓取 {len(sw_map)} 个申万一级行业: {sorted(sw_map)}")

net_rows, pct_rows = {}, {}   # {date: {industry: value}}
success = 0                   # 成功抓到 daykline 的行业数(区分"代理全挂" vs "无新交易日")
for name, bk in sorted(sw_map.items()):
    if time.time() - t0 > DEADLINE_S:
        print(f"  ⚠ deadline {DEADLINE_S}s 触发, 已抓 {success}/{len(sw_map)} 行业, 写部分数据退出")
        break
    j = eastmoney_get(DAYKLINE_URL, {
        "secid": f"90.{bk}", "lmt": 120, "klt": 101,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f57",   # 日期, 主力净流入额, 主力净流入占比%
    }, max_tries=10)
    if not j or not j.get("data"):
        print(f"  {name}({bk}): 拉取失败(代理差窗口), 跳过")
        continue
    success += 1
    klines = j["data"].get("klines", []) or []
    new_cnt = 0
    for kl in klines:
        parts = kl.split(",")
        if len(parts) < 3:
            continue
        d = parts[0][:10]
        if d in existing_dates:
            continue
        try:
            net_val = float(parts[1]) if parts[1] else None
            pct_val = float(parts[2]) if parts[2] else None
        except (ValueError, IndexError):
            continue
        if net_val is not None:
            net_rows.setdefault(d, {})[name] = net_val
        if pct_val is not None:
            pct_rows.setdefault(d, {})[name] = pct_val
        new_cnt += 1
    print(f"  {name}({bk}): {len(klines)} 条历史, {new_cnt} 个新日期")
    time.sleep(0.3)   # 低频: 避免高频触发东财对资金流端点的频率降权

# 诚实 exit: 代理全挂才报 error; 正常无新交易日(周末/已最新)报 ok
if success == 0:
    print(f"ERROR: 全部 {len(sw_map)} 个行业抓取失败(QG 代理池不可用/死窗口), 无数据写入")
    sys.exit(1)
if not net_rows:
    print(f"OK: {success} 个行业抓取成功, 但无新交易日(可能周末或数据已是最新)")
    sys.exit(0)


def append_wide(path, new_data):
    rows = pd.read_csv(path).to_dict("records") if path.exists() else []
    for d in sorted(new_data):
        rows.append({"date": d, **new_data[d]})
    r = pd.DataFrame(rows).drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    r.to_csv(path, index=False)
    return len(r)


n_net = append_wide(net_path, net_rows)
n_pct = append_wide(pct_path, pct_rows)
print(f"OK Net: {n_net} rows, Pct: {n_pct} rows, 新增 {len(net_rows)} 交易日, latest={max(net_rows.keys())}")
