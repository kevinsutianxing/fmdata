#!/usr/bin/env python3
"""westock(腾讯自选股 StockBuddy MCP) access_token 每日续期。

token ~100h 有效且无 refresh_token → 用 .env 里的 fskey 登录会话重铸。
cron 每日 08:37 跑;失败(通常=fskey 会话过期)exit 2 触发飞书告警,
需人工重授权:浏览器开 authorize 链接登录后把 openid+fskey 抄回 .env。
(也可被 _WestockMcpClient 的 401 懒触发调用。)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fmdata.recipe_fetcher import westock_renew_token  # noqa: E402

if __name__ == "__main__":
    tok = westock_renew_token()
    if tok:
        print(f"OK renewed, token head={tok[:12]}...")
    else:
        print("FAIL: fskey 会话可能已过期,需人工重授权(见 .env 注释/recipe 描述)")
        sys.exit(2)
