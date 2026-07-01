"""统一刷新申万一级行业完整数据。

产出:
  - market/sw_daily.csv        long 格式日线 (ts_code/trade_date/OHLCV/pe/pb/...),
                               trade_date 保持无横杠 int (该文件历史格式)
  - market/sw_daily_v2.csv     11 列子集 + 带横杠日期 (active 轮动策略消费)
  - market/sw_{pe_history,pb_history,first_level_close,first_level_amount}.csv
                               wide 格式 (date × 行业), 全部带横杠日期

sw_daily.csv 是单一源; 其余 5 个文件均由 derive_derived_files 从它派生,
全带横杠 (消费者契约)。消灭旧的 refresh_sw_pe_pb/refresh_sw_data 多写者格式冲突。
"""
import time
from datetime import datetime, timedelta

import pandas as pd
from pathlib import Path
import tushare as ts

STORE = Path("/home/ubuntu/fmdata/store")
SW_DAILY = STORE / "market/sw_daily.csv"

# 申万一级 ts_code -> 行业名 (静态映射)。不读 sw_daily.csv 的 name 列 (99.5% 为空),
# 也不在派生时再调 tushare index_classify。32 条是 sw_daily 31 个 ts_code 的超集
# (多一个已废弃的 801020 采掘)。与 quant_allocation/industry_rotation_pro.py 的 CODE_MAP 同源。
CODE_MAP = {
    '801010.SI': '农林牧渔', '801020.SI': '采掘', '801030.SI': '化工',
    '801040.SI': '钢铁', '801050.SI': '有色金属', '801080.SI': '电子',
    '801110.SI': '家用电器', '801120.SI': '食品饮料', '801130.SI': '纺织服饰',
    '801140.SI': '轻工制造', '801150.SI': '医药生物', '801160.SI': '公用事业',
    '801170.SI': '交通运输', '801180.SI': '房地产', '801200.SI': '商业贸易',
    '801210.SI': '社会服务', '801230.SI': '综合', '801710.SI': '建筑材料',
    '801720.SI': '建筑装饰', '801730.SI': '电气设备', '801740.SI': '国防军工',
    '801750.SI': '计算机', '801760.SI': '传媒', '801770.SI': '通信',
    '801780.SI': '银行', '801790.SI': '非银金融', '801880.SI': '汽车',
    '801890.SI': '机械设备', '801950.SI': '煤炭', '801960.SI': '石油石化',
    '801970.SI': '环保', '801980.SI': '美容护理',
}

# sw_daily_v2 的 11 列 (active 轮动策略 industry_rotation_pro 期望的 schema)
V2_COLS = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
           'pct_change', 'vol', 'amount', 'pe', 'pb']


def _to_dash(series):
    """无横杠日期列 (int 20210104) -> 带横杠 str (2021-01-04)。"""
    return pd.to_datetime(series.astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')


def derive_derived_files(sw_df):
    """从 sw_daily.csv (long, 无横杠) 派生 5 个带横杠文件: sw_daily_v2 + 4 个 wide。
    单一写入路径, 不读已存在文件 (全量重算), 故无 int/str concat 陷阱。"""
    df = sw_df.copy()
    df['trade_date'] = _to_dash(df['trade_date'])
    df['industry'] = df['ts_code'].map(CODE_MAP)
    df = df.dropna(subset=['industry'])  # 未知 ts_code 不进 wide (防御)

    market = STORE / "market"
    market.mkdir(parents=True, exist_ok=True)

    # sw_daily_v2 (long, 11 列)
    v2 = df[[c for c in V2_COLS if c in df.columns]].sort_values(['trade_date', 'ts_code'])
    v2.to_csv(market / "sw_daily_v2.csv", index=False)

    # 4 个 wide: date × 行业。pe 的少量 null 保留 NaN (源数据如此, 占比 <0.01%, 不 ffill)
    last = None
    for col, out in [('close', 'sw_first_level_close.csv'),
                     ('amount', 'sw_first_level_amount.csv'),
                     ('pe', 'sw_pe_history.csv'),
                     ('pb', 'sw_pb_history.csv')]:
        last = (df.pivot_table(index='trade_date', columns='industry',
                               values=col, aggfunc='first')
                  .sort_index().reset_index()
                  .rename(columns={'trade_date': 'date'}))
        last.to_csv(market / out, index=False)

    print(f"derive: sw_daily_v2 {len(v2)} rows; wide {len(last)} dates × {last.shape[1]-1} industries")


def main():
    pro = ts.pro_api()
    industries = pro.index_classify(level='L1', src='SW2021')
    codes = industries['index_code'].tolist()
    names = industries['industry_name'].tolist()
    print(f"共 {len(codes)} 个一级行业")

    # 增量起点: 仅看 sw_daily.csv (单一源), 不再看派生文件
    start_date = None
    if SW_DAILY.exists():
        old = pd.read_csv(SW_DAILY)
        if not old.empty and 'trade_date' in old.columns:
            last = pd.to_datetime(str(old['trade_date'].max()))
            start_date = (last + timedelta(days=1)).strftime("%Y%m%d")
            print(f"增量更新从 {start_date} 开始")

    all_rows = []
    for i, (code, name) in enumerate(zip(codes, names)):
        try:
            params = {"ts_code": code, "end_date": "20991231"}
            if start_date:
                params["start_date"] = start_date
            df = pro.sw_daily(**params)
            if df is not None and not df.empty:
                all_rows.append(df)
                print(f"  [{i+1}/{len(codes)}] {name}({code}): +{len(df)} 条")
            else:
                print(f"  [{i+1}/{len(codes)}] {name}({code}): 无新数据")
            time.sleep(0.5)
        except Exception as e:
            print(f"  [{i+1}/{len(codes)}] {name}({code}): 错误 {e}")

    if not all_rows:
        print("无新数据")
        return

    combined = pd.concat(all_rows, ignore_index=True)
    combined = combined.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)
    print(f"合计 {len(combined)} 条新数据")

    # 追加到 sw_daily (long, trade_date 保持无横杠 —— 历史格式不动)
    if SW_DAILY.exists():
        old = pd.read_csv(SW_DAILY)
        combined_full = pd.concat([old, combined], ignore_index=True)
        # trade_date 两边同源同格式 (无横杠), drop_duplicates 安全;
        # 增量 start_date=last+1 保证无重叠 (结构性保护)
        combined_full = combined_full.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
        combined_full = combined_full.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)
    else:
        combined_full = combined
    combined_full.to_csv(SW_DAILY, index=False)
    print(f"sw_daily: {len(combined_full)} total rows")

    derive_derived_files(combined_full)
    print("Done!")


if __name__ == "__main__":
    main()
