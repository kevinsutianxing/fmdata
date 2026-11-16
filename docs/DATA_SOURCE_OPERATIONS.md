# fmdata 数据源运营手册（2026-11-16 立）

> 机制文档：**怎么更新、怎么查询、因子库怎么运转、怎么监控**。
> 数据源清单/凭据/配额/坑 → `~/claude-workspace/docs/data-sources-inventory.md`（单一真值源，含券商速查表）。
> Agent 取数入门 → `docs/AGENT_GUIDE.md`。

## 一、更新机制矩阵（谁在什么时候更新什么）

| 通道 | 覆盖 | 节奏 | 监控 |
|------|------|------|------|
| **治理 Phase 3**（`~/claude-workspace/scripts/fmdata-governance.py --merge`，cc-connect cron 03:15/19:15） | 全部 update_freq=daily/weekly/monthly 的 recipe（tushare/akshare/FMP/AV/FRED 等，~90 日更源） | daily 每夜 / weekly 周末 / monthly 月初1-3日 | 失败单次重试→WARN；日更源由 Phase 4b 兜底 |
| **专属 cron 脚本**（治理故意跳过，见 EXTERNALLY_REFRESHED_BY_CRON） | soochow 9数据集(19:15/07:40/13:40)、HK日线(07:10/16:32)、美股K线EM(06:15/15:25+gfclaw备选)、SW因子快照(09:07自愈+月度4日)、cn看板包(19:35/07:50)、ts202/cons7/sw2月度物化 | 各自 cron | 各脚本自带告警；soochow 族+gfclaw 由 Phase 4b 心跳监测 |
| **COS 同步**（`/data/gfclaw/scripts/daily_sync.sh`，cron 23:10） | gfclaw 91表数仓（etag增量+字典双导出+心跳） | 每日，服务端 18:00/19:40/22:00 三波发完再收 | `.last_success` 心跳 >36h 告警；失败飞书 |
| **on-demand POST /fetch/{name}** | 全部 recipe | agent 按需 | 413/超时返回结构化错误 |
| **静态登记**（source:static / _auto_fetch:false） | 一次性物化的小表 | 不自动 | 无（内容不变） |

**红线**：recipe 注册走 `POST /recipes`（name/source/fetch 必填，agent/remote 源须 X-API-Key 且脚本在 `AGENT_SCRIPT_ALLOWLIST`）；新增日更源必须同步登记 inventory + 可见 SCRIPT_FED_HEARTBEATS（若脚本喂）。

## 二、查询机制矩阵（什么需求走什么路）

| 需求 | 路径 |
|------|------|
| 发现数据 | `GET /catalog`（分类/描述/获取方式）→ `GET /search?q=`（中英文模糊）→ 别直接看 /status |
| 普通 CSV 数据集 | `GET /data/{name}?limit=N`（200k行/10M cell 硬顶） |
| 个股/行业/宏观便捷路由 | `GET /market/stock-daily?code=` `/market/sw-pe` `/macro/cpi` 等 |
| **gfclaw 数仓（91表 Parquet）** | `GET /gfclaw/tables`（清单+中文字段）→ `GET /gfclaw/query?table=&start=&end=&ticker_id=&columns=&limit=&frozen_only=`；🔴回测必须 `frozen_only=true`（当月 current 层整月重刷）；大表必带 start/end 或 ticker_id |
| **单因子取数（HTTP）** | `POST /fetch/factor_query` body `{"params":{"factor_id":"sw.动量","trade_date":"20261030"}}` → `GET /data/factor_query` 长表 |
| **单因子取数（Python，回测级）** | `from fmdata.factor_store import FactorStore; FactorStore().cross_section_any(fid, ym)`——cjpy/htsc 须 `allow_nonpit=True`（默认 PermissionError，落 audit.log）且🔴禁回测 |
| HK43 agent | `ssh sz81 "curl -s http://127.0.0.1:1934/..."`（唯一通道，禁直连） |
| 证券代码校验 | `GET /validate?codes=600519`（硬门，未验证不取数） |

## 三、统一因子库（catalog 419 = ts272 + cjpy70 + sw2 50 + sw16(退役) + cons7 + htsc4）

```
build_factor_catalog.py（生成器，改 catalog 改它勿手改产物；月度+htsc接入时刷新）
  → /data/factor_catalog（目录：每因子 pit_status/口径警告）
  → FactorStore.cross_section_any（命名空间路由: ts./sw./sw2./cons./cjpy./htsc.）
      ├─ PIT 围栏: cjpy/htsc 默认 PermissionError（非PIT服务端可重写历史）
      ├─ ts202 = 付费冻结 parquet（物化至 2026-10-30，base+delta 多文件禁只读单个）
      └─ sw2 低波/反转方向与 v1 相反；sw16 全 DEPRECATED
  → factor_query 门面（HTTP 层，agent 主入口）
```

**回测纪律**：只用冻结快照（ts202 parquet / gfclaw history 分区 / cons 月度面板）；服务端会重写历史的源（cjpy/htsc/妙想/东吴5d20d列）禁入回测终值；PIT 状态查 catalog 的 pit_status 列。

## 四、监控体系（2026-11-15/16 建）

- **Phase 4b 日更审计**（随治理两针）：81 日更源，交易日感知 mtime 门槛（周末/节假日/周一凌晨自动免疫），幂等 fetcher 族（soochow）与脚本源（gfclaw）走生产者心跳；stale 列名单+飞书
- **Phase 4c 因子库日检**：catalog≥410 基线 / 六源各抽1因子 FactorStore 实弹 / PIT 围栏负向测试（cjpy 不带 allow_nonpit 必须拒）/ R26 黄金锚包在场 / factor_query 通路
- **晨检 cron ba086d50**（09:37，LLM 兜底）：复盘 4b/4c+gfclaw 心跳→修复→全绿静默；周一加因子库知识回忆（文档 vs 实际真查）
- 手动巡检：`python3 ~/claude-workspace/scripts/fmdata-governance.py --audit-only`

## 五、2026-11-16 修复记录（review 产出）

1. **factor_query sw 路由日历月末坑**：本地 sw 快照锚日历月末（如 2026-10-31 周六），按"最后交易日 10-30"查返 0 行（一年 ~1/5 月份踩空）→ 精确日匹配空则月粒度回退（sw 本就是月度快照，语义正确）
2. **ts 路由多代码坑**：tushare factor_value 拒绝逗号多代码整单返空 → facade 拆分逐查合并（600519.SH,000001.SZ 实测 2 行）
3. 同日早前（见 git log）：gfclaw 接入、美股K线备选降级、registry 指针烂修复（daily_basic/etf_monthly_close）、csi_cb_index_daily 死 recipe 禁用
