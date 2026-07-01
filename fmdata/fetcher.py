"""Unified data fetching layer with rate limiting, retry, and caching."""
import time
import logging
import functools
from datetime import datetime

import tushare as ts
import akshare as ak
import pandas as pd

from fmdata.config import (
    TUSHARE_TOKEN,
    TUSHARE_RATE_LIMIT,
    AKSHARE_RATE_LIMIT,
    MAX_RETRIES,
    CACHE_TTL,
)

logger = logging.getLogger("fmdata.fetcher")


class _RateLimiter:
    def __init__(self, interval):
        self.interval = interval
        self._last_call = 0.0

    def wait(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.time()


class _Cache:
    def __init__(self, ttl):
        self.ttl = ttl
        self._store = {}

    def get(self, key):
        if key in self._store:
            result, ts = self._store[key]
            if time.time() - ts < self.ttl:
                return result
            del self._store[key]
        return None

    def set(self, key, value):
        self._store[key] = (value, time.time())

    def clear(self):
        self._store.clear()


class TushareFetcher:
    def __init__(self, token=None):
        self._token = token or TUSHARE_TOKEN
        if not self._token:
            raise ValueError("TUSHARE_TOKEN not set (env var or constructor arg)")
        self._pro = ts.pro_api(self._token)
        self._limiter = _RateLimiter(TUSHARE_RATE_LIMIT)
        self._cache = _Cache(CACHE_TTL)

    def _call(self, api_name, cache_key=None, **kwargs):
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug(f"cache hit: {cache_key}")
                return cached

        for attempt in range(MAX_RETRIES):
            try:
                self._limiter.wait()
                func = getattr(self._pro, api_name)
                df = func(**kwargs)
                if cache_key and df is not None:
                    self._cache.set(cache_key, df)
                logger.info(f"tushare.{api_name}({kwargs}) -> {len(df) if df is not None else 0} rows")
                return df
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"tushare.{api_name} attempt {attempt+1}/{MAX_RETRIES} failed: {e}, retry in {wait}s")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait)
                else:
                    raise

    def daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        params = {k: v for k, v in locals().items() if v is not None and k != 'self'}
        key = f"daily:{sorted(params.items())}"
        return self._call("daily", key, **params)

    def daily_basic(self, ts_code=None, trade_date=None, start_date=None, end_date=None,
                    fields=None):
        params = {k: v for k, v in locals().items() if v is not None and k != 'self'}
        key = f"daily_basic:{sorted(params.items())}"
        return self._call("daily_basic", key, **params)

    def fina_indicator(self, ts_code=None, end_date=None, start_date=None, fields=None):
        params = {k: v for k, v in locals().items() if v is not None and k != 'self'}
        key = f"fina_indicator:{sorted(params.items())}"
        return self._call("fina_indicator", key, **params)

    def trade_cal(self, exchange="SSE", start_date=None, end_date=None):
        params = {k: v for k, v in locals().items() if v is not None and k != 'self'}
        key = f"trade_cal:{sorted(params.items())}"
        return self._call("trade_cal", key, **params)

    def stock_basic(self, exchange="", list_status="L", fields=None):
        params = {k: v for k, v in locals().items() if v is not None and k != 'self'}
        key = f"stock_basic:{sorted(params.items())}"
        return self._call("stock_basic", key, **params)

    def index_classify(self, level="L1", src="SW2021"):
        return self._call("index_classify", f"index_classify:{level}:{src}",
                          level=level, src=src)

    def index_member(self, index_code=""):
        return self._call("index_member", f"index_member:{index_code}",
                          index_code=index_code)

    def moneyflow(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        params = {k: v for k, v in locals().items() if v is not None and k != 'self'}
        key = f"moneyflow:{sorted(params.items())}"
        return self._call("moneyflow", key, **params)

    def clear_cache(self):
        self._cache.clear()


class AkshareFetcher:
    def __init__(self):
        self._limiter = _RateLimiter(AKSHARE_RATE_LIMIT)
        self._cache = _Cache(CACHE_TTL)

    def _call(self, func_name, cache_key=None, **kwargs):
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        for attempt in range(MAX_RETRIES):
            try:
                self._limiter.wait()
                func = getattr(ak, func_name)
                df = func(**kwargs)
                if cache_key and df is not None:
                    self._cache.set(cache_key, df)
                logger.info(f"akshare.{func_name}({kwargs}) -> {len(df) if df is not None else 0} rows")
                return df
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"akshare.{func_name} attempt {attempt+1}/{MAX_RETRIES} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(wait)
                else:
                    raise

    def sw_index_daily(self, symbol="801010", start_date="20200101", end_date=None):
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")
        key = f"sw_index_daily:{symbol}:{start_date}:{end_date}"
        return self._call("sw_index_daily_em", key, symbol=symbol,
                          start_date=start_date, end_date=end_date)

    def stock_zh_a_spot_em(self):
        return self._call("stock_zh_a_spot_em", "stock_zh_a_spot_em")

    def clear_cache(self):
        self._cache.clear()
