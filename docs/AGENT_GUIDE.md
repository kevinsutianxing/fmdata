# fmdata Agent 使用指南

> 给所有 agent（CC / OC / Hermes / Codex / DeerFlow）的数据获取操作手册。
> 服务地址：SZ81 `http://127.0.0.1:1934`。HK43 走 SSH：`ssh sz81 "curl -s http://127.0.0.1:1934/..."`。

## 0. 铁律

1. **需要金融数据先查 fmdata**。不要 `import akshare/tushare` 裸连（必被封 IP）。
2. **先发现，再取数**。用 `/catalog` 或 `/search` 定位数据，不要在 `/status` 的 1.4 万条里硬找。
3. **缺数据先注册 recipe**，不要绕过 fmdata 自己拉（见 §5）。
4. **涉及股票代码先 `/validate`**（见 §4），避免代码记混（603377≠宏和科技，是 ST东时）。

---

## 1. 发现数据（最重要）

`/status` 返回 13893 条，其中 13418 是个股代码副本（基金净值按 code 拆），噪音大。**别直接看 `/status`**，用下面两个端点：

### `GET /catalog` — 数据目录

精选 ~130 个有意义数据集（按类别分组，噪音默认隐藏）。每条带描述 + 获取方式。

```bash
curl -s http://127.0.0.1:1934/catalog                     # 全量目录（~130 个）
curl -s "http://127.0.0.1:1934/catalog?category=macro"    # 按类别筛
curl -s "http://127.0.0.1:1934/catalog?q=consensus"       # 子串过滤
curl -s "http://127.0.0.1:1934/catalog?include=dated"     # 展开日期快照（默认隐藏）
```

每条返回：
```json
{
  "name": "analyst_consensus",
  "description": "A股分析师一致预期：当年/未来3年EPS预测、机构评级数量...",
  "category": "fundamentals",
  "rows": 2764,
  "has_recipe": true,
  "update_freq": "weekly",
  "fetch_method": "recipe (auto-refresh)",
  "endpoint": "/data/analyst_consensus"
}
```

`fetch_method` 三种值，告诉你怎么拿数据：
- `recipe (auto-refresh)` — cron 自动刷新，直接打 endpoint 取最新
- `on-demand POST /fetch/{name}` — 按需触发抓取（见 §3）
- `static file` — 没 recipe，数据是手工脚本生成的，不能通过 API 刷新

类别速查：
| category | 内容 | 典型数据集 |
|----------|------|-----------|
| `macro` | 宏观经济（CPI/PPI/PMI/利率/货币） | cpi, ppi, pmi, shibor, lpr, money_supply |
| `market` | 市场行情（个股/指数/行业/资金流） | daily_basic, sw_daily_v2, north_money |
| `fundamentals` | 基本面（财报/一致预期/评级） | analyst_consensus, actual_financials |
| `reference` | 参考数据（股票列表/行业/日历） | stock_list, trade_calendar, sw_industry_list |
| `overseas` | 海外（VIX/原油/美债/新闻） | vix, crude_oil_wti, us_10y_treasury, news_* |
| `factors` | 因子矩阵（行业轮动） | factor_matrix_unified, factor_matrix_v13 |

### `GET /search?q=X` — 模糊搜索

不知道数据集叫啥时用。中英文都行，ranked 返回 top 30。

```bash
curl -s "http://127.0.0.1:1934/search?q=一致预期"     # → analyst_consensus
curl -s "http://127.0.0.1:1934/search?q=限售解禁"     # → restricted_release
curl -s "http://127.0.0.1:1934/search?q=龙虎榜"       # → lhb_daily
curl -s "http://127.0.0.1:1934/search?q=美元指数"     # → usd_index
curl -s "http://127.0.0.1:1934/search?q=treasury"     # → us_10y_treasury, cgb_yield_*
```

返回每个匹配的 `name / description / endpoint / fetch_method / score`。

> **已知限制**：搜索是整体子串匹配。`沪深300权重`（无空格）搜不到「沪深300历史成分权重」。用更短的关键词（`沪深300` 或 `权重`）或换 `/catalog?category=market` 浏览。

---

## 2. 取数据

拿到数据集名后，用 `/data/{name}` 或专用语义端点取。

### 通用端点（所有数据集都通）

```bash
curl -s http://127.0.0.1:1934/data/cpi                          # 通用取数
curl -s http://127.0.0.1:1934/data/analyst_consensus            # 分析师一致预期
curl -s http://127.0.0.1:1934/data/sw_daily_v2                  # 申万行业日线
```

### 语义端点（更直观，参数化）

```bash
curl -s "http://127.0.0.1:1934/market/stock-daily?code=002594"        # 个股日线（没有会自动从 tushare 拉）
curl -s "http://127.0.0.1:1934/market/stock-daily?code=002594&start=20260101&end=20260630"
curl -s http://127.0.0.1:1934/market/daily-matrix                    # 日线矩阵
curl -s http://127.0.0.1:1934/market/sw-close                        # 申万行业收盘
curl -s "http://127.0.0.1:1934/market/fundamentals?period=20260331"  # 季度财报
curl -s http://127.0.0.1:1934/macro/cpi                              # 宏观（= /data/cpi 的别名）
curl -s http://127.0.0.1:1934/reference/stocks                       # 全 A 股票列表
curl -s "http://127.0.0.1:1934/reference/stocks?industry=银行"       # 按行业筛
curl -s http://127.0.0.1:1934/reference/last-trade-day               # 最近交易日
curl -s "http://127.0.0.1:1934/reference/calendar?date=20260702"     # 某日是否交易日
```

### Python API（仅 SZ81，HK43 无 fmdata 包）

```python
import fmdata
stocks = fmdata.stock_list()              # 全 A 股
sw = fmdata.sw_industry_close()           # 申万行业收盘
fina = fmdata.stock_fina("20260331")      # 季度财报
cpi = fmdata.macro("cpi")                 # 宏观
ltd = fmdata.last_trade_day()             # 最近交易日
```

完整函数列表见 `fmdata/__init__.py`。

---

## 3. 增量获取 / 按需刷新

数据集 stale（过期）或没数据时，按需触发抓取：

```bash
# 刷新单个数据集（按 recipe 定义抓取，支持增量）
curl -s -X POST http://127.0.0.1:1934/fetch/cpi \
  -H "X-API-Key: $FMDATA_ADMIN_KEY"

# 刷新所有 stale 数据集（有 recipe 但数据旧的）
curl -s -X POST "http://127.0.0.1:1934/fetch/stale?max_age_hours=24" \
  -H "X-API-Key: $FMDATA_ADMIN_KEY"
```

`FMDATA_ADMIN_KEY` 在 `~/fmdata/.env`（SZ81）或通过 SSH（HK43）。普通取数（`/data/*`）**不需要** key，只有 POST /fetch 需要。

recipe 的增量行为在 recipe YAML 的 `fetch.incremental` 字段定义（true = 只拉新增，false = 全量重拉）。

---

## 4. 股票代码校验（硬门）

涉及股票/行业/指数代码时，**先 validate**，避免代码记混产出错误报告。

```bash
curl -s "http://127.0.0.1:1934/validate?codes=600519"             # 单个
curl -s "http://127.0.0.1:1934/validate?codes=000001.SZ,600519.SH" # 多个
curl -s "http://127.0.0.1:1934/validate?name=比亚迪"              # 反查（名称→代码）

# 数据端点自动校验（推荐）
curl -s "http://127.0.0.1:1934/market/stock-daily?code=002594&validate_first=true"
```

返回 `{"valid": true/false, "results": [...], "warnings": [...]}`。已知易错代码：
- `603377` → ST东时（不是宏和科技）
- `688217` → 睿昂基因（不是铜冠铜箔）
- `688033` → *ST天宜（不是天承科技）

> 注意：`/validate` 对无效代码返 **422**（携带 `{valid:false}` 结构化响应，不是请求格式错）。消费端应读 body 而非当异常抛。

---

## 5. 缺数据 → 注册 recipe

fmdata 没你要的数据时，**注册 recipe 而不是绕过**（注册后自动抓取 + 进 registry + 所有 agent 可用）。

```bash
# 查注册指南（完整 schema + 示例）
curl -s http://127.0.0.1:1934/how-to-add

# 注册新数据集（POST 后自动触发首次抓取）
curl -s -X POST http://127.0.0.1:1934/recipes \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $FMDATA_ADMIN_KEY" \
  -d '{
    "name": "my_new_dataset",
    "category": "market",
    "description": "数据集说明",
    "source": "tushare",
    "update_freq": "daily",
    "fetch": {"func": "daily", "params": {"ts_code": "300750.SZ"}}
  }'

# 之后取数
curl -s http://127.0.0.1:1934/data/my_new_dataset
```

支持的 source：`tushare` / `akshare`（自动套 QG 代理池）/ `agent`（本地脚本）/ `remote`（SSH 到 HK43 取 FRED 等）。

---

## 6. Research Snapshot API（研究可复现性，进阶）

跑回测、写研报、需要**可复现数据快照**（带 content hash + manifest）时用。日常取数**不要**用这个，用 `/data/*` 就够。

```bash
# 健康检查（无需 auth）
curl -s http://127.0.0.1:1934/research/health

# 创建不可变快照（带 manifest + hash，状态 = PENDING，等外部 gate 验证）
curl -s -X POST http://127.0.0.1:1934/research/snapshots \
  -H "X-Research-Key: $FMDATA_RESEARCH_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "analyst_consensus",
    "as_of": "2026-07-02",
    "parameters": {},
    "fields": [],
    "expected_semantics": {}
  }'

# 取快照 manifest / data / raw
curl -s -H "X-Research-Key: $KEY" http://127.0.0.1:1934/research/snapshots/{id}/manifest
curl -s -H "X-Research-Key: $KEY" http://127.0.0.1:1934/research/snapshots/{id}/data
```

**核心理念**：fmdata 永不把自己的输出标记为 VALIDATED。snapshot 创建后状态 = PENDING，由**外部研究 gate** 决定是否通过。这是 Maker≠Checker 在数据层的落地。

`FMDATA_RESEARCH_KEY` 或 `FMDATA_ADMIN_KEY` 都能认证 research 端点。详见 `docs/RESEARCH_SNAPSHOT_API.md`。

---

## 7. HK43 agent 访问

HK43 没有本地 fmdata，**必须走 SSH**：

```bash
# HK43 上的 codex/hermes/oc agent
ssh sz81 "curl -s http://127.0.0.1:1934/catalog"
ssh sz81 "curl -s 'http://127.0.0.1:1934/search?q=一致预期'"
ssh sz81 "curl -s 'http://127.0.0.1:1934/data/analyst_consensus'"
```

HK43 **不能** `import fmdata`（包不在 HK43）。

海外数据（VIX/原油/美债/FRED）由 HK43 反向提供给 SZ81 fmdata（recipe `host: hk43`），但消费方仍从 SZ81 fmdata 取。

---

## 8. 常见任务速查

| 我想... | 怎么做 |
|--------|--------|
| 看 fmdata 有什么数据 | `/catalog` |
| 搜「分析师预期」相关 | `/search?q=分析师` |
| 取茅台日线 | `/market/stock-daily?code=600519` |
| 校验代码 002594 是不是比亚迪 | `/validate?codes=002594` |
| 取申万行业 PE 历史 | `/search?q=行业PE` → `/market/sw-pe` |
| 刷新过期的一致预期 | `POST /fetch/analyst_consensus` |
| 知道某日是不是交易日 | `/reference/calendar?date=20261001` |
| 注册 fmdata 没有的新数据 | `POST /recipes`（见 §5） |
| 给回测留可复现数据快照 | `POST /research/snapshots`（见 §6） |

---

## 9. 排障

| 症状 | 原因 / 处理 |
|------|------------|
| 连不上 1934 | `systemctl --user status fmdata`（SZ81）；HK43 检查 SSH 通不通 |
| `/data/X` 返 404 | `/catalog` 确认名字；可能数据集没建（`POST /recipes`）或文件没生成（`POST /fetch/X`） |
| `/validate` 返 422 | 不是 bug，是代码无效（读 body 的 `valid:false`） |
| akshare 类 recipe 抓取失败 | 代理池问题，看 `~/fmdata/.env` 的 `QG_PROXY_AUTHKEY` / `QG_PROXY_AUTHKEY_2` |
| 海外数据陈旧 | recipe 走 HK43，查 HK43 的 fred_cli.py + SSH 连通 |

---

*本指南随 fmdata 仓库版本化（`docs/AGENT_GUIDE.md`）。有改进提 PR 到 `kevinsutianxing/fmdata`。*
