"""Dataset registry — auto-discovery, recipes, metadata tracking."""
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
import yaml

from fmdata.config import REGISTRY_FILE, STORE_DIR, RECIPES_DIR

logger = logging.getLogger("fmdata.registry")


def _default_registry():
    return {
        "version": 2,
        "updated_at": datetime.now().isoformat(),
        "datasets": {},
    }


def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            reg = json.load(f)
        return reg
    return _default_registry()


def save_registry(reg: dict):
    reg["updated_at"] = datetime.now().isoformat()
    with open(REGISTRY_FILE, "w") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)
    logger.debug(f"registry saved: {len(reg['datasets'])} datasets")


def get_dataset(name: str) -> Optional[dict]:
    reg = load_registry()
    return reg["datasets"].get(name)


def register_dataset(name: str, meta: dict):
    reg = load_registry()
    reg["datasets"][name] = meta
    save_registry(reg)
    logger.info(f"registered dataset: {name}")


def update_dataset_stats(name: str, rows: int = None, date_range: list = None):
    reg = load_registry()
    ds = reg["datasets"].get(name)
    if not ds:
        logger.warning(f"dataset {name} not found in registry")
        return
    if rows is not None:
        ds["rows"] = rows
    if date_range is not None:
        ds["date_range"] = date_range
    ds["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    save_registry(reg)


def list_datasets() -> dict:
    reg = load_registry()
    return reg["datasets"]


def scan_csv(path: Path, date_col: str = None) -> dict:
    """Quick scan of a CSV file to extract row count and date range."""
    if not path.exists():
        return {"rows": 0, "exists": False}

    df = pd.read_csv(path, nrows=5)
    cols = list(df.columns)
    full_rows = sum(1 for _ in open(path)) - 1

    result = {"rows": full_rows, "exists": True, "columns": cols}

    if date_col and date_col in df.columns:
        full_df = pd.read_csv(path, usecols=[date_col])
        dates = full_df[date_col].dropna()
        if len(dates) > 0:
            result["date_range"] = [str(dates.min()), str(dates.max())]

    return result


# ---- Recipe loading ----

def load_recipe(name: str) -> Optional[dict]:
    """Load a single recipe YAML file."""
    recipe_path = RECIPES_DIR / f"{name}.yaml"
    if recipe_path.exists():
        with open(recipe_path) as f:
            return yaml.safe_load(f)
    return None


def load_all_recipes() -> dict:
    """Load all recipe YAML files from store/recipes/."""
    recipes = {}
    if not RECIPES_DIR.exists():
        return recipes
    for path in sorted(RECIPES_DIR.glob("*.yaml")):
        try:
            with open(path) as f:
                recipe = yaml.safe_load(f)
            name = recipe.get("name", path.stem)
            recipes[name] = recipe
        except Exception as e:
            logger.warning(f"failed to load recipe {path}: {e}")
    return recipes


# ---- Auto-discovery ----

CATEGORY_MAP = {
    "reference": "reference",
    "market": "market",
    "macro": "macro",
    "fundamentals": "fundamentals",
    "factors": "factors",
    "overseas": "overseas",
    "sw_extended": "sw_extended",
    "strategy": "strategy",
}

# Known date columns for common files (fallback if recipe doesn't specify)
_DATE_COL_HINTS = {
    "sw_first_level_close": "date",
    "sw_first_level_amount": "date",
    "sw_pe_history": "date",
    "sw_pb_history": "date",
    "hs300_daily": "date",
    "hs300_monthly": "date",
    "north_money": "trade_date",
    "trade_calendar": "cal_date",
    "macro_monthly": "month",
}


def _discover_csv_files() -> dict:
    """Scan store/ for all CSV files and build dataset entries."""
    datasets = {}

    for category_dir in STORE_DIR.iterdir():
        if not category_dir.is_dir():
            continue
        cat_name = category_dir.name
        category = CATEGORY_MAP.get(cat_name, cat_name)

        for csv_path in sorted(category_dir.rglob("*.csv")):
            rel = csv_path.relative_to(STORE_DIR)
            name = csv_path.stem

            # Skip if name already registered (first match wins)
            if name in datasets:
                continue

            date_col = _DATE_COL_HINTS.get(name)
            info = scan_csv(csv_path, date_col)

            datasets[name] = {
                "file": str(rel),
                "category": category,
                "rows": info.get("rows", 0),
                "columns": info.get("columns", []),
                "date_range": info.get("date_range", [None, None]),
                "last_updated": info.get("date_range", [None, None])[-1] if info.get("date_range") else None,
                "exists": info.get("exists", False),
            }

    return datasets


def _discover_dir_datasets() -> dict:
    """Handle directory-based datasets (e.g., fundamentals/stock_fina/)."""
    datasets = {}

    fina_dir = STORE_DIR / "fundamentals" / "stock_fina"
    if fina_dir.exists():
        fina_files = sorted(fina_dir.glob("fina_*.csv"))
        if fina_files:
            latest = fina_files[-1]
            info = scan_csv(latest)
            datasets["stock_fina"] = {
                "file": "fundamentals/stock_fina/",
                "source": "tushare.fina_indicator",
                "update_freq": "quarterly",
                "last_updated": latest.stem.replace("fina_", ""),
                "rows": info["rows"],
                "periods": len(fina_files),
                "date_range": [
                    fina_files[0].stem.replace("fina_", ""),
                    fina_files[-1].stem.replace("fina_", ""),
                ],
                "category": "fundamentals",
                "exists": True,
            }

    ext_path = STORE_DIR / "fundamentals" / "stock_fina_extended" / "extended_combined.csv"
    if ext_path.exists():
        info = scan_csv(ext_path)
        datasets["stock_fina_extended"] = {
            "file": "fundamentals/stock_fina_extended/extended_combined.csv",
            "source": "tushare.fina_indicator",
            "update_freq": "quarterly",
            "rows": info["rows"],
            "columns": info.get("columns", []),
            "category": "fundamentals",
            "exists": True,
        }

    return datasets


def _merge_recipes(datasets: dict) -> dict:
    """Merge recipe metadata into discovered datasets."""
    recipes = load_all_recipes()
    for name, recipe in recipes.items():
        if name in datasets:
            ds = datasets[name]
            ds["recipe"] = recipe
            ds["source"] = recipe.get("source", ds.get("source", "unknown"))
            ds["update_freq"] = recipe.get("update_freq", ds.get("update_freq", "unknown"))
            if recipe.get("description"):
                ds["description"] = recipe["description"]
        else:
            # Recipe exists but no data yet — register as placeholder
            datasets[name] = {
                "file": recipe.get("file", ""),
                "category": recipe.get("category", "unknown"),
                "rows": 0,
                "exists": False,
                "source": recipe.get("source", "unknown"),
                "update_freq": recipe.get("update_freq", "unknown"),
                "description": recipe.get("description", ""),
                "recipe": recipe,
            }
    return datasets


def init_registry_from_store():
    """Full rebuild: auto-discover all CSVs + directory datasets + merge recipes."""
    datasets = {}

    # Layer 1: auto-discover CSV files
    csv_datasets = _discover_csv_files()
    datasets.update(csv_datasets)

    # Layer 2: directory-based datasets (fundamentals etc.)
    dir_datasets = _discover_dir_datasets()
    datasets.update(dir_datasets)

    # Layer 3: merge recipes (adds fetch metadata + placeholders)
    datasets = _merge_recipes(datasets)

    reg = _default_registry()
    reg["datasets"] = datasets
    save_registry(reg)
    logger.info(f"registry initialized: {len(datasets)} datasets ({len(load_all_recipes())} recipes)")
    return reg
