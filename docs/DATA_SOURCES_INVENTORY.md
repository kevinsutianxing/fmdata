# 数据源全量清单（2026-11-09 实测盘点）

> 维护规则：新增数据源必须登记到此表；每季度或新增源时复核「探活」列。

## ⛑ 缺数据急救卡（补数据先看这，别现场发挥）

1. **先定位**：缺的是哪张表/哪段时间？`ls -la ~/fmdata/store/<域>/<表>.csv` 看 mtime + `python3 -c "import pandas as pd; df=pd.read_csv('<文件>'); print(df.tail(3))"` 看尾部日期
2. **按路由表选源**（见下文「按用途路由」）：该数据的首选/次选源是什么，别跳过直接找新源
3. **补洞命令速查**：
   - HK 日线（00700/09988/02800/09618）：`python3 ~/fmdata/scripts/fetch_hk_daily_incremental.py`（幂等自动补洞，16:30 前不收当日棒；深历史洞 <5 年用 westock，更老用 tushare hk_daily 配额，5次/天省着烧）
   - SW 金工因子：`python3 ~/fmdata/scripts/backfill_sw_factors.py`（幂等，最近 3 个月 force；服务端发布滞后 >38 天属正常勿惊）
   - 海外 FRED 系（vix/sp500/usd_index 等）：`curl -X POST -H "X-API-Key: $(grep FMDATA_ADMIN_KEY ~/fmdata/.env | cut -d= -f2)" http://127.0.0.1:1934/fetch/<名>`；DXY/WTI 出版滞后 3-12 天属 FRED 节奏非故障
   - FMP/AV：免费档配额见下表陷阱区，AV 25 次/天只够逐 symbol
   - **治理日志固定 `❌ <名>: HTTP Error 502`**（秒回、非超时）：`journalctl --user -u fmdata --since "12 hours ago" | grep '<名>'` 看服务端真因。`script not in allowlist`（source:agent 脚本没进 `fmdata/recipe_fetcher.py::AGENT_SCRIPT_ALLOWLIST`）分两种修，**别修反**：①有专属 cron、数据不靠治理刷新 → 登记治理脚本 `EXTERNALLY_REFRESHED_BY_CRON`，免重启（2026-11-25 cboe_historical）；②治理是唯一刷新路径 → 补 allowlist 一行 + `systemctl --user restart fmdata` 才加载（2026-11-25 sw_industry_pts：monthly 窗口每月 502、数据停在 07-07 无人察，补跑后 5491→5504 行）。只登记跳过而不修刷新 = 永久静默陈旧。治理 Phase 3 开头的「结构性502预检」会点名未登记者与"改了没重启"
4. **补完验证**：行数对不对、日期接上没、抽查 3 行数值（对照另一源）
5. **登记**：如果发现新的缺口模式/坑 → 写进本文档对应段落，别只留在聊天里


> 探活方式：`python3 /tmp/cred_probe_20261109.py`（模板，按需重建）；本次全部凭据实测通过。

## 一、总览（凭据健康度）

| # | 数据源 | 域 | 凭据位置 | 探活(11-09) | 配额/限制 | 续期方式 |
|---|--------|-----|----------|:---:|-----------|----------|
| 1 | tushare | 国内 | `~/fmdata/.env TUSHARE_TOKEN` | ✅ | 2000积分档；hk_daily 5次/天**已迁出日常用量**（2026-11-09 起增量走 ifzq/westock 免费源，配额全留深历史回填）；factor_value 1000次/天滚动窗 | 年付¥2000（因子库），2026-08 续 |
| 2 | akshare + QG代理池1 | 国内 | `.env QG_PROXY_AUTHKEY/1` | ✅ | 余额制（2026-09-03 充值）；🔴裸连必封 IP；**2026-11-16 减负：中债/上金所/sina 系 6 recipe 已切 proxy:direct（非东财主机不封数据中心 IP，实测直连稳定），performance_express 因消费者暂停降频 manual——日度代理调用 ~80%↓，现仅剩东财系刚需（us_stock_spot/china_bond_index/研报系）走池** | 余额耗尽时充值 |
| 3 | QG代理池2 (kevinsu) | 国内 | `.env QG_PROXY_AUTHKEY_2` | ❌欠费 | BALANCE_INSUFFICIENT（09-03） | 闲置 fallback，不修 |
| 4 | 妙想 MCP（东财） | 国内 | `.env EM_API_KEY` = CC `mx-ds-mcp` | ✅ | 官方认证 API | 长效，无到期 |
| 4.5 | 中信前瞻AI产业链库 | 国内 | https://h97m3ln5.qwenwork.host/api/chart-data (公开JSON端点,机构订阅站) → SZ81 fetch_citic_ai_chain.py | ✅ | 每日1GET(源~11:49更新,礼节频率);155序列(LLM价格/ARR/GPU租赁/CDS/Z-Spread/期限结构)+长债; fmdata citic_ai_credit/citic_ai_bonds/citic_ai_series_long(155序列长表)/citic_ai_series_meta + 日期化PIT原始存档 + GVRM ai_credit键 | 免费(订阅站) |
| 5 | 申万金工 MCP | 国内 | `.env SW_MCP_URL/R63G**` = CC `sw-goldeneng` | ✅ | 个人邀请码授权；礼貌限速 0.15s/call；120行/响应；get_factor_value 已下线→用 get_factor_series。看板数据集(2026-11-11)：sw_tech_screen/sw_pattern_map(形态温度,D+1)/sw_macro_snapshot(利率商品,~60日窗) 三个 fetcher cron 19:05-19:10 落 store/data/sw/ | 邀请码长效 |
| 6 | 腾讯自选股 StockBuddy | 国内+港美 | `.env WESTOCK_*`（fskey 会话） | ✅ 今日新接 | A股实时/美股延盘；token ~100h 无 refresh_token | **fskey 自动重铸**（cron 08:57）；🔴fskey 失效才需人工重授权 |
| 7 | 天软 cjpy (TS-OPI) | 国内 | `~/.cjpy/config.json token` | ✅ | IP 白名单制，改出口须重启 | token 长效 |
| 8 | FRED | 海外 | `.env FRED_API_KEY`（SZ91 持有，SSH 注入 HK43 执行） | ✅ | 免费近乎无限 | 无 |
| 9 | Alpha Vantage | 海外 | `.env AV_API_KEY` | ✅ | 🔴免费 25 次/天（av_us_earnings 日耗 1-2 次） | 无 |
| 10 | FMP | 海外 | `.env FMP_API_KEY` | ✅（/stable 端点） | free tier；🔴v3 端点已全线退役，只能用 `/stable/*`（fetch_fmp.py 已就位） | 无 |
| 11 | NewsAPI | 海外 | `.env NEWSAPI_KEY`（HK43 执行） | ✅ | 免费 100 次/天（现用 3 次/天）；top-headheads 对免费档被拦属正常，走 /v2/everything | 无 |
| 12 | 腾讯公开行情 (qt/ifzq.gtimg) | 港美 | 无凭据 | ✅ | qt.gtimg.cn 实时 quote（GBK）；ifzq K线（**HK 日线增量主源**：`hkfqkline/get?param=hkXXXXX,day,,,N,qfq`，amount 字段🔴万港元×1e4，深度 640 根/次可按日期段翻页）；美股延盘 | 无 |
| 13 | gfclaw COS 数仓 (广发金工·安宁宁团队) | 国内+港美 | `/data/gfclaw/.gfclaw_cos_config.json`（腾讯云子账号 sutianxing_MUWjwz，只读桶 gfclaw-1255885899） | ✅ 2026-11-15 新接 | 91 表 Parquet 全量 25.8G 落 `/data/gfclaw/local_market_data/`（1990→今月度分区）；服务端每天 18:00/19:40/22:00 三波发布、多 flavor 共用 latest.json 指针互踩→cron 定 23:10；查询走 fmdata `/gfclaw/tables`+`/gfclaw/query`（🔴回测必须 `frozen_only=true`，当月 current 层每日重刷禁入回测终值） | 长效密钥（腾讯云子账号 sutianxing_MUWjwz，失效找苏天醒重发；技术支持=广发金工团队） |

**HK 日线换源记录（2026-11-09）**：`tencent_hk_daily`(00700)/`tencent_hk_daily_09988` 增量已从 tushare hk_daily（5次/天配额）迁至 **ifzq 主源 + westock fq=bfq 备源**（`scripts/fetch_hk_daily_incremental.py`）。OHLC/vol/amount 与 tushare 逐字段实测精确吻合；qfq 是活锚会随分红重写历史 → **只追加新完成的交易日棒**（当日棒 16:30 HKT 前不落库）；westock bfq 深度仅 5 年，深历史回填仍用 tushare 配额（recipe .bak.20261109-tushare 保留）。pre_close 为前收盘推算，除净日与官方差除息额（研究可接受）。

**HK 供给归拢（2026-11-09 晚，GVRM 数据层加固）**：四标的（00700/09988/02800/09618）三表（tencent_hk_daily / _09988 / _batch）统一由 `fetch_hk_daily_incremental.py` 供给（原三路并行：ak+tushare兜底 / ak批量 / tushare 直连全废）。cron 归拢 07:10+16:32 两针（16:32 收当日完成棒，fmdata 当晚可用；GVRM sync 15:40 在 HK 收盘前跑，HK 腿 T-1 属结构性）。守门：取回<10 棒或源端落后表内拒写；缺口自愈实测通过（02800 挖 3 天洞自动补齐且与生产值一致）。tushare hk_daily 日常用量=0。ak 路保留 `_ak` manual recipe 作应急。
| 13 | HK43 remote 执行通道 | 海外 | ssh hk43 + `~/.openclaw/skills/{fred,news}-data` | ✅ | — | 无 |
| 14 | 东吴宏观 Macro Fans MCP | 国内 | `.env SOOCHOW_API_KEY`（Bearer=邮箱） = CC `soochow-macro` | ✅ 11-09 新接 | 🔴429 限速严格（无间隔连打~15次即触发，脚本已内置 1s/call+指数退避）；hist_section top_n≤500；rank_etf_flow limit≤100；原始Excel窗口仅10个交易日 | 邮箱 Key 长效；失效找东吴对口销售 |
| 15 | 华泰智研 MCP | 国内 | `.env HTSC_MCP_URL/HTSC_APP_KEY`（无状态 streamable-http, header HTSC_APP_KEY） | ✅ 11-14 新接 | 限速未知（脚本内置 1s/call+429退避60s×3）；get_quant_factor 区间跨度>~7天=暂无数据（逐月[月末-5天,月末]取月末截面）；query_table_data page_size≤500；单日查询须真实交易日（周末/节假日=暂无数据） | sk_ Key 长效 |

**结论：当前没有任何 token 需要续期或重申请。**

**gfclaw COS 数仓接入记录（2026-11-15）**：广发金工（安宁宁团队）出品的机构级 Parquet 数仓（授权：国寿安保 苏天醒 只读子账号），COS 桶 `gfclaw-1255885899`(ap-guangzhou)。91 表 25.8G 全历史（1990→）落 `/data/gfclaw/local_market_data/`：A股/港股/美股日行情+衍生+估值、财务三表核心+8张附注明细+TTM、**一致预期三表（snap/rolling_daily/盈利预测事件）+评级快照**、**机构持仓明细 eq_institution_holding_detail(1998→)**、**调研 corp_ir_qa/activity/participant(2010→)**、两融、港股通资金流、自由流通权重日表、SW/CITICS L2 行业指数、ETF净值/转债行情/金价的月度分区。接入方式与 ts202 同款"本地 Parquet + fmdata 查询层"：`GET /gfclaw/tables`（字典驱动清单+中文字段含义）、`GET /gfclaw/query`（table/start/end/ticker_id/columns/limit，ticker_id 即 `600519.SH` 风格代码）。recipe `gfclaw_warehouse`（catalog 可搜"机构持仓/调研/一致预期"）。同步：客户端 `/data/gfclaw/scripts/sync_from_cos.py`（上游 skill，etag/sha256 去重增量），cron 23:10 `daily_sync.sh`（flock 防并发+失败飞书告警+字典CSV导出）。🔴三坑：①当月 `current/` 分区整月每日重刷，**回测必须 `frozen_only=true`**（history 分区不可变）；②latest.json 被多 flavor（core/institution/ir/management/consensus）互踩——同 key etag 全一致、任一 flavor 即全量数据，跟着 latest 走即可，churn 上限 ~200MB；③`--start-date 1990-01-01` 属非默认 scope，客户端永不标 release 已同步属预期，监控看 `logs/daily_sync_*.log` 尾部而非 _sync_status.parquet。

## 一.5、券商数据源速查（2026-11-16 整理，问"哪家券商有什么"先看这张表）

| 券商 | 通道形态 | 规模 | 双层用法（结构化→fmdata / 研究类→agent直连） | 因子库贡献 | 🔴关键限制 | 凭据 |
|------|---------|------|----------------------------------------------|-----------|-----------|------|
| **申万宏源金工** | MCP `sw-goldeneng`（CC user scope） | 53 工具（缠论/形态/相似K线/基金归因/财报深度） | 16 因子月度库 → fmdata `sw_factor_value/sw_factor_series`；技术分析/归因 → agent 直连 | sw16（DEPRECATED 退役）+ **sw2 50**（筹码7独有+raw39+行业轮动4），catalog 现役主力 | 120行/响应上限；T+7 滞后（可超38天）；get_factor_value 已下线改 get_factor_series；PIT 隔月diff未闭环回测须注明 | `.env SW_MCP_URL/CODE` |
| **东吴证券宏观** | MCP `soochow-macro`（CC） | 88 工具（择时/情绪矩阵/资金流/QDII/研报全文/舆情） | 9 数据集 → fmdata（etf_flow/stock_flow/timing_section 等，cron 19:15+07:40）；研报全文/复盘 → agent 直连 | 无（宏观择时类） | 🔴429 限速严（1s/call）；🔴5d/20d 滚动窗服务端重写禁回测；🔴原始 Excel 仅留10交易日，cron 必须跑 | `.env SOOCHOW_*`（Bearer=邮箱） |
| **华泰智研** | MCP `htsc-research-mcp`（CC） | 15 工具（金工因子/资金流/情绪/研报框架/800+数据表） | 4 金工因子+4宏观recipe → fmdata（2026-11-14 接入）；研究框架/数据表查询 → agent 直连 | **htsc 4**（量价融合 IC0.071 最强/综合价值/综合成长/llm_fadt） | 🔴全系非PIT围栏（FactorStore 默认拒绝），2-3 月隔月 diff 后才可能转正，回测禁用 | `.env HTSC_*`（AppKey+Header） |
| **广发金工** | ⚠️非MCP——COS Parquet 数仓（gfclaw，2026-11-15 接入） | 91 表 26G（1990→） | 全量本地 `/data/gfclaw` + fmdata `/gfclaw/query`；美股K线研究主源+EM备选降级（11-16） | 无（数仓非因子） | 当月 current 层禁回测（frozen_only）；T+1；美股代码 .O 非 .OQ | `/data/gfclaw/.gfclaw_cos_config.json` |
| **东方财富（互联网券商系）** | MCP `mx-ds-mcp`（妙想，CC/Codex/OC/Hermes 全配） | 11 NL 工具（A/US/HK/指数/基金/债券/宏观/筛选/新闻/公告） | ad-hoc/研究/最新价/研报公告 → agent 直连妙想；结构化/回测 → fmdata recipe（不变） | 无 | 🔴NL 接口非确定性+无历史 as-of，**禁用于回测**（铁律） | `.env EM_API_KEY` |

> 易混淆的非券商源（别计入）：腾讯自选股 westock（互联网）、天软 cjpy（数据商）、讯息之桥（第三方知识库）。
> 三家纯券商 MCP 的接入详情各有 memory：sw-mcp-fmdata-integration / soochow-macro-mcp-integration / htsc-mcp-evaluation-20261114；广发=gfclaw-cos-warehouse-integration-20261115。

## 二、按用途路由（什么需求用什么源）

| 需求 | 首选 | 次选 | 禁止 |
|------|------|------|------|
| A股 EOD/财务/因子（回测级） | fmdata recipe（tushare/akshare） | — | 实时源回测 |
| A股盘中实时价/涨跌 | **westock_query**（data_quote） | qt.gtimg.cn 直调 | — |
| 一致预期（结构化/回测） | fmdata consensus_*（月度面板） | — | 妙想/westock 回测 |
| 一致预期（ad-hoc 前瞻） | westock data_consensus / 妙想 | — | — |
| 美股 EOD/财报/日历 | **研究/回测主源 = gfclaw `eq_us_quote_daily`**（2026-11-16 定：3.3万只全市场、2000-01→、原始价+restated_ratio+除息修正prior_close，AAPL 4日收益率与EM实测差0.4-6.0bp；`/gfclaw/query?table=eq_us_quote_daily&ticker_id=AAPL.O`，🔴代码后缀.O非.OQ；回测frozen_only=true）；**GVRM盘中监控保留 us_equity_kline_em**（东财push2his qfq前复权日线, 36标的=GVRM美股池全覆盖; EOD当日北京午后即有, 15:25拉→15:40sync T+0供给gfclaw给不了; cron 06:15/15:35 全量重拉; 🔴qfq深历史≤0伪影已过滤, 深度不足80%闸门的标的自动留cboe如AMD；**2026-11-16 起带 gfclaw 备选降级**：cron wrapper v3 在 EM 重跑仍 rc=2 时抓 FAILED_SYMBOLS 交 `fetch_us_equity_kline_fallback.py` 收益重定基补棒（36/36 代码映射实证含 ETF .P 系、全池砍5补5回归中位 2.4bp/p95 77bp、实弹写路径 5.5-6.7bp、护栏：单日30%收益中止+断档>60根拒补+bootstrap拒做+source=gfclaw_fallback 溯源；🔴gfclaw T+1 → 最新一根可能仍缺属预期，次日 EM 恢复即自愈重锚） | FMP（stable）+ AV；CBOE `cboe_historical`（**GVRM 日线统一源, 2026-11-21 起**：42 标的未复权全史 21万行, fmdata recipe `fetch_cboe_historical.py` + cron 06:09/22:33 周二~六; GVRM 直连退役为手动补数 `--source direct`; 🔴21万行超 /data max_rows=20万, 拉全量须 `?full=true`; 假日幻影/坏bar原样保留, 清洗在 GVRM 侧） | fred/sp500 等（T+1） | — |
| 美债/宏观序列 | FRED（经 HK43） | — | — |
| 港股日线 | tencent_hk_daily（akshare@HK43）；hk_daily 配额 5/天慎用 | westock | — |
| 龙虎榜/资金流/板块/产业链 | westock（82 工具） | 东财代理池 | 裸连东财 |
| 金工月频因子 | sw MCP（get_factor_series） | — | get_factor_value（已下线） |
| 华泰金工因子（月频截面） | **htsc.\***（FactorStore cross_section_any, 3因子已物化 2021-01.., 非PIT围栏 allow_nonpit） | sw2.\* | llm_fadt（覆盖34%未接入, 仅 htsc_pending/ 快照） |
| ETF 逐日申赎/份额变化（全量1715只） | **soochow_etf_flow**（Wind口径独有） | GVRM A股ETF申赎线（口径互补对账） | — |
| 行业资金流×两融联动 | soochow_industry_flow（L1+L2带融资净买入） | tushare moneyflow | — |
| 择时信号（顶底/风险度/评分，10口径） | **soochow_timing_section**（本地冻结截面） | — | 🔴服务端仅49日滚动窗+会重写；回测必须用本地冻结副本 |
| QDII 申购状态/额度变化 | **soochow_qdii_status**（365只独有） | — | — |
| 机构申赎情绪（客群×基金类型矩阵） | soochow_sentiment_matrix（JSONL原始） | — | — |
| 解禁/增减持/再融资（含未来日历） | soochow_weekly_reports | tushare share_float | — |
| 宏观择时模型信号（东吴自产） | soochow_macro_model（月度） | — | 模型文件月更自带历史 |
| 游资复盘（涨停梯队/龙虎榜/监管异动） | **soochow_daily_review**（md全文; 🔴仅留10个交易日窗） | — | GVRM晨会看板; cron 19:15+07:40 必跑断即永久丢 |
| 多资产新闻日览（催化/情绪结构） | **soochow_multi_news**（md全文; 仅10日窗） | — | GVRM晨会看板隔夜层 |
| 择时信号池概览（四口径持仓/到期集中） | soochow_signal_pool（md） | — | GVRM晨会看板 |
| 每日买点共振技术筛选 | **sw_tech_screen**(+meta; cron 19:05) | — | 服务端 limit 钳 20; GVRM晨会看板票池块 |
| A股涨跌停/炸板清单(含连板数) | **ashare_limit_list**(tushare limit_list_d; 17:05+07:45) | — | GVRM晨会看板T-0盘面速览;🔴tushare盘后才有当日数据 |
| 核心指数日度速览(涨跌幅+成交额) | **ashare_index_brief**(上证/深成/创业板/深综) | — | 同上;成交额=上证+深综合计 |
| 中证500/1000 全历史指数日线 | **csi500_index_daily / csi1000_index_daily**(tushare index_daily, 积分内; governance 03:15 夜刷; 2026-11-16 建——起因 GVRM 日线监控消费 sw_index_daily(SW MCP 月刷)最长滞后 30 天; tushare vs SW 重叠 5305 天收盘相对差 0.00000000%) | sw_index_daily(深历史+HSI/SPX/IXIC/N225 保留, 月刷) | — |
| 新闻/资讯 | NewsAPI 3 频道 / westock data_news | — | — |
| 天软独有（per-meeting 等小众表） | cjpy MCP | — | 批量导出走 Python API 非 MCP |

## 三、结构性配额陷阱（历史教训，动前必看）

1. **tushare hk_daily 5次/天**——批量港股日线走 tencent_hk_daily_batch_ak，hk_daily 只做补洞
2. **东财任何接口必须套 QG 代理池**（池1 活/池2 死）；烧穿症状与自愈见 memory qg-proxy-burn-fix
3. **FMP 只认 /stable**；AlphaVantage 25 次/天省着用（av_us_earnings 一次一个 symbol）
4. **sw MCP 120 行/响应上限**；get_factor_value 已于 2026-11-09 下线
5. **westock 美股延盘**（quote 带 delay 标记）；实时语义禁回测/PIT
6. NewsAPI 免费档 top-headlines 会返拦截页，属正常，走 everything
7. **东吴 soochow 三坑**（2026-11-09 实测）：①429 限速严（连打~15次触发，1s/call+20s×n退避已内置 `fetch_soochow_mcp.py`）；②滚动窗口陷阱——Excel/MCP 里 5d/20d 列与 rank_etf_flow 是当期滚动窗，服务端逐日重写，🔴禁回测拼接（逐券大表入库时已剔除，行业/聚合小表保留仅作参照）；③原始 Excel 只留 10 个交易日、择时历史库只留 49 个交易日，**错过即永久失忆**，日度 cron（19:15）+ 空日期标记自愈；④hist_section 指定 fields 后响应只含所列字段（code/name 必须显式列入，否则行全被丢）

## 四、本次盘点排除项（非凭据问题）

- sw 筹码因子 8/9/10 月 NaN = 服务端未发布（上游滞后，静默自愈 cron d91c0b7d 盯着）
- research_export/* 一批 10-25 前的 CSV = 一次性研究导出，非活跃管道
- 135 个数据集 11-05 后有更新，cron 管道整体健康
