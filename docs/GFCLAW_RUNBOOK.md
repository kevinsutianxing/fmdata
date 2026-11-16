# gfclaw COS 数仓 RUNBOOK（2026-11-15 接入）

国寿安保（苏天醒）机构级 Parquet 数仓，91 表 / 26G / 1990→今，月度分区。
本手册讲**怎么用**；字段级说明看 `/data/gfclaw/references/table_fields.md`（每次同步自动重生成，90 表全量中文含义）。

## 一、架构 30 秒

```
腾讯COS桶 gfclaw-1255885899 (服务端每日 18:00/19:40/22:00 三波发布)
  └─ cron 23:10 daily_sync.sh（etag/sha256增量，flock防并发，失败飞书告警）
       └─ /data/gfclaw/local_market_data/<表>/period=YYYY-MM/*.parquet（91表）
            └─ fmdata :1934  GET /gfclaw/tables + GET /gfclaw/query（SZ81/HK43 agent 统一入口）
```

- 数据落 /data 盘（80G）；recipe 名 `gfclaw_warehouse`（catalog 搜"机构持仓/调研/一致预期"可命中）
- 同步状态看 `/data/gfclaw/logs/daily_sync_YYYYMMDD.log` 尾部（🔴`_sync_status.parquet` 因全历史 scope 永不推进，勿当健康依据）；成功心跳 `/data/gfclaw/logs/.last_success`（治理 Phase 4b 监测它）

## 二、查询用法

```bash
# 1. 发现：表清单+每表字段中文含义+分区范围
curl -s http://127.0.0.1:1934/gfclaw/tables                    # 全部91表
curl -s "http://127.0.0.1:1934/gfclaw/tables?table=eq_quote_daily"   # 单表详情

# 2. 查询：日期自动识别日期列(mark_day/post_day/active_from...)，分区裁剪+行级过滤
curl -s "http://127.0.0.1:1934/gfclaw/query?table=eq_quote_daily&ticker_id=600519.SH&start=2026-11-10&end=2026-11-14&limit=5"

# 3. 选列（宽表默认裁前40列防超限，要全列显式给 columns=a,b,c）
curl -s "http://127.0.0.1:1934/gfclaw/query?table=fdm_indicator_core&ticker_id=000001.SZ&start=2026-08-01&columns=ticker_id,post_day,cutoff_day,roe_avg"

# 4. 回测口径：必须 frozen_only=true（只读冻结分区，当月 current 层每日重刷禁入回测）
curl -s "http://127.0.0.1:1934/gfclaw/query?table=eq_derivative_daily&start=2026-10-01&end=2026-10-31&frozen_only=true&limit=10"
```

HK43 agent 走 SSH 透传：`ssh sz81 "curl -s 'http://127.0.0.1:1934/gfclaw/...'"`

**代码体系**：`ticker_id` 直接是带后缀代码——A股 `600519.SH`/`000001.SZ`/`920489.BJ`、港股 **4 位** `0700.HK`/`3690.HK`（🔴非 5 位 00700）、美股 Wind 后缀 **`AAPL.O`/`MSFT.O`**（🔴说明书写的 .OQ 与实际不符，实测 .O=NASDAQ 主板系；.N/.A/.OB/.P/.BAT=其他交易所与 OTC）。指数表用 `bench_id`，港股财务表用 `corp_id`（映射见 `eq_security_profile`/`eq_hk_security_profile`/`idx_security_profile`）。

**美股 K 线路由（2026-11-16 定）**：研究/回测层**主源 = `eq_us_quote_daily`**（3.3万只全市场、2000-01→、原始价+`restated_ratio` 复权因子+除息修正版 `prior_close_px`；与 EM 源 AAPL 4 日收益率实测差 0.4-6.0bp、量差 ~2.5%）；GVRM 盘中监控层**保留 `us_equity_kline_em`**（36 票监控池、15:25 拉 15:40 sync 的 T+0 供给 + hfq_rebased 口径 GVRM 已适配——gfclaw 是 T+1，换不动这层）。

## 三、PIT 规则（回测必读）

| 层 | 语义 | 回测 |
|----|------|------|
| `period=<上月及以前>` | history 冻结归档，不可变 | ✅ 可用 |
| `period=<当月>` | current 层整月每日重刷 | 🔴 禁入回测终值（查活数据去掉 frozen_only） |

事件表（行业归属/ST/指数成分/港股通）带 `active_from` 区间语义；财务表以 `post_day`（披露日）为可知时点，`cutoff_day` 为报告期——PIT 合并按 post_day 对齐，勿用 cutoff_day 前视。一致预期表 `ana_consensus_snap`/`_rolling_daily` 以 `sample_day` 为可知时点。

## 四、高频表速查（完整版看 table_fields.md）

| 用途 | 表 | 覆盖 |
|------|-----|------|
| A股日行情/复权 | eq_quote_daily | 1990-12→，5.4k 票 |
| 市值/估值/TTM 衍生 | eq_derivative_daily, eq_valuation_daily, mkt_ev_metrics_d | |
| 财务三表+指标 | fdm_{balance,cashflow,income}_core, fdm_indicator_core, fdm_ttm_indicator_core | post_day PIT |
| 附注明细 8 张 | fdm_{top5_revenue,inventory,rd_expense,receivable_aging,asset_impairment,finance_expense,nonrecurring_pl,sales_segment}_detail | |
| 一致预期 | ana_consensus_snap / _rolling_daily(自然日滚动,FY0-FY3/FTTM) / ana_earning_forecast_event / ana_rating_consensus_snap | 1999→ |
| 机构持仓 | eq_institution_holding_detail | 1998→ 季度 |
| 调研 | corp_ir_qa / corp_ir_activity / corp_ir_participant | 2010→ |
| 行业归属(事件) | eq_sw_class_event / eq_citic_class_event（带一二级名称） | |
| 指数 | idx_benchmark_quote_daily / idx_weight_ff_daily(自由流通权重) / idx_sw_l2_quote_daily / idx_citics_l2_quote_daily | |
| 港股全套 | eq_hk_quote_daily / fdm_hk_* / eq_hk_stock_connect_member_event / mkt_hk_connect_flow_daily | 1990→ |
| 美股 | eq_us_quote_daily | 2000→ |
| 转债/基金/商品 | bond_convertible_quote_daily / fund_etf_{quote,nav}_daily / commodity_gold_spot_quote_daily | |
| 宏观 220 指标 | macro_data（中国/美国经济+汇率利率+商品，最早 1990） | 🔴唯一无字典行的表，字段看官方安装说明或直接查 |

## 五、坑（排障先看）

1. **flavor 互踩**：服务端多 flavor（core/institution/ir/management/consensus）共用 latest.json 指针。实测任一 flavor 都是 91 表全量（同 key etag 一致），跟着走即可；翻转时 churn ~200MB 属正常。
2. **特殊分区**：`pre2020-*`（2020 前历史打包）与 `undated` 永远被扫描，行级日期过滤兜底——查 2020 后数据不会混入旧事件，但查全历史时它们在场。
3. **宽表 40 列裁剪**：>40 列的表默认只回前 40 列（防 10M cell 上限），要后面的列用 `columns=` 显式指定。
4. **行数上限**：200k 行/10M cell 硬顶（413 同款保护），大截面查询用 ticker_id/start/end 缩小范围。无任何过滤的查询走 head 快路径（`total_matched=-1` 表示未计数），秒回；**大表（机构持仓/一致预期滚动/行情全截面）务必带 start/end 或 ticker_id**，否则全表物化会拖慢。
5. **当日棒**：当天数据要等服务端 18:00 波发布 + 我们 23:10 同步，即当日数据最快次日 00:00 后可查（T+1 供给，非实时——实时需求走 westock）。
6. **官方安装说明（2026-06-09 版，广发金工·安宁宁）与现状的差异**：说明书 51 表/4.4G 是 6 月口径（这就是提供方"5 个多 G"说法的来源），现在实际 91 表/25.8G；其 `calc_ttm_roe.py` 脚本当前 zip/bundle 均未附带（TTM 需求用 `fdm_ttm_indicator_core` 或按说明书公式自算：TTM净利=近4单季归母之和，平均净资产=(4季前+当期)/2）；代码 bundle 里反而多了缠论/业绩披露/业绩预告分析模块。

## 六、运维

```bash
# 手动补同步（不与 cron 撞，flock 自动让行）
bash /data/gfclaw/scripts/daily_sync.sh

# 治理审计视角手动巡检
python3 ~/claude-workspace/scripts/fmdata-governance.py --audit-only

# 凭据：/data/gfclaw/.gfclaw_cos_config.json（600，只读子账号；失效找苏天醒重发后改此文件即可）
```

cron 23:10（SZ81 crontab，避开三波发布窗）；治理 Phase 4b 按心跳监测新鲜度；总登记见 `docs/data-sources-inventory.md` #13 与 memory `gfclaw-cos-warehouse-integration-20261115`。
