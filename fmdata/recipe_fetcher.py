"""Recipe-based on-demand data fetching with proxy support."""
import logging
import os
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from fmdata.config import STORE_DIR

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
}

# Remote host allowlist
REMOTE_HOST_ALLOWLIST = {"hk43"}


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

        # Step 2: Fetch new data
        logger.info(f"fetching {name} via akshare.{func_name}({params})")
        df = func(**params)

        if df is None or df.empty:
            return {"status": "empty", "message": f"{func_name} returned no data"}

        # Step 3: Merge with existing if incremental
        if existing_df is not None and not existing_df.empty and date_col and date_col in df.columns:
            df = pd.concat([existing_df, df], ignore_index=True)
            if date_col in df.columns:
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
