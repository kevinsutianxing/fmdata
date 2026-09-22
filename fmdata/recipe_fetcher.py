"""Recipe-based on-demand data fetching with proxy support."""
import json
import logging
import os
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from fmdata.config import STORE_DIR, EM_API_KEY, SW_MCP_URL, SW_MCP_CODE

logger = logging.getLogger("fmdata.recipe_fetcher")

# ---- Agent script allowlist ----
# Only these scripts can be executed by agent recipes.
# Maps script alias → absolute path.
AGENT_SCRIPT_ALLOWLIST = {
    "refresh_sw_daily_full": "/home/ubuntu/fmdata/scripts/refresh_sw_daily_full.py",
    "refresh_spot_snapshot": "/home/ubuntu/fmdata/scripts/refresh_spot_snapshot.py",
    "refresh_sw_data": "/home/ubuntu/fmdata/scripts/refresh_sw_data.py",
    "refresh_sw_fundamentals": "/home/ubuntu/fmdata/scripts/refresh_sw_fundamentals.py",
    "refresh_sw_fund_flow": "/home/ubuntu/fmdata/scripts/refresh_sw_fund_flow.py",
    "compute_tech_signals": "/home/ubuntu/fmdata/scripts/compute_tech_signals.py",
    "compute_factor_matrix": "/home/ubuntu/fmdata/scripts/compute_factor_matrix.py",
    "compute_industry_median": "/home/ubuntu/fmdata/scripts/compute_industry_median.py",
    "compute_macro_monthly": "/home/ubuntu/fmdata/scripts/compute_macro_monthly.py",
    "refresh_etf_daily": "/home/ubuntu/fmdata/scripts/refresh_etf_daily.py",
    "refresh_etf_data_collection": "/home/ubuntu/fmdata/scripts/refresh_etf_daily.py",
    "refresh_etf_monthly": "/home/ubuntu/fmdata/scripts/refresh_etf_monthly.py",
    "refresh_cgb_yield": "/home/ubuntu/fmdata/scripts/refresh_cgb_yield.py",
    "fetch_actual_financials": "/home/ubuntu/fmdata/store/scripts/fetch_actual_financials.py",
    "fetch_active_fund_list": "/home/ubuntu/fmdata/store/scripts/fetch_active_fund_list.py",
    "fetch_historical_consensus_ak": "/home/ubuntu/fmdata/store/scripts/fetch_historical_consensus_ak.py",
    "refresh_consensus": "/home/ubuntu/fmdata/store/scripts/refresh_consensus.py",
    "fetch_performance_forecast": "/home/ubuntu/fmdata/store/scripts/fetch_performance_forecast.py",
    "compute_factors": "/home/ubuntu/claude-workspace/industry_rotation/compute_factors.py",
    # cjpy (长江金工/天软 TS-OPI) 数据源
    "fetch_cjpy": "/home/ubuntu/fmdata/scripts/fetch_cjpy.py",
    # 半年报追踪 (快报+预告+Q2拆解+同比/环比)
    "fetch_semiannual_tracker": "/home/ubuntu/fmdata/scripts/fetch_semiannual_tracker.py",
    "fetch_fund_nav": "/home/ubuntu/fmdata/store/scripts/fetch_fund_nav.py",
    # 海外美股快照 (eastmoney push2his + QG 代理池)
    "fetch_us_stock_spot": "/home/ubuntu/fmdata/store/scripts/fetch_us_stock_spot.py",
    # CSI300 指数增强回测数据 (tushare, 半年分页 / 逐只 adj_factor)
    "fetch_index_weight_000300": "/home/ubuntu/fmdata/store/scripts/fetch_index_weight_000300.py",
    "fetch_index_weight_hs300_zz500": "/home/ubuntu/fmdata/store/scripts/fetch_index_weight_hs300_zz500.py",
    "fetch_adj_factor_csi300": "/home/ubuntu/fmdata/store/scripts/fetch_adj_factor_csi300.py",
    # 分析师一致预期因子表 (本地 analyst_consensus/panel/snapshots + daily-matrix)
    "build_consensus_factors": "/home/ubuntu/fmdata/store/scripts/build_consensus_factors.py",
    # 统一因子库 (2026-09-14): 四源目录 + 单入口查询门面
    "build_factor_catalog": "/home/ubuntu/fmdata/store/scripts/build_factor_catalog.py",
    "factor_query": "/home/ubuntu/fmdata/store/scripts/factor_query.py",
    # 中证800 (000906) 指数权重 (tushare index_weight, 半年分页, 镜像 000300)
    "fetch_index_weight_000906": "/home/ubuntu/fmdata/store/scripts/fetch_index_weight_000906.py",
    # CSI1000/500 historical index weights
    "fetch_index_weight_000852_full": "/home/ubuntu/fmdata/store/scripts/fetch_index_weight_000852.py",
    "fetch_index_weight_000905_full": "/home/ubuntu/fmdata/store/scripts/fetch_index_weight_000905.py",
    "ingest_wind_index_daily": "/home/ubuntu/fmdata/store/scripts/ingest_wind_index_daily.py",
    "fetch_cb_cashflows_pit": "/home/ubuntu/fmdata/scripts/fetch_cb_cashflows_pit.py",
    "fetch_cb_stock_fundamentals_pit": "/home/ubuntu/fmdata/scripts/fetch_cb_stock_fundamentals_pit.py",
    "fetch_cb_stock_daily": "/home/ubuntu/fmdata/scripts/fetch_cb_stock_daily.py",
    # 披露/融资融券/互动易/业绩 (akshare-em 系, 2026-08-17 补登记)
    "fetch_disclosure": "/home/ubuntu/fmdata/store/scripts/fetch_disclosure.py",
    "fetch_income_full": "/home/ubuntu/fmdata/store/scripts/fetch_income_full.py",
    "fetch_irm_qa": "/home/ubuntu/fmdata/store/scripts/fetch_irm_qa.py",
    "fetch_margin_detail": "/home/ubuntu/fmdata/store/scripts/fetch_margin_detail.py",
    "fetch_semiannual_investment": "/home/ubuntu/fmdata/scripts/fetch_semiannual_investment_system.py",
    # 回购/研报/限售解禁/基金基础 (akshare-em 系, 2026-08-31 补登记——连续两晚治理 502 真因)
    "fetch_repurchase": "/home/ubuntu/fmdata/store/scripts/fetch_repurchase.py",
    "fetch_research_reports": "/home/ubuntu/fmdata/store/scripts/fetch_research_reports.py",
    "fetch_restricted_release": "/home/ubuntu/fmdata/store/scripts/fetch_restricted_release.py",
    "fetch_fund_5y_returns_full": "/home/ubuntu/fmdata/store/scripts/fetch_fund_5y_returns_full.py",
    "fetch_fund_basic_open": "/home/ubuntu/fmdata/store/scripts/fetch_fund_basic_open.py",
}

# Remote host allowlist
REMOTE_HOST_ALLOWLIST = {"hk43"}

# 东方财富「妙想」MCP Server —— 官方认证 API(stateless, NL query, 2026-08-06)。
# 非裸连:走 mxapi.eastmoney.com/mxds/mcp + em_api_key,不经 QG 代理池。
MX_MCP_URL = "https://mxapi.eastmoney.com/mxds/mcp"
MX_TOOLS = {
    "mx_ashare_finance_data", "mx_us_finance_data", "mx_hk_finance_data",
    "mx_index_block_finance_data", "mx_fund_finance_data", "mx_bond_finance_data",
    "mx_macro_data", "mx_stocks_screener", "mx_finance_search_news",
    "mx_finance_search_notice", "mx_comprehensive_finance_data",
}


# Dual QG proxy pool credentials (2026-07-01: 旧池快耗尽, kevinsu 池做 fallback via env)
_QG_POOLS = [
    {"key": os.environ.get("QG_PROXY_AUTHKEY", ""), "pwd": os.environ.get("QG_PROXY_AUTHPWD", "")},
    {"key": os.environ.get("QG_PROXY_AUTHKEY_2", ""), "pwd": os.environ.get("QG_PROXY_AUTHPWD_2", "")},
]


def _get_qg_proxy():
    """Fetch a QG proxy URL from dual pools with fallback.
    Tries pool 0 (primary, env-configured) then pool 1 (kevinsu fallback).
    """
    import json
    from urllib.request import urlopen, Request
    from urllib.error import URLError

    for pi, pool in enumerate(_QG_POOLS):
        key, pwd = pool["key"], pool["pwd"]
        if not key:
            continue
        url = f"https://share.proxy.qg.net/get?key={key}&num=1&format=json&distinct=true"
        try:
            resp = urlopen(Request(url), timeout=10)
            data = json.loads(resp.read())
            if data.get("data"):
                item = data["data"][0]
                server = item.get("server") or item.get("proxy_url")
                if server:
                    bare = server.split("://")[-1]
                    return f"http://{key}:{pwd}@{bare}"
        except Exception as e:
            pool_label = "primary" if pi == 0 else "fallback"
            logger.warning(f"QG proxy {pool_label} fetch failed: {e}")
    return None


def _set_requests_proxy(proxy_url):
    """Set HTTP/HTTPS proxy for the requests library (used by akshare)."""
    if proxy_url:
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        logger.info(f"proxy set: {proxy_url}")
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


def eastmoney_get(url, params, max_tries=8, timeout=12, backoff=0.15):
    """东财经 QG 代理池 IP 轮换抓取(2026-06-16 固化,自 refresh_sw_fund_flow 的 req_retry 抽取)。

    成功技巧(2026-06-16 03:32 生产实证:代理 IP 轮换抓到 06-15 新鲜数据):
    1. 调用方务必用 push2his.eastmoney.com(历史子域)而非 push2(实时子域)——东财对实时子域
       限速远严;akshare 内部打 push2 故经代理必断,业务侧须按 akshare 源码精确参数自连 push2his。
    2. 每次重试 _get_qg_proxy() 换一个出口 IP,穿代理池对东财的簇集波动窗口(成功率 0-50% 随
       时间漂移)。代理核心价值=IP 轮换把请求摊薄到多 IP,避免单 IP(含本机)被累计高频打降权。
    3. 退避递增(0.5+backoff·i)避免快速重试同一批差 IP;ProxyError 快速失败故高 max_tries 代价低。
    4. 死窗口(0%)穿不过,靠上层 cron 多次跑轮换窗口兜底(脚本诚实 exit success==0 即可)。

    返回 JSON dict 或 None。
    """
    import time as _t
    import requests as _rq
    for i in range(max_tries):
        p = _get_qg_proxy()
        if not p:
            _t.sleep(1)
            continue
        _set_requests_proxy(p)
        try:
            return _rq.Session().get(url, params=params, timeout=timeout).json()
        except Exception:
            _t.sleep(0.5 + i * backoff)
    return None


# ---- 申万宏源金工 MCP ----
# 16 因子官方目录(月末快照;10风格+4筹码+行业轮动+GBM量价)
SW_FACTOR_KEYS = ["估值", "低波", "低流动性", "分析师", "动量", "反转", "市值", "成长",
                  "盈利", "红利", "筹码成本差", "筹码成本", "机构筹码集中度", "筹码合成",
                  "行业轮动", "GBM量价"]
# 服务端单响应硬上限 120 行(实测 600 只请求只回 120,metadata.truncated 不可信),
# 所有调用方必须自行分块:截面 ≤100 只/次,时序 ≤110 月/次
SW_ROW_CAP = 120


class _SwMcpClient:
    """申万宏源金工 MCP 客户端(Streamable HTTP,session 制)。

    与妙想桥不同:本服务强制 mcp-session-id,须先 initialize 握手;
    session 失效(400)时自动重置重试一次。响应可能是 JSON 或 SSE。
    """

    def __init__(self, url: str, code: str):
        self.url, self.code = url.rstrip("/"), code
        self._session = None

    def _post(self, payload: dict, timeout: int) -> dict:
        import requests as _rq
        headers = {"Authorization": f"Bearer {self.code}",
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self._session:
            headers["mcp-session-id"] = self._session
        resp = _rq.post(self.url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session = sid
        # 两个坑:①SSE 无 charset,resp.text 会按 ISO-8859-1 解码 → 中文 mojibake,
        # 必须显式 UTF-8;②mojibake 里的 \x85(NEL) 会被 splitlines() 当换行把
        # data 行拦腰斩断 → 只能按真实 \n 切再剥 \r
        body = resp.content.decode("utf-8", errors="replace")
        if "text/event-stream" in resp.headers.get("content-type", ""):
            for line in body.split("\n"):
                line = line.rstrip("\r")
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except Exception:
                        continue
            return {}
        return json.loads(body) if body.strip() else {}

    def _ensure_session(self, timeout: int) -> None:
        if self._session:
            return
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "fmdata-sw-mcp", "version": "0.1"}}}, timeout)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)

    def call(self, tool: str, args: dict, timeout: int = 60) -> dict:
        """tools/call 并解析 content[0].text 为 JSON(服务端 text 里是 JSON,含裸 NaN)。"""
        self._ensure_session(timeout)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": tool, "arguments": args}}
        try:
            rj = self._post(payload, timeout)
        except Exception:
            # session 过期是唯一可自愈失败模式:重置握手重试一次
            self._session = None
            self._ensure_session(timeout)
            rj = self._post(payload, timeout)
        if rj.get("error"):
            raise RuntimeError(f"SW MCP error: {rj['error']}")
        result = rj.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"SW MCP isError: {str(result.get('content'))[:200]}")
        for item in result.get("content", []) or []:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except Exception:
                    return {"raw": item["text"]}
        return {}


class RecipeFetcher:
    """Execute recipes to fetch or update datasets on-demand."""

    def fetch(self, recipe: dict) -> dict:
        """Execute a recipe and save the result. Returns status dict."""
        source = recipe.get("source", "unknown")
        fetch_cfg = recipe.get("fetch", {})
        name = recipe.get("name", "unknown")
        needs_proxy = fetch_cfg.get("proxy") == "qg" or source == "akshare"

        try:
            proxy_url = None
            if needs_proxy:
                proxy_url = _get_qg_proxy()
                if proxy_url:
                    _set_requests_proxy(proxy_url)
                else:
                    logger.warning(f"no proxy available for {name}, trying direct")

            if source == "akshare":
                return self._fetch_akshare(name, recipe, fetch_cfg)
            elif source == "tushare":
                return self._fetch_tushare(name, recipe, fetch_cfg)
            elif source == "agent":
                return self._fetch_agent(name, recipe, fetch_cfg)
            elif source == "remote":
                return self._fetch_remote(name, recipe, fetch_cfg)
            elif source == "eastmoney_mx":
                return self._fetch_eastmoney_mx(name, recipe, fetch_cfg)
            elif source == "sw_mcp":
                return self._fetch_sw_mcp(name, recipe, fetch_cfg)
            else:
                return {"status": "error", "message": f"unknown source: {source}"}
        except Exception as e:
            logger.error(f"recipe fetch failed for {name}: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            if needs_proxy:
                _set_requests_proxy(None)

    def _fetch_akshare(self, name: str, recipe: dict, fetch_cfg: dict) -> dict:
        func_name = fetch_cfg.get("func")
        if not func_name:
            return {"status": "error", "message": "no func specified in recipe"}

        import akshare as ak
        func = getattr(ak, func_name, None)
        if not func:
            return {"status": "error", "message": f"akshare function not found: {func_name}"}

        params = dict(fetch_cfg.get("params", {}))
        date_col = fetch_cfg.get("date_col")
        output_path = STORE_DIR / recipe.get("file", f"market/{name}.csv")
        incremental = fetch_cfg.get("incremental", True)

        # Step 1: Read existing data BEFORE fetch (for incremental merge)
        existing_df = None
        if incremental and date_col and output_path.exists():
            existing_df = pd.read_csv(output_path)
            if not existing_df.empty and date_col in existing_df.columns:
                last_date = pd.to_datetime(existing_df[date_col]).max()
                start_key = fetch_cfg.get("start_date_param", "start_date")
                if start_key not in params:
                    params[start_key] = (last_date + pd.Timedelta(days=1)).strftime("%Y%m%d")

        # Step 2: Fetch new data — 池内单IP对东财成功率随时间簇集波动(0-50%),
        # 抽到坏IP单发即挂; 镜像 eastmoney_get 的轮换策略: 每次重试换一个出口IP
        logger.info(f"fetching {name} via akshare.{func_name}({params})")
        attempts = fetch_cfg.get("attempts", 3)
        df, last_err = None, None
        for attempt in range(1, attempts + 1):
            try:
                df = func(**params)
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.warning(f"{name} akshare attempt {attempt}/{attempts} failed: {e}")
                if attempt < attempts:
                    _set_requests_proxy(_get_qg_proxy() or "")
        if last_err is not None:
            return {"status": "error", "message": f"{func_name}: {last_err}"}

        if df is None or df.empty:
            return {"status": "empty", "message": f"{func_name} returned no data"}

        # Step 3: Merge with existing if incremental
        if existing_df is not None and not existing_df.empty and date_col and date_col in df.columns:
            df = pd.concat([existing_df, df], ignore_index=True)
            if date_col in df.columns:
                # akshare 1.18.91+ 部分接口返回 datetime.date, 旧CSV是str — 混型会让
                # drop_duplicates 失效(同日双行被保留)+ sort_values 直接崩; 统一转回 str
                if df[date_col].map(type).nunique() > 1:
                    df[date_col] = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
                df = df.drop_duplicates(subset=[date_col], keep="last")
                df = df.sort_values(date_col).reset_index(drop=True)

        # Step 4: Atomic write via temp file + rename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(".csv.tmp")
        df.to_csv(tmp_path, index=False)
        tmp_path.rename(output_path)
        logger.info(f"saved {name}: {len(df)} rows to {output_path}")

        return {"status": "ok", "rows": len(df), "file": str(output_path)}

    def _fetch_tushare(self, name: str, recipe: dict, fetch_cfg: dict) -> dict:
        func_name = fetch_cfg.get("func")
        if not func_name:
            return {"status": "error", "message": "no func specified in recipe"}

        from fmdata.fetcher import TushareFetcher
        tushare = TushareFetcher()
        params = dict(fetch_cfg.get("params", {}))
        # Resolve "latest" sentinel → most recent trade day, so tushare recipes can
        # stay current without a hardcoded date (mirrors akshare's auto-date path).
        if "latest" in params.values():
            from fmdata.reference import last_trade_day
            _ltd = last_trade_day()
            params = {k: (_ltd if v == "latest" else v) for k, v in params.items()}
        output_path = STORE_DIR / recipe.get("file", f"macro/{name}.csv")

        logger.info(f"fetching {name} via tushare.{func_name}({params})")
        df = tushare._call(func_name, None, **params)

        if df is None or df.empty:
            return {"status": "empty", "message": f"{func_name} returned no data"}

        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        logger.info(f"saved {name}: {len(df)} rows to {output_path}")

        return {"status": "ok", "rows": len(df), "file": str(output_path)}

    def _fetch_eastmoney_mx(self, name: str, recipe: dict, fetch_cfg: dict) -> dict:
        """东方财富「妙想」MCP NL 查询桥(官方认证 API,非裸连,无需 QG 代理)。

        非确定性 / ad-hoc / 不可用于回测 —— 仅 on-demand 查询。把多 sheet 响应展平为
        长表 CSV(sheet, row_label, col_header, value),无损。ad-hoc 查询走 Layer 1
        (agent 直调 MCP 工具);本 recipe 仅作"经 fmdata 表面可达 + catalog 可发现"的 canned 桥。
        """
        import requests as _rq
        params = fetch_cfg.get("params") or {}
        tool = fetch_cfg.get("func") or params.get("tool", "mx_ashare_finance_data")
        query = fetch_cfg.get("query") or params.get("query")
        if not query:
            return {"status": "error", "message": "eastmoney_mx recipe 需 fetch.query 或 fetch.params.query"}
        if not EM_API_KEY:
            return {"status": "error", "message": "EM_API_KEY 未配置(~/fmdata/.env)"}
        if tool not in MX_TOOLS:
            return {"status": "error", "message": f"未知妙想工具 {tool};可用:{sorted(MX_TOOLS)}"}
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "em_api_key": EM_API_KEY}
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": tool, "arguments": {"query": query}}}
        timeout = fetch_cfg.get("timeout", 90)
        logger.info(f"mx_query {name}: tool={tool} query={query!r}")
        resp = _rq.post(MX_MCP_URL, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        rj = resp.json()
        if rj.get("error"):
            return {"status": "error", "message": f"MCP error: {rj['error']}"}
        result = rj.get("result", {})
        if result.get("isError"):
            return {"status": "error", "message": f"MCP isError: {str(result.get('content'))[:200]}"}
        rows = []
        for item in result.get("content", []) or []:
            if item.get("type") != "text":
                continue
            raw = item.get("text", "")
            try:
                parsed = json.loads(raw)
            except Exception:
                rows.append({"sheet": "(raw)", "row_label": "", "col_header": "text", "value": raw})
                continue
            for sheet in parsed.get("data", []) or []:
                sheet_name = sheet.get("sheetName", "")
                columns = sheet.get("columns", []) or []
                for itemrow in sheet.get("items", []) or []:
                    row_label = itemrow[0] if itemrow else ""
                    for j, val in enumerate(itemrow[1:], start=1):
                        col_header = columns[j] if j < len(columns) else f"col{j}"
                        rows.append({"sheet": sheet_name, "row_label": row_label,
                                     "col_header": str(col_header), "value": val})
        df = pd.DataFrame(rows, columns=["sheet", "row_label", "col_header", "value"])
        output_path = STORE_DIR / recipe.get("file", f"market/{name}.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = output_path.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        tmp.rename(output_path)
        logger.info(f"saved {name}: {len(df)} cells (long-format) to {output_path}")
        return {"status": "ok", "rows": len(df), "file": str(output_path)}

    def _fetch_sw_mcp(self, name: str, recipe: dict, fetch_cfg: dict) -> dict:
        """申万宏源金工 MCP 桥(结构化参数,session 制,非 NL → 可确定性复现)。

        fetch.func = factor_value(月度截面,默认全 universe) | factor_series(个股时序)。
        120 行/响应上限由本方法内部分块;增量为 (date, ts_code) 去重合并。
        PIT 注意:月末快照结构,历史月值回测资格待"隔月重拉 diff"验证(见 RUNBOOK)。
        """
        if not SW_MCP_URL or not SW_MCP_CODE:
            return {"status": "error", "message": "SW_MCP_URL/SW_MCP_CODE 未配置(~/fmdata/.env)"}
        func = fetch_cfg.get("func", "factor_value")
        params = dict(fetch_cfg.get("params", {}))
        timeout = fetch_cfg.get("timeout", 90)
        output_path = STORE_DIR / recipe.get("file", "factors/sw_factor_value.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cli = _SwMcpClient(SW_MCP_URL, SW_MCP_CODE)
        rows: list = []

        if func == "factor_value":
            factors = params.get("factors") or SW_FACTOR_KEYS
            date = params.get("date") or datetime.now().strftime("%Y-%m-%d")
            ts_codes = params.get("ts_codes")
            if not ts_codes:
                uni_path = STORE_DIR / "factors/sw_universe.csv"
                if uni_path.exists():
                    ts_codes = pd.read_csv(uni_path)["ts_code"].tolist()
                else:
                    return {"status": "error",
                            "message": "factor_value 需 fetch.params.ts_codes 或先建 factors/sw_universe.csv(见 scripts/backfill_sw_factors.py)"}
            actual = set()
            for i in range(0, len(ts_codes), 100):
                chunk = ts_codes[i:i + 100]
                d = cli.call("get_factor_value",
                             {"factors": factors, "ts_codes": chunk, "date": date}, timeout)
                rows.extend(d.get("records", []))
                m = (d.get("metadata") or {}).get("actual_month_end")
                if m:
                    actual.add(m)
                time.sleep(0.15)  # 礼貌限速(个人邀请码授权,无文档化配额)
        elif func == "factor_series":
            factors = params.get("factors") or SW_FACTOR_KEYS
            ts_codes = params.get("ts_codes") or []
            start = params.get("start")
            end = params.get("end")
            if not (ts_codes and start and end):
                return {"status": "error", "message": "factor_series 需 params.ts_codes/start/end"}
            # 窗口按 ≤110 个月分块(上限 120 行/响应)
            window = pd.period_range(start, end, freq="M")
            for code in ts_codes:
                for w0 in range(0, len(window), 110):
                    w = window[w0:w0 + 110]
                    d = cli.call("get_factor_series",
                                 {"factors": factors, "ts_codes": [code],
                                  "start": str(w[0].start_time.date()),
                                  "end": str(w[-1].end_time.date())}, timeout)
                    rows.extend(d.get("records", []))
                    time.sleep(0.15)
        else:
            return {"status": "error", "message": f"未知 sw_mcp func: {func}(factor_value|factor_series)"}

        if not rows:
            return {"status": "empty", "message": "SW MCP 返回 0 行(月度因子更新滞后 ~T+7,换上月末日期重试)"}
        df = pd.DataFrame(rows)
        # 增量合并:(date, ts_code) 去重保留最新
        if output_path.exists():
            old = pd.read_csv(output_path)
            if not old.empty:
                df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["date", "ts_code"], keep="last")
        df = df.sort_values(["date", "ts_code"]).reset_index(drop=True)
        tmp = output_path.with_suffix(".csv.tmp")
        df.to_csv(tmp, index=False)
        tmp.rename(output_path)
        extra = {"actual_month_end": sorted(actual)} if func == "factor_value" else {}
        logger.info(f"saved {name}: {len(df)} rows (wide) to {output_path}")
        return {"status": "ok", "rows": len(df), "file": str(output_path), **extra}

    def _fetch_agent(self, name: str, recipe: dict, fetch_cfg: dict) -> dict:
        """Execute an agent recipe using the script allowlist (no shell=True)."""
        command = fetch_cfg.get("command")
        if not command:
            return {"status": "error", "message": "no command specified in agent recipe"}

        timeout = fetch_cfg.get("timeout", 300)
        output_rel = fetch_cfg.get("output", recipe.get("file", ""))
        output_path = STORE_DIR / output_rel if output_rel else None

        # Parse command to extract script path
        parts = shlex.split(command)
        if not parts:
            return {"status": "error", "message": "empty command"}

        # Handle python3 <script> or cp <src> <dst>
        if parts[0] == "python3" and len(parts) >= 2:
            script_path = os.path.expanduser(parts[1])
            # Check allowlist
            allowed = False
            for alias, allowed_path in AGENT_SCRIPT_ALLOWLIST.items():
                if os.path.abspath(script_path) == os.path.abspath(allowed_path):
                    allowed = True
                    break
            if not allowed:
                logger.error(f"agent script not in allowlist: {script_path}")
                return {"status": "error", "message": f"script not in allowlist: {parts[1]}"}
            # Build argv without shell
            argv = ["python3", script_path] + parts[2:]
        elif parts[0] == "cp" and len(parts) == 3:
            # Allow cp for known file copy operations (e.g. tencent_hk_adj_factor)
            src = os.path.expanduser(parts[1])
            dst = os.path.expanduser(parts[2])
            # Both paths must be under STORE_DIR or known safe dirs
            if not (src.startswith("/home/ubuntu/fmdata/") or src.startswith("/home/ubuntu/claude-workspace/")):
                return {"status": "error", "message": f"cp source not allowed: {parts[1]}"}
            if not dst.startswith("/home/ubuntu/fmdata/"):
                return {"status": "error", "message": f"cp destination not allowed: {parts[2]}"}
            argv = ["cp", src, dst]
        else:
            return {"status": "error", "message": f"unsupported command: {command}. Use 'python3 <allowlisted_script>' or 'cp <src> <dst>'"}

        logger.info(f"fetching {name} via agent: {' '.join(argv)}")
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"command failed (exit {result.returncode})",
                "stderr": result.stderr[:500],
            }

        if output_path and output_path.exists():
            rows = sum(1 for _ in open(output_path)) - 1
            return {"status": "ok", "rows": rows, "file": str(output_path)}

        return {
            "status": "ok",
            "message": "command completed (no output file check)",
            "stdout": result.stdout[:500],
        }

    def _fetch_remote(self, name: str, recipe: dict, fetch_cfg: dict) -> dict:
        """Fetch data by delegating to a remote host via SSH.

        Host must be in REMOTE_HOST_ALLOWLIST. Env vars listed in fetch.env
        are automatically injected from the local process environment.
        """
        host = fetch_cfg.get("host")
        command = fetch_cfg.get("command")
        if not host or not command:
            return {"status": "error", "message": "remote recipe needs 'host' and 'command'"}

        # Validate host
        if host not in REMOTE_HOST_ALLOWLIST:
            return {"status": "error", "message": f"host '{host}' not in allowlist: {REMOTE_HOST_ALLOWLIST}"}

        output_path = STORE_DIR / recipe.get("file", f"overseas/{name}.csv")
        timeout = fetch_cfg.get("timeout", 120)

        # Inject env vars from recipe's env list
        env_names = fetch_cfg.get("env", [])
        env_prefix = ""
        for var in env_names:
            val = os.environ.get(var, "")
            if val:
                env_prefix += f"{var}={shlex.quote(val)} "

        remote_cmd = f"{env_prefix}{command}"
        # Use subprocess without shell=True: ["ssh", host, command]
        argv = ["ssh", host, remote_cmd]
        logger.info(f"fetching {name} via remote ({host}): {remote_cmd}")

        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"remote fetch failed (exit {result.returncode})",
                "stderr": result.stderr[:500],
            }

        stdout = result.stdout.strip()
        if not stdout:
            return {"status": "empty", "message": f"remote returned no data for {name}"}

        # Parse based on parser type
        parser = fetch_cfg.get("parser", "raw")
        df = self._parse_remote_output(stdout, parser, fetch_cfg)

        if df is None or df.empty:
            return {"status": "empty", "message": f"parsed no data for {name}"}

        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        logger.info(f"saved {name}: {len(df)} rows to {output_path}")

        return {"status": "ok", "rows": len(df), "file": str(output_path)}

    def _parse_remote_output(self, stdout: str, parser: str, fetch_cfg: dict) -> pd.DataFrame:
        """Parse remote command output into a DataFrame."""
        import io

        if parser == "fred":
            # FRED CLI observations --format csv output
            try:
                df = pd.read_csv(io.StringIO(stdout))
                if "date" in df.columns and "value" in df.columns:
                    df["value"] = pd.to_numeric(df["value"], errors="coerce")
                    df = df.dropna(subset=["value"])
                return df
            except Exception as e:
                logger.warning(f"FRED CSV parse failed: {e}, trying JSON")
                try:
                    import json
                    data = json.loads(stdout)
                    if isinstance(data, list):
                        return pd.DataFrame(data)
                    elif isinstance(data, dict) and "observations" in data:
                        return pd.DataFrame(data["observations"])
                except Exception:
                    pass
                return None
        else:
            # raw: assume CSV
            try:
                return pd.read_csv(io.StringIO(stdout))
            except Exception:
                return None


def fetch_dataset(name: str) -> dict:
    """Fetch a dataset by name using its recipe. Returns status dict."""
    from fmdata.registry import load_recipe, update_dataset_stats, get_dataset, register_dataset

    recipe = load_recipe(name)
    if not recipe:
        return {"status": "error", "message": f"no recipe found for '{name}'"}

    fetcher = RecipeFetcher()
    result = fetcher.fetch(recipe)

    if result.get("status") == "ok":
        output_path = result.get("file", "")
        if output_path and Path(output_path).exists():
            rows = result.get("rows", 0)
            # 若 dataset 还没进 registry(手放 YAML 首次 fetch),用 recipe 信息自动注册,
            # 否则 /data/{name} 会 404(update_dataset_stats 只更新已存在条目)。
            if not get_dataset(name):
                register_dataset(name, {
                    "file": recipe.get("file", ""),
                    "category": recipe.get("category", "unknown"),
                    "rows": 0,
                    "exists": True,
                    "source": recipe.get("source", "unknown"),
                    "update_freq": recipe.get("update_freq", "unknown"),
                    "description": recipe.get("description", ""),
                    "recipe": recipe,
                })
            update_dataset_stats(name, rows=rows)

    return result


def fetch_stale(max_age_hours: int = 24) -> list:
    """Fetch all datasets that are stale (older than max_age_hours)."""
    from fmdata.registry import list_datasets

    results = []
    datasets = list_datasets()
    now = datetime.now()

    for name, ds in datasets.items():
        if ds.get("update_freq") == "on_demand":
            continue
        recipe = ds.get("recipe")
        if not recipe:
            continue

        last = ds.get("last_updated")
        if last:
            try:
                last_dt = pd.to_datetime(str(last))
                age_hours = (now - last_dt).total_seconds() / 3600
                if age_hours < max_age_hours:
                    continue
            except Exception:
                pass

        result = fetch_dataset(name)
        results.append({"name": name, **result})
        time.sleep(0.5)

    return results
