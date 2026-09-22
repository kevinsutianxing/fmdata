"""fmdata configuration."""
import os
from pathlib import Path

# Base paths
FMDATA_DIR = Path(os.environ.get("FMDATA_DIR", "/home/ubuntu/fmdata"))


def _load_env_file(path) -> None:
    """.env 兜底:systemd EnvironmentFile 只对服务进程生效;独立脚本/交互 python
    import config 时由此读同一份 .env。已 set 的环境变量优先(不覆盖)。"""
    try:
        content = path.read_text()
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_env_file(FMDATA_DIR / ".env")
STORE_DIR = FMDATA_DIR / "store"
RECIPES_DIR = STORE_DIR / "recipes"
REGISTRY_FILE = FMDATA_DIR / "registry.json"

# Store subdirectories
REFERENCE_DIR = STORE_DIR / "reference"
MARKET_DIR = STORE_DIR / "market"
MACRO_DIR = STORE_DIR / "macro"
FUNDAMENTALS_DIR = STORE_DIR / "fundamentals"
FACTORS_DIR = STORE_DIR / "factors"

# API tokens
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
# 东方财富「妙想」MCP Server 认证 key(官方认证 API,非裸连;走 mxapi.eastmoney.com/mxds/mcp)
EM_API_KEY = os.environ.get("EM_API_KEY", "")

# 申万宏源金工 MCP(Streamable HTTP + 6位邀请码;裸 IP 服务器曾迁移过,换地址只改 .env)
SW_MCP_URL = os.environ.get("SW_MCP_URL", "")
SW_MCP_CODE = os.environ.get("SW_MCP_CODE", "")

# HTTP server
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 1934

# Fetcher settings
TUSHARE_RATE_LIMIT = 0.3  # seconds between calls
AKSHARE_RATE_LIMIT = 0.2
MAX_RETRIES = 3
CACHE_TTL = 300  # 5 minutes
