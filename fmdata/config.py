"""fmdata configuration."""
import os
from pathlib import Path

# Base paths
FMDATA_DIR = Path(os.environ.get("FMDATA_DIR", "/home/ubuntu/fmdata"))
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

# HTTP server
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 1934

# Fetcher settings
TUSHARE_RATE_LIMIT = 0.3  # seconds between calls
AKSHARE_RATE_LIMIT = 0.2
MAX_RETRIES = 3
CACHE_TTL = 300  # 5 minutes
