"""FastAPI HTTP server for fmdata."""
import json
import logging
import math
import os
import re
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from fmdata import config
from fmdata.registry import list_datasets, load_recipe, load_all_recipes

logger = logging.getLogger("fmdata.server")
app = FastAPI(title="fmdata", version="0.3.0")

# Admin API key for mutating endpoints (source: agent/remote)
ADMIN_KEY = os.environ.get("FMDATA_ADMIN_KEY", "")

# Recipe name validation: only alphanumeric, underscore, hyphen
RECIPE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

SAFE_SOURCES = {"tushare", "akshare"}
DANGEROUS_SOURCES = {"agent", "remote"}


def _check_admin_key(request: Request) -> Optional[JSONResponse]:
    """Return error response if admin key is required but missing/invalid."""
    if not ADMIN_KEY:
        return JSONResponse(status_code=503, content={
            "error": "admin_not_configured",
            "message": "FMDATA_ADMIN_KEY not set — admin operations disabled",
        })
    provided = request.headers.get("X-API-Key", "")
    if provided != ADMIN_KEY:
        return JSONResponse(status_code=403, content={
            "error": "forbidden",
            "message": "valid X-API-Key required for this operation",
        })
    return None


def _sanitize(obj):
    """Replace NaN/Inf with None for JSON serialization."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _df_to_json(df, order_col=None, order="asc", extra_meta=None):
    """Convert DataFrame to JSON-serializable dict with optional sorting and metadata."""
    if df is None or df.empty:
        result = {"rows": 0, "data": []}
        if extra_meta:
            result.update(extra_meta)
        return result

    # Sort if requested
    if order_col and order_col in df.columns:
        ascending = order == "asc"
        df = df.sort_values(order_col, ascending=ascending).reset_index(drop=True)

    data = df.to_dict(orient="records")
    result = {"rows": len(df), "data": _sanitize(data)}

    # Add date range metadata if order_col exists
    if order_col and order_col in df.columns:
        col = df[order_col]
        result["order"] = order
        result["date_col"] = order_col
        result["date_range"] = [str(col.iloc[0]), str(col.iloc[-1])]

    if extra_meta:
        result.update(extra_meta)

    return result


# ---- Status ----

@app.get("/status")
def get_status(summary: bool = False):
    """Get status of all datasets."""
    datasets = list_datasets()
    if summary:
        data = {
            name: {
                "rows": ds.get("rows", 0),
                "last_updated": ds.get("last_updated"),
                "category": ds.get("category", ""),
                "has_recipe": "recipe" in ds,
            }
            for name, ds in datasets.items()
        }
        data["_verification"] = {
            "validate_endpoint": "GET /validate?codes=XXX",
            "validate_first_param": "Add &validate_first=true to any data endpoint",
            "rule": "Call /validate BEFORE any stock/industry/index data task. valid=true required.",
        }
        return data
    return datasets


@app.get("/status/{dataset}")
def get_dataset_status(dataset: str):
    """Get status of a specific dataset."""
    datasets = list_datasets()
    ds = datasets.get(dataset)
    if not ds:
        return JSONResponse(status_code=404, content={"error": f"dataset {dataset} not found"})
    return ds


# ---- Health ----

@app.get("/health")
def health_check():
    """Basic liveness check."""
    return {"status": "ok", "service": "fmdata", "version": "0.3.0"}


@app.get("/health/data")
def health_data(category: Optional[str] = Query(None, description="Filter by category")):
    """Data quality report: empty datasets, stale datasets, fetch errors."""
    from fmdata.config import STORE_DIR
    datasets = list_datasets()

    empty = []
    stale_or_unknown = []
    file_missing = []
    total = 0

    for name, ds in datasets.items():
        if category and ds.get("category") != category:
            continue
        total += 1

        # Empty dataset (0 rows or rows field missing)
        rows = ds.get("rows", 0)
        if rows == 0:
            empty.append({"name": name, "category": ds.get("category", ""), "has_recipe": "recipe" in ds})
            continue

        # File missing on disk
        file_rel = ds.get("file", "")
        if file_rel:
            fpath = STORE_DIR / file_rel
            if not fpath.exists():
                file_missing.append({"name": name, "file": file_rel})
                continue

        # Stale or never updated
        last_updated = ds.get("last_updated")
        has_recipe = "recipe" in ds
        if has_recipe and not last_updated:
            stale_or_unknown.append({"name": name, "category": ds.get("category", ""), "reason": "never_fetched"})

    return {
        "total_datasets": total,
        "empty_count": len(empty),
        "stale_count": len(stale_or_unknown),
        "file_missing_count": len(file_missing),
        "empty_datasets": empty[:50],
        "stale_datasets": stale_or_unknown[:50],
        "file_missing": file_missing[:20],
    }


@app.get("/data/{name}")
def get_dataset_data(name: str):
    """Get data for any dataset by name. Works for all categories."""
    import pandas as pd
    from fmdata.config import STORE_DIR

    datasets = list_datasets()
    ds = datasets.get(name)
    if not ds:
        return JSONResponse(status_code=404, content={"error": f"dataset '{name}' not found"})

    file_path = STORE_DIR / ds.get("file", "")
    if not file_path.exists() or file_path.is_dir():
        # Try common patterns
        candidates = [
            STORE_DIR / f"{ds.get('category','')}/{name}.csv",
            STORE_DIR / f"market/{name}.csv",
            STORE_DIR / f"macro/{name}.csv",
            STORE_DIR / f"overseas/{name}.csv",
        ]
        for c in candidates:
            if c.exists() and not c.is_dir():
                file_path = c
                break

    if not file_path.exists() or file_path.is_dir():
        return JSONResponse(status_code=404, content={"error": f"data file for '{name}' not found on disk"})

    try:
        df = pd.read_csv(file_path)
        return _df_to_json(df)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"failed to read {name}: {str(e)}"})


# ---- Recipes ----

@app.get("/recipes")
def get_recipes():
    """List all available recipes."""
    recipes = load_all_recipes()
    return {
        name: {
            "source": r.get("source"),
            "category": r.get("category"),
            "description": r.get("description", ""),
            "update_freq": r.get("update_freq", ""),
        }
        for name, r in recipes.items()
    }


@app.get("/recipes/{name}")
def get_recipe(name: str):
    """Get a specific recipe."""
    recipe = load_recipe(name)
    if not recipe:
        return JSONResponse(status_code=404, content={"error": f"recipe '{name}' not found"})
    return recipe


# ---- Fetch (on-demand) ----

@app.post("/fetch/{name}")
def fetch_dataset_endpoint(name: str, request: Request):
    """Trigger on-demand fetch for a dataset using its recipe.

    Agent/remote recipes require X-API-Key header.
    """
    recipe = load_recipe(name)
    if not recipe:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"no recipe for '{name}'. Add a YAML to store/recipes/."}
        )
    # Auth check for dangerous sources
    if recipe.get("source") in DANGEROUS_SOURCES:
        auth_err = _check_admin_key(request)
        if auth_err:
            return auth_err
    from fmdata.recipe_fetcher import fetch_dataset as do_fetch
    result = do_fetch(name)
    return result


@app.post("/fetch-stale")
def fetch_stale(request: Request, max_age_hours: int = Query(24, description="Max age in hours before considered stale")):
    """Fetch all stale datasets (have recipes but data is old).

    Requires X-API-Key since stale datasets may include agent/remote recipes.
    """
    auth_err = _check_admin_key(request)
    if auth_err:
        return auth_err
    from fmdata.recipe_fetcher import fetch_stale as do_fetch_stale
    results = do_fetch_stale(max_age_hours)
    return {"fetched": len(results), "results": results}


# ---- Validation (Harness enforcement) ----

KNOWN_ERRORS = {
    "603377": ("ST东时", "宏和科技"),
    "688217": ("睿昂基因", "铜冠铜箔"),
    "688033": ("*ST天宜", "天承科技"),
}


@app.get("/validate")
def validate_codes(
    codes: Optional[str] = Query(None, description="逗号分隔的股票代码，如 000001.SZ,600519"),
    name: Optional[str] = Query(None, description="反向查询：股票名称查代码"),
):
    """标的代码验证。所有 agent 在涉及股票代码时必须先调此端点。"""
    from fmdata.reference import stock_list

    if name:
        df = stock_list()
        matches = df[df["name"].str.contains(name, na=False)]
        if matches.empty:
            return JSONResponse(status_code=404, content={"valid": False, "error": f"未找到包含'{name}'的股票"})
        return {"valid": True, "results": [{"code": r.get("ts_code", ""), "name": r.get("name", "")} for _, r in matches.head(10).iterrows()]}

    if not codes:
        return JSONResponse(status_code=400, content={"valid": False, "error": "需要 codes 或 name 参数"})

    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return JSONResponse(status_code=400, content={"valid": False, "error": "无有效代码"})

    df = stock_list()
    stock_map = dict(zip(df["ts_code"], df["name"]))

    results = []
    warnings = []
    has_error = False

    for code in code_list:
        ts_code = code if "." in code else (f"{code}.SZ" if code.startswith(("0", "3")) else f"{code}.SH")
        pure = code.split(".")[0]
        stock_name = stock_map.get(ts_code, "")

        if stock_name:
            results.append({"code": ts_code, "name": stock_name, "status": "ok"})
        else:
            results.append({"code": ts_code, "name": None, "status": "fail", "error": f"未找到: {ts_code}"})
            has_error = True

        if pure in KNOWN_ERRORS:
            actual, mistaken = KNOWN_ERRORS[pure]
            warnings.append({"code": ts_code, "actual": actual, "commonly_mistaken_as": mistaken})

    resp = {"valid": not has_error, "results": results}
    if warnings:
        resp["warnings"] = warnings

    if has_error:
        return JSONResponse(status_code=422, content=resp)
    return resp


# ---- Reference ----

@app.get("/reference/calendar")
def get_calendar(date: Optional[str] = Query(None, description="YYYYMMDD")):
    from fmdata.reference import trade_calendar, is_trade_day
    if date:
        return {"date": date, "is_trade_day": is_trade_day(date)}
    cal = trade_calendar()
    return _df_to_json(cal)


@app.get("/reference/last-trade-day")
def get_last_trade_day():
    from fmdata.reference import last_trade_day
    return {"last_trade_day": last_trade_day()}


@app.get("/reference/stocks")
def get_stocks(industry: Optional[str] = Query(None)):
    from fmdata.reference import stock_list
    df = stock_list()
    if industry:
        df = df[df["industry"].str.contains(industry, na=False)]
    return _df_to_json(df)


@app.get("/reference/tech-stocks")
def get_tech_stocks():
    from fmdata.reference import tech_stock_list
    return _df_to_json(tech_stock_list())


@app.get("/reference/industries")
def get_industries():
    from fmdata.reference import industry_list
    return _df_to_json(industry_list())


@app.get("/reference/industry-map")
def get_industry_map(ts_code: Optional[str] = Query(None)):
    from fmdata.reference import stock_industry_map
    df = stock_industry_map()
    if "ts_code" not in df.columns:
        # The cached map is the industry list fallback (tushare index_member
        # fetch failed at build time), not a stock→industry mapping.
        return {
            "status": "unavailable",
            "message": "stock→industry map not built; showing industry list instead",
            "data": _df_to_json(df),
        }
    if ts_code:
        df = df[df["ts_code"] == ts_code]
    return _df_to_json(df)


# ---- Market ----

@app.get("/market/daily-matrix")
def get_daily_matrix():
    from fmdata.market import daily_matrix
    df = daily_matrix()
    return _df_to_json(df)


@app.get("/market/sw-close")
def get_sw_close():
    from fmdata.market import sw_industry_close
    return _df_to_json(sw_industry_close())


@app.get("/market/sw-amount")
def get_sw_amount():
    from fmdata.market import sw_industry_amount
    return _df_to_json(sw_industry_amount())


@app.get("/market/sw-pe")
def get_sw_pe():
    from fmdata.market import sw_pe_history
    return _df_to_json(sw_pe_history())


@app.get("/market/sw-pb")
def get_sw_pb():
    from fmdata.market import sw_pb_history
    return _df_to_json(sw_pb_history())


@app.get("/market/hs300")
def get_hs300(freq: str = Query("daily")):
    from fmdata.market import hs300
    return _df_to_json(hs300(freq))


@app.get("/market/north-money")
def get_north_money():
    from fmdata.market import north_money
    return _df_to_json(north_money())


@app.get("/market/tech-indicators")
def get_tech_indicators():
    from fmdata.market import tech_indicators
    return _df_to_json(tech_indicators())


@app.get("/market/fundamentals")
def get_fundamentals(period: Optional[str] = Query(None)):
    from fmdata.market import stock_fina
    return _df_to_json(stock_fina(period))


@app.get("/market/fundamentals-extended")
def get_fundamentals_extended():
    from fmdata.market import stock_fina_extended
    return _df_to_json(stock_fina_extended())


@app.get("/market/stock-daily")
def get_stock_daily(
    code: str = Query(..., description="股票代码，如 002594 或 002594.SZ"),
    start_date: Optional[str] = Query(None, description="起始日期 YYYYMMDD"),
    end_date: Optional[str] = Query(None, description="截止日期 YYYYMMDD"),
    source: str = Query("tushare", description="数据源: tushare 或 akshare"),
    validate_first: bool = Query(False, description="自动验证代码有效性，无效返回422"),
    order: str = Query("asc", description="排序方向: asc (升序，默认) 或 desc (降序)"),
):
    """个股日线 OHLCV。fmdata 代理查询并缓存，agent 无需直接调 tushare/akshare。"""
    from fmdata.fetcher import TushareFetcher, AkshareFetcher
    from fmdata.reference import stock_list

    ts_code = code if "." in code else f"{code}.SZ" if code.startswith(("0", "3")) else f"{code}.SH"

    if validate_first:
        df_ref = stock_list()
        stock_map = dict(zip(df_ref["ts_code"], df_ref["name"]))
        if ts_code not in stock_map:
            return JSONResponse(status_code=422, content={"error": "invalid_code", "message": f"{ts_code} 不在股票列表中，请先调 /validate 确认代码正确"})

    if source == "akshare":
        af = AkshareFetcher()
        params = dict(symbol=ts_code.split(".")[0], period="daily", adjust="qfq")
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        df = af._call("stock_zh_a_hist", cache_key=f"stock_daily:{ts_code}:{start_date}:{end_date}", **params)
    else:
        tf = TushareFetcher()
        kwargs = dict(ts_code=ts_code)
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        df = tf._call("daily", cache_key=f"stock_daily:{ts_code}:{start_date}:{end_date}", **kwargs)

    if df is None or (hasattr(df, "empty") and df.empty):
        return JSONResponse(status_code=404, content={"status": "empty", "message": f"no data for {ts_code}"})

    # Determine date column name
    date_col = "trade_date" if "trade_date" in df.columns else ("date" if "date" in df.columns else None)
    return _df_to_json(df, order_col=date_col, order=order)


@app.get("/market/factor-matrix")
def get_factor_matrix():
    from fmdata.market import factor_matrix
    return _df_to_json(factor_matrix())


# ---- Macro ----
# Semantic aliases for /data/{name} on macro datasets.
# Valid names: cpi, ppi, pmi, money_supply, credit, lpr, shibor, macro_monthly

@app.get("/macro/{name}")
def get_macro(name: str):
    from fmdata.macro import get as get_macro_df
    df = get_macro_df(name)
    if df.empty:
        return JSONResponse(status_code=404, content={"error": f"macro dataset '{name}' not found or empty"})
    return _df_to_json(df)


@app.post("/recipes")
def create_recipe(request: Request, recipe: dict):
    """Create a new recipe via HTTP and optionally trigger fetch.

    - source: tushare/akshare → no auth required (safe, read-only data fetch)
    - source: agent/remote → requires X-API-Key header (can execute commands)
    - name must match [A-Za-z0-9_-]+
    """
    from fmdata.registry import RECIPES_DIR, init_registry_from_store
    name = recipe.get("name")
    if not name:
        return JSONResponse(status_code=400, content={"status": "error", "message": "recipe must have a 'name'"})

    # Validate name format
    if not RECIPE_NAME_RE.match(name):
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": f"recipe name must match [A-Za-z0-9_-]+, got: '{name}'",
        })

    source = recipe.get("source", "")
    if source in DANGEROUS_SOURCES:
        # Require admin key for agent/remote recipes
        auth_err = _check_admin_key(request)
        if auth_err:
            return auth_err

    existing = load_recipe(name)
    if existing:
        return JSONResponse(status_code=409, content={"status": "exists", "message": f"recipe '{name}' already exists. Use POST /fetch/{name} to refresh."})

    required = ["name", "source", "fetch"]
    missing = [f for f in required if f not in recipe]
    if missing:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"missing fields: {missing}"})

    fetch_cfg = recipe.get("fetch", {})
    # Safe sources require func field; agent/remote require command
    if source in SAFE_SOURCES and "func" not in fetch_cfg:
        return JSONResponse(status_code=400, content={"status": "error", "message": "fetch.func is required for safe sources"})
    if source in DANGEROUS_SOURCES and "command" not in fetch_cfg and "func" not in fetch_cfg:
        return JSONResponse(status_code=400, content={"status": "error", "message": "fetch.command or fetch.func is required"})

    recipe.setdefault("category", "market")
    recipe.setdefault("file", f"{recipe['category']}/{name}.csv")

    import yaml as _yaml
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    recipe_path = RECIPES_DIR / f"{name}.yaml"
    with open(recipe_path, "w") as f:
        _yaml.dump(recipe, f, default_flow_style=False, allow_unicode=True)

    init_registry_from_store()

    auto_fetch = recipe.get("_auto_fetch", True)
    fetch_result = None
    if auto_fetch:
        from fmdata.recipe_fetcher import fetch_dataset as do_fetch
        fetch_result = do_fetch(name)

    return {
        "status": "created",
        "recipe": name,
        "file": str(recipe_path),
        "fetch_result": fetch_result,
    }


@app.get("/how-to-add")
def how_to_add():
    """Agent guide: how to add missing data to fmdata instead of bypassing it."""
    return {
        "principle": "fmdata is the data governance layer. If data is missing, ADD it here — never bypass to tushare/akshare directly.",
        "mandatory_protocol": {
            "before_any_stock_data": "GET /validate?codes=YOUR_CODES — must return valid=true",
            "with_data_endpoints": "Add &validate_first=true to auto-validate codes",
            "after_data_pull": "Check rows>0, no all-NaN columns, date range correct",
            "after_calculation": "Spot-check 3-5 samples manually",
            "before_report": "Cross-check core numbers with source",
        },
        "steps": [
            {
                "step": 1,
                "action": "Check if recipe exists",
                "command": "GET /recipes",
                "note": "List all recipes. If your dataset has a recipe, use POST /fetch/{name} to refresh it."
            },
            {
                "step": 2,
                "action": "Register recipe via HTTP (recommended, no file system access needed)",
                "command": "POST /recipes",
                "note": "Send JSON with name, source, fetch config. Auto-creates recipe YAML + triggers first fetch. Example: POST /recipes {\"name\":\"my_data\",\"source\":\"tushare\",\"fetch\":{\"func\":\"daily\",\"params\":{\"ts_code\":\"300750.SZ\"}}}",
                "example_curl": "curl -s -X POST http://127.0.0.1:1934/recipes -H 'Content-Type: application/json' -d '{\"name\":\"my_data\",\"source\":\"tushare\",\"fetch\":{\"func\":\"daily\",\"params\":{\"ts_code\":\"300750.SZ\"}}}'"
            },
            {
                "step": "2b",
                "action": "Or create recipe YAML manually (if on SZ81)",
                "path": "~/fmdata/store/recipes/{name}.yaml",
                "format": {
                    "name": "dataset_name",
                    "category": "market|macro|reference|fundamentals|overseas",
                    "description": "What this dataset contains",
                    "file": "category/name.csv",
                    "source": "tushare|akshare|agent",
                    "fetch": {
                        "func": "tushare_or_akshare_function_name",
                        "date_col": "column_name_for_incremental_update",
                        "update_freq": "daily|weekly|monthly",
                        "params": {}
                    }
                }
            },
            {
                "step": 3,
                "action": "Trigger first fetch",
                "command": "POST /fetch/{name}",
                "note": "fmdata will call the source API, save to CSV, and serve from cache afterward."
            },
            {
                "step": 4,
                "action": "Add a dedicated route (optional)",
                "path": "~/fmdata/fmdata/server.py",
                "note": "For frequently used queries, add a GET endpoint with filtering params (see stock-daily as example)."
            },
            {
                "step": 5,
                "action": "Restart fmdata",
                "command": "sudo systemctl restart fmdata",
                "note": "Only needed if you added a new route in server.py. Recipes are auto-discovered."
            }
        ],
        "examples": {
            "tushare_recipe": "daily_basic.yaml — calls tushare daily_basic, saves to market/daily_basic.csv",
            "akshare_recipe": "hs300_daily.yaml — calls akshare index_zh_a_hist_em, incremental by date",
            "parameterized_route": "/market/stock-daily?code=XXX — proxies tushare daily with caching per stock code",
            "command_recipe": "Use source: agent with a shell command in fetch.command for custom pipelines"
        },
        "key_paths": {
            "recipes": "~/fmdata/store/recipes/",
            "data": "~/fmdata/store/",
            "server": "~/fmdata/fmdata/server.py",
            "fetcher": "~/fmdata/fmdata/fetcher.py (TushareFetcher, AkshareFetcher)",
            "recipe_fetcher": "~/fmdata/fmdata/recipe_fetcher.py"
        }
    }
