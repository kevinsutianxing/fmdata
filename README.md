# fmdata — Unified Financial Data Middleware

A lightweight FastAPI service that acts as a **data governance layer** between AI agents and financial data APIs (tushare, akshare, FRED, etc.).

**One API. Cached. Validated. Proxy-aware.**

## Why fmdata?

AI agents calling `tushare` or `akshare` directly cause three problems:

1. **IP bans** — akshare calls eastmoney/sina directly; repeated calls from the same IP get blocked. fmdata routes through a rotating QG proxy pool.
2. **Duplicate API calls** — 5 agents requesting the same stock data = 5× API quota burned. fmdata caches everything.
3. **No validation** — agents pass wrong stock codes (603377≠宏和科技) and produce garbage reports. fmdata enforces a `/validate` gate.

## Architecture

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Claude   │     │  Hermes  │     │  Codex   │
│  (SZ81)   │     │  (SZ81)  │     │  (HK43)  │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     └────────────────┼────────────────┘
                      │ HTTP (port 1934)
                 ┌────▼────┐
                 │  fmdata  │  FastAPI middleware
                 └────┬────┘
                      │
          ┌───────────┼───────────┐
          │           │           │
     ┌────▼────┐ ┌───▼────┐ ┌───▼────┐
     │ tushare │ │akshare │ │  FRED  │  (via HK43 SSH)
     └─────────┘ └────────┘ └────────┘
          │           │
          │     ┌─────▼─────┐
          │     │ QG Proxy  │  rotating IP pool
          │     └───────────┘
          │
    ┌─────▼──────┐
    │ CSV Cache  │  ~/fmdata/store/
    │ (3.9 GB)   │
    └────────────┘
```

## Quick Start

```bash
# Install
cd fmdata
pip install --user -e .

# Set API tokens
export TUSHARE_TOKEN="your_token"
export FRED_API_KEY="your_key"      # optional
export QG_PROXY_AUTHKEY="your_key"  # optional, for akshare proxy

# Start server
fmdata serve --port 1934

# Or with systemd
sudo cp fmdata.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fmdata
```

## API Reference

### Status & Health

```bash
# Service health
curl http://127.0.0.1:1934/health

# Dataset summary (all datasets with row counts, categories)
curl http://127.0.0.1:1934/status?summary=true

# Data quality report (empty/stale/missing datasets)
curl http://127.0.0.1:1934/health/data
```

### Reference Data

```bash
# Stock list with industry classification
curl http://127.0.0.1:1934/reference/stocks
curl http://127.0.0.1:1934/reference/stocks?industry=银行

# Trade calendar
curl http://127.0.0.1:1934/reference/calendar
curl http://127.0.0.1:1934/reference/last-trade-day

# Industry mapping
curl http://127.0.0.1:1934/reference/industries
curl http://127.0.0.1:1934/reference/industry-map?ts_code=000001.SZ
```

### Stock Code Validation (HARD GATE)

```bash
# Validate stock codes BEFORE any data pull
curl "http://127.0.0.1:1934/validate?codes=000001.SZ,600519.SH,603377.SH"
# → returns valid=true/false + warnings for known mistaken codes

# Auto-validate on data endpoints
curl "http://127.0.0.1:1934/market/stock-daily?code=002594&validate_first=true"

# Reverse lookup: name → code
curl "http://127.0.0.1:1934/validate?name=比亚迪"
```

### Market Data

```bash
# Individual stock OHLCV (auto-fetches from tushare/akshare on first call)
curl "http://127.0.0.1:1934/market/stock-daily?code=002594"
curl "http://127.0.0.1:1934/market/stock-daily?code=002594&start_date=20240101&source=akshare"

# Daily price matrix (tech stocks)
curl http://127.0.0.1:1934/market/daily-matrix

# Shenwan industry indices
curl http://127.0.0.1:1934/market/sw-close        # L1 industry daily close
curl http://127.0.0.1:1934/market/sw-pe           # PE history
curl http://127.0.0.1:1934/market/sw-pb           # PB history

# Benchmark
curl http://127.0.0.1:1934/market/hs300?freq=daily

# North-bound capital flow
curl http://127.0.0.1:1934/market/north-money

# Fundamentals
curl "http://127.0.0.1:1934/market/fundamentals?period=20260331"
curl http://127.0.0.1:1934/market/fundamentals-extended

# Factor matrix (industry rotation)
curl http://127.0.0.1:1934/market/factor-matrix
```

### Macro Data

```bash
curl http://127.0.0.1:1934/macro/cpi
curl http://127.0.0.1:1934/macro/ppi
curl http://127.0.0.1:1934/macro/pmi
```

### Recipe Management (On-Demand Data)

fmdata uses a **recipe system** — YAML configs that describe how to fetch each dataset. Agents can register new data sources without touching Python code.

```bash
# List all recipes
curl http://127.0.0.1:1934/recipes

# View a recipe
curl http://127.0.0.1:1934/recipes/daily_basic

# Register a new recipe (auto-fetches on creation)
curl -s -X POST http://127.0.0.1:1934/recipes \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_dataset",
    "source": "tushare",
    "fetch": {"func": "daily", "params": {"ts_code": "300750.SZ"}}
  }'

# Fetch/refresh a dataset
curl -X POST http://127.0.0.1:1934/fetch/my_dataset

# Agent guide
curl http://127.0.0.1:1934/how-to-add
```

### Generic Data Access

```bash
# Access any dataset by name (works for all categories)
curl http://127.0.0.1:1934/data/cpi
curl http://127.0.0.1:1934/data/sw_first_level_close
curl http://127.0.0.1:1934/data/analyst_consensus
```

## Python API

```python
import fmdata

# Reference
stocks = fmdata.stock_list()              # All A-share stocks
tech = fmdata.tech_stock_list()           # Tech stock subset
industries = fmdata.industry_list()       # SW L1 industries
cal = fmdata.trade_calendar()             # Trading calendar
today = fmdata.last_trade_day()           # Most recent trade day

# Market
prices = fmdata.daily_matrix()            # Tech stock price matrix
sw = fmdata.sw_industry_close()           # SW industry close prices
pe = fmdata.sw_pe_history()               # SW industry PE history
north = fmdata.north_money()              # North-bound flow
fina = fmdata.stock_fina("20260331")      # Quarterly financials
factors = fmdata.factor_matrix()          # Factor matrix

# Macro
cpi = fmdata.macro("cpi")
macro = fmdata.macro_monthly()

# Registry
datasets = fmdata.list_datasets()         # All datasets with metadata
```

## Recipe System

Recipes are YAML files in `store/recipes/`. They describe how to fetch each dataset:

```yaml
# store/recipes/sw_pe_history.yaml
name: sw_pe_history
category: market
description: Shenwan L1 industry PE history
file: market/sw_pe_history.csv
source: akshare
update_freq: daily
fetch:
  func: sw_index_daily_em
  params:
    symbol: "801010"
  date_col: date
  incremental: true
  proxy: qg
```

**Supported sources:**

| Source | Description | Auth Required |
|--------|-------------|---------------|
| `tushare` | Tushare Pro API calls | `TUSHARE_TOKEN` env var |
| `akshare` | Akshare functions (auto-proxied) | `QG_PROXY_AUTHKEY` for proxy |
| `agent` | Local Python scripts (allowlisted) | None |
| `remote` | SSH to remote host (e.g. HK43 for FRED) | `FMDATA_ADMIN_KEY` header |

## Validation System

fmdata enforces **stock code validation** as a hard gate for all agents:

```bash
# Agent protocol:
# 1. Validate codes BEFORE any data pull
curl "http://127.0.0.1:1934/validate?codes=603377.SH"
# → {"valid": true, "warnings": [{"code": "603377.SH", "actual": "ST东时",
#     "commonly_mistaken_as": "宏和科技"}]}

# 2. Use validate_first=true on data endpoints
curl "http://127.0.0.1:1934/market/stock-daily?code=002594&validate_first=true"
# → 422 if invalid, data if valid
```

Known mistaken codes are tracked in `server.py:KNOWN_ERRORS`:
- 603377 → ST东时 (not 宏和科技)
- 688217 → 睿昂基因 (not 铜冠铜箔)
- 688033 → *ST天宜 (not 天承科技)

## Proxy Architecture

fmdata solves the akshare IP-ban problem with a **dual QG proxy pool**:

```
fmdata → QG Pool 1 (primary, env-configured)
      → QG Pool 2 (kevinsu fallback)
      → rotating IP per request
      → eastmoney push2his subdomain (less rate-limited than push2)
      → exponential backoff (0.5s + 0.15s × attempt)
```

The proxy logic is in `recipe_fetcher.py:eastmoney_get()`. Recipes using akshare automatically get proxied; tushare calls go direct.

## CLI

```bash
fmdata status              # List all datasets with row counts
fmdata status cpi          # Show specific dataset
fmdata recipes             # List all recipes
fmdata recipes sw_pe_history  # Show specific recipe
fmdata fetch cpi           # Fetch a dataset using its recipe
fmdata fetch stale         # Fetch all stale datasets
fmdata init                # Rebuild registry from store/
fmdata serve               # Start HTTP server
```

## Project Structure

```
fmdata/
├── fmdata/                  # Core package
│   ├── __init__.py          # Public API exports
│   ├── server.py            # FastAPI HTTP server (all routes)
│   ├── config.py            # Paths, tokens, rate limits
│   ├── registry.py          # Dataset discovery, recipe loading, CSV scanning
│   ├── recipe_fetcher.py    # Recipe execution engine (tushare/akshare/agent/remote)
│   ├── fetcher.py           # TushareFetcher, AkshareFetcher with rate limiting + cache
│   ├── reference.py         # Trade calendar, stock list, industry mapping
│   ├── market.py            # Daily prices, SW indices, fundamentals, factors
│   ├── macro.py             # CPI, PPI, PMI, money supply, etc.
│   └── cli.py               # Command-line interface
├── scripts/                 # Refresh/fetch production scripts
├── store/
│   ├── recipes/             # Recipe YAML files (one per dataset)
│   ├── scripts/             # Store-level fetch scripts
│   └── cjpy_dict/           # CJPy (长江金工) data dictionary
├── tests/                   # pytest test suite
├── setup.py                 # Package install config
├── fmdata.service           # systemd unit file
└── README.md
```

## Data Storage

fmdata stores all data as CSV files under `~/fmdata/store/`:

```
store/
├── reference/     Stock lists, trade calendars, industry mappings
├── market/        Daily prices, SW indices, PE/PB, north money
├── macro/         CPI, PPI, PMI, credit, money supply
├── fundamentals/  Quarterly financial statements (stock_fina/ by period)
├── factors/       Factor matrices for industry rotation
├── overseas/      VIX, S&P 500, US Treasury, USD index
├── strategy/      Semiannual investment system output
├── recipes/       Recipe YAML files ← THIS is in the repo
└── cjpy_dict/     CJPy field dictionary ← THIS is in the repo
```

The registry (`registry.json`) is auto-generated from the store directory and recipes — it's not committed to git.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TUSHARE_TOKEN` | Yes | Tushare Pro API token |
| `FMDATA_DIR` | No | Base directory (default: `~/fmdata`) |
| `FRED_API_KEY` | For overseas | FRED API key |
| `QG_PROXY_AUTHKEY` | For akshare | QG proxy pool auth key |
| `QG_PROXY_AUTHPWD` | For akshare | QG proxy pool auth password |
| `FMDATA_ADMIN_KEY` | For agent/remote recipes | API key for mutating endpoints |

## Adapting to Your Environment

The repo uses `FMDATA_DIR` env var (default: `~/fmdata`) for the main package, but some production scripts in `scripts/` and `store/scripts/` have hardcoded paths to `/home/ubuntu/fmdata/store/`. If deploying on a different machine:

```bash
# Set the base directory
export FMDATA_DIR=/path/to/your/fmdata

# Update hardcoded paths in scripts (one-time)
cd scripts && store/scripts
sed -i 's|/home/ubuntu/fmdata|$FMDATA_DIR|g' *.py
```

## License

MIT
