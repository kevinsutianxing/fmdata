"""fmdata — unified financial data middleware."""
import logging

logging.getLogger("fmdata").addHandler(logging.StreamHandler())
logging.getLogger("fmdata").setLevel(logging.INFO)

# Reference layer
from fmdata.reference import (
    trade_calendar,
    is_trade_day,
    last_trade_day,
    next_trade_day,
    prev_trade_day,
    stock_list,
    tech_stock_list,
    industry_list,
    stock_industry_map,
    get_industry,
    get_industry_stocks,
    update_reference,
)

# Market layer
from fmdata.market import (
    daily_matrix,
    sw_industry_close,
    sw_industry_amount,
    sw_pe_history,
    sw_pb_history,
    sw_main_flow_net,
    sw_main_flow_pct,
    hs300,
    tech_indicators,
    turnover_matrix,
    north_money,
    stock_fina,
    stock_fina_extended,
    factor_matrix,
    sw_members,
    data_status,
)

# Macro layer
from fmdata.macro import get as macro, macro_monthly

# Registry
from fmdata.registry import list_datasets, init_registry_from_store
