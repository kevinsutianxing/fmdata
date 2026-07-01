"""Macro data layer — CPI, PPI, PMI, interest rates, etc."""
import logging
from pathlib import Path

import pandas as pd

from fmdata.config import MACRO_DIR

logger = logging.getLogger("fmdata.macro")

_VALID_NAMES = {"cpi", "ppi", "pmi", "money_supply", "credit", "lpr", "shibor", "macro_monthly"}


def get(name: str) -> pd.DataFrame:
    """Load a macro dataset by name.

    Valid names: cpi, ppi, pmi, money_supply, credit, lpr, shibor, macro_monthly
    """
    if name not in _VALID_NAMES:
        logger.warning(f"unknown macro dataset: {name}")
        return pd.DataFrame()

    path = MACRO_DIR / f"{name}.csv"
    if not path.exists():
        logger.warning(f"macro file not found: {path}")
        return pd.DataFrame()
    return pd.read_csv(path)


def macro_monthly() -> pd.DataFrame:
    """Combined macro monthly indicators."""
    return get("macro_monthly")
