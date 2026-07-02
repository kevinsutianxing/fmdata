"""Research-grade immutable snapshot support for fmdata.

This module deliberately separates three concepts:

1. the mutable canonical cache under ``store/``;
2. immutable content-addressed snapshots under ``store/_snapshots``;
3. research validation, which remains PENDING until an external gate approves it.

The snapshot service never promotes its own data to VALIDATED.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from fmdata.config import STORE_DIR
from fmdata.registry import list_datasets, load_recipe

SNAPSHOT_ROOT = Path(
    os.environ.get("FMDATA_SNAPSHOT_DIR", str(STORE_DIR / "_snapshots"))
)

_DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")
_PERIOD_RE = re.compile(r"^\d{8}$")
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|pwd|auth|api[_-]?key|cookie)", re.IGNORECASE
)


class SnapshotError(ValueError):
    """A deterministic request, path, or semantic validation error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def redact(value: Any) -> Any:
    """Recursively redact credentials before writing request metadata."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result[key] = "***REDACTED***" if _SECRET_KEY_RE.search(str(key)) else redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _safe_child(root: Path, relative: str) -> Path:
    if not relative:
        raise SnapshotError("dataset has no file path")
    root = root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SnapshotError(f"path escapes store root: {relative}") from exc
    return target


def _normalize_date(value: str | None, field: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if not _DATE_RE.match(text):
        raise SnapshotError(f"{field} must be YYYY-MM-DD or YYYYMMDD")
    parsed = pd.to_datetime(text, errors="raise")
    return parsed.date().isoformat()


def _resolve_dataset_file(
    dataset: str,
    metadata: dict[str, Any],
    parameters: dict[str, Any],
) -> Path:
    relative = str(metadata.get("file") or "")
    if relative.endswith("/"):
        if dataset != "stock_fina":
            raise SnapshotError(
                f"directory dataset '{dataset}' requires a dedicated resolver"
            )
        period = str(parameters.get("period") or "")
        if not _PERIOD_RE.fullmatch(period):
            raise SnapshotError("stock_fina snapshot requires period=YYYYMMDD")
        relative = f"fundamentals/stock_fina/fina_{period}.csv"

    path = _safe_child(STORE_DIR, relative)
    if not path.is_file():
        raise SnapshotError(f"dataset file is missing: {relative}")
    return path


def _semantic_metadata(
    dataset: str,
    metadata: dict[str, Any],
    recipe: dict[str, Any] | None,
) -> dict[str, Any]:
    recipe = recipe or {}
    semantics = dict(recipe.get("semantics") or {})
    fetch = dict(recipe.get("fetch") or {})

    # date_col is a mechanical field and may safely fall back to fetch config.
    semantics.setdefault("date_col", fetch.get("date_col"))
    semantics.setdefault("published_at_col", fetch.get("published_at_col"))
    semantics.setdefault("available_at_col", fetch.get("available_at_col"))
    semantics.setdefault("source", recipe.get("source") or metadata.get("source"))
    semantics.setdefault("source_func", fetch.get("func"))
    semantics.setdefault("dataset", dataset)
    return semantics


def _research_limitations(
    category: str,
    semantics: dict[str, Any],
) -> list[str]:
    limitations: list[str] = []

    for field in ("frequency", "timezone", "revision_policy"):
        if not semantics.get(field):
            limitations.append(f"missing semantic metadata: {field}")

    if not semantics.get("license_or_usage_note"):
        limitations.append("missing licensing/storage/redistribution metadata")

    if not (semantics.get("available_at_col") or semantics.get("available_at_rule")):
        limitations.append(
            "point-in-time availability is not defined; historical use must be blocked"
        )

    if category in {"market", "factors", "strategy"}:
        if not semantics.get("adjustment"):
            limitations.append("price/return adjustment convention is not defined")
        if not semantics.get("currency"):
            limitations.append("currency is not defined")

    if category == "fundamentals":
        for field in ("accounting_scope", "period_basis"):
            if not semantics.get(field):
                limitations.append(f"missing fundamental semantic metadata: {field}")
        if not (semantics.get("published_at_col") or semantics.get("published_at_rule")):
            limitations.append("filing/announcement publication timing is not defined")

    if category == "macro" and not semantics.get("vintage_policy"):
        limitations.append("macro release-vintage policy is not defined")

    return limitations


def _schema_fingerprint(df: pd.DataFrame) -> str:
    schema = [
        {"name": str(column), "dtype": str(df[column].dtype)}
        for column in df.columns
    ]
    return sha256_bytes(canonical_json(schema))


def _date_bounds(df: pd.DataFrame, date_col: str | None) -> tuple[str | None, str | None]:
    if not date_col or date_col not in df.columns or df.empty:
        return None, None
    parsed = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if parsed.empty:
        return None, None
    return parsed.min().date().isoformat(), parsed.max().date().isoformat()


def _max_timestamp(df: pd.DataFrame, column: str | None) -> str | None:
    if not column or column not in df.columns or df.empty:
        return None
    parsed = pd.to_datetime(df[column], errors="coerce", utc=True).dropna()
    if parsed.empty:
        return None
    return parsed.max().isoformat()


def _apply_request_filters(
    df: pd.DataFrame,
    *,
    fields: list[str] | None,
    entity_ids: list[str] | None,
    start_date: str | None,
    end_date: str | None,
    date_col: str | None,
) -> pd.DataFrame:
    filtered = df.copy()

    if entity_ids:
        entity_col = next(
            (
                name
                for name in ("entity_id", "ts_code", "code", "symbol", "index_code")
                if name in filtered.columns
            ),
            None,
        )
        if entity_col is None:
            raise SnapshotError("entity_ids supplied but dataset has no entity column")
        requested = {str(value) for value in entity_ids}
        filtered = filtered[filtered[entity_col].astype(str).isin(requested)]

    if start_date or end_date:
        if not date_col or date_col not in filtered.columns:
            raise SnapshotError("date range supplied but date_col is unavailable")
        parsed = pd.to_datetime(filtered[date_col], errors="coerce")
        if start_date:
            filtered = filtered[parsed >= pd.Timestamp(start_date)]
            parsed = pd.to_datetime(filtered[date_col], errors="coerce")
        if end_date:
            filtered = filtered[parsed <= pd.Timestamp(end_date)]

    if fields:
        missing = [field for field in fields if field not in filtered.columns]
        if missing:
            raise SnapshotError(f"requested fields are missing: {missing}")
        required_columns: list[str] = []
        for name in (date_col, "entity_id", "ts_code", "code", "symbol", "index_code"):
            if name and name in filtered.columns and name not in required_columns:
                required_columns.append(name)
        selected = required_columns + [field for field in fields if field not in required_columns]
        filtered = filtered[selected]

    return filtered.reset_index(drop=True)


def _write_content_addressed(directory: Path, digest: str, suffix: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}{suffix}"
    if target.exists():
        if sha256_bytes(target.read_bytes()) != digest:
            raise SnapshotError(f"existing content-addressed object is corrupt: {target}")
        return target
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target


def _relative_snapshot_path(path: Path) -> str:
    return str(path.resolve().relative_to(SNAPSHOT_ROOT.resolve()))


def create_dataset_snapshot(
    *,
    dataset: str,
    as_of: str,
    parameters: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    entity_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    expected_semantics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or reuse an immutable snapshot for a registered dataset.

    The method does not fetch an arbitrary provider function. Refreshing the
    mutable cache remains an explicitly authenticated operation handled by the
    existing recipe endpoint or an external scheduler.
    """
    as_of_normalized = _normalize_date(as_of, "as_of")
    start_normalized = _normalize_date(start_date, "start_date")
    end_normalized = _normalize_date(end_date, "end_date")
    if start_normalized and end_normalized and start_normalized > end_normalized:
        raise SnapshotError("start_date is after end_date")

    datasets = list_datasets()
    metadata = datasets.get(dataset)
    if not metadata:
        raise SnapshotError(f"dataset is not registered: {dataset}")

    parameters = dict(parameters or {})
    recipe = load_recipe(dataset)
    semantics = _semantic_metadata(dataset, metadata, recipe)
    source_path = _resolve_dataset_file(dataset, metadata, parameters)
    raw_bytes = source_path.read_bytes()
    raw_hash = sha256_bytes(raw_bytes)

    try:
        frame = pd.read_csv(source_path)
    except Exception as exc:
        raise SnapshotError(f"cannot parse dataset CSV: {exc}") from exc

    date_col = semantics.get("date_col")
    filtered = _apply_request_filters(
        frame,
        fields=fields,
        entity_ids=entity_ids,
        start_date=start_normalized,
        end_date=end_normalized,
        date_col=date_col,
    )

    normalized_bytes = filtered.to_csv(index=False, lineterminator="\n").encode("utf-8")
    normalized_hash = sha256_bytes(normalized_bytes)

    redacted_request = redact(
        {
            "dataset": dataset,
            "as_of": as_of_normalized,
            "parameters": parameters,
            "fields": fields or [],
            "entity_ids": entity_ids or [],
            "start_date": start_normalized,
            "end_date": end_normalized,
            "expected_semantics": expected_semantics or {},
        }
    )
    request_hash = sha256_bytes(canonical_json(redacted_request))
    snapshot_id = f"fmdata-{dataset}-{request_hash[:12]}-{normalized_hash[:12]}"

    raw_target = _write_content_addressed(
        SNAPSHOT_ROOT / "raw", raw_hash, source_path.suffix or ".bin", raw_bytes
    )
    normalized_target = _write_content_addressed(
        SNAPSHOT_ROOT / "normalized", normalized_hash, ".csv", normalized_bytes
    )

    observation_start, observation_end = _date_bounds(filtered, date_col)
    published_at = _max_timestamp(filtered, semantics.get("published_at_col"))
    available_at = _max_timestamp(filtered, semantics.get("available_at_col"))
    retrieved_at = utc_now()

    category = str(metadata.get("category") or recipe.get("category") if recipe else "unknown")
    limitations = _research_limitations(category, semantics)
    conflicts: list[str] = []
    for key, expected in (expected_semantics or {}).items():
        actual = semantics.get(key)
        if expected is not None and actual != expected:
            conflicts.append(
                f"semantic mismatch for {key}: expected {expected!r}, got {actual!r}"
            )

    if available_at:
        available_date = pd.Timestamp(available_at).date().isoformat()
        if available_date > as_of_normalized:
            conflicts.append(
                f"dataset availability {available_date} exceeds research as_of {as_of_normalized}"
            )

    provider = str(semantics.get("source") or "unknown")
    source_func = semantics.get("source_func")
    source_locator = f"fmdata://{provider}/{source_func or dataset}"
    recipe_hash = sha256_bytes(canonical_json(recipe or {}))

    status = "CONFLICTED" if conflicts else ("PARTIAL" if limitations else "OK")
    manifest: dict[str, Any] = {
        "status": status,
        "snapshot_id": snapshot_id,
        "source_id": f"fmdata:{provider}:{dataset}:{raw_hash[:16]}",
        "evidence_id": f"evidence:{snapshot_id}",
        "dataset_id": f"fmdata:{dataset}:{normalized_hash[:16]}",
        "provider": provider,
        "source_locator": source_locator,
        "query_parameters": redacted_request,
        "request_sha256": request_hash,
        "retrieved_at": retrieved_at,
        "as_of": as_of_normalized,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "published_at": published_at,
        "available_at": available_at,
        "available_at_rule": semantics.get("available_at_rule"),
        "snapshot_path": _relative_snapshot_path(normalized_target),
        "content_sha256": normalized_hash,
        "raw_snapshot_path": _relative_snapshot_path(raw_target),
        "raw_content_sha256": raw_hash,
        "manifest_path": f"metadata/{snapshot_id}.json",
        "row_count": int(len(filtered)),
        "source_row_count": int(len(frame)),
        "schema_fingerprint": _schema_fingerprint(filtered),
        "entity_ids": entity_ids or [],
        "fields": [str(column) for column in filtered.columns],
        "unit": semantics.get("unit"),
        "scale": semantics.get("scale"),
        "currency": semantics.get("currency"),
        "timezone": semantics.get("timezone"),
        "frequency": semantics.get("frequency"),
        "adjustment": semantics.get("adjustment"),
        "revision_policy": semantics.get("revision_policy"),
        "vintage_policy": semantics.get("vintage_policy"),
        "accounting_scope": semantics.get("accounting_scope"),
        "period_basis": semantics.get("period_basis"),
        "license_or_usage_note": semantics.get("license_or_usage_note"),
        "recipe_sha256": recipe_hash,
        "limitations": limitations,
        "conflicts": conflicts,
        "validation_status": "PENDING",
        "service": {"name": "fmdata", "snapshot_contract_version": 1},
    }

    metadata_dir = SNAPSHOT_ROOT / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    manifest_target = metadata_dir / f"{snapshot_id}.json"
    encoded_manifest = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if manifest_target.exists():
        existing = json.loads(manifest_target.read_text(encoding="utf-8"))
        # retrieved_at is operational and may differ. All identity-bearing fields
        # must remain stable for an idempotent snapshot.
        stable_keys = (
            "snapshot_id",
            "request_sha256",
            "content_sha256",
            "raw_content_sha256",
            "row_count",
            "schema_fingerprint",
        )
        if any(existing.get(key) != manifest.get(key) for key in stable_keys):
            raise SnapshotError(f"snapshot identity collision: {snapshot_id}")
        return existing

    temporary = manifest_target.with_suffix(".json.tmp")
    temporary.write_bytes(encoded_manifest)
    temporary.replace(manifest_target)
    return manifest


def load_snapshot_manifest(snapshot_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", snapshot_id):
        raise SnapshotError("invalid snapshot_id")
    path = _safe_child(SNAPSHOT_ROOT, f"metadata/{snapshot_id}.json")
    if not path.is_file():
        raise SnapshotError(f"snapshot not found: {snapshot_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot_file(snapshot_id: str, *, raw: bool = False) -> Path:
    manifest = load_snapshot_manifest(snapshot_id)
    relative = manifest["raw_snapshot_path" if raw else "snapshot_path"]
    path = _safe_child(SNAPSHOT_ROOT, relative)
    if not path.is_file():
        raise SnapshotError(f"snapshot object is missing: {relative}")
    expected = manifest["raw_content_sha256" if raw else "content_sha256"]
    actual = sha256_bytes(path.read_bytes())
    if actual != expected:
        raise SnapshotError(f"snapshot hash mismatch: {relative}")
    return path


def research_catalog() -> dict[str, Any]:
    datasets = list_datasets()
    result: dict[str, Any] = {}
    for name, metadata in sorted(datasets.items()):
        recipe = load_recipe(name)
        semantics = _semantic_metadata(name, metadata, recipe)
        limitations = _research_limitations(
            str(metadata.get("category") or (recipe or {}).get("category") or "unknown"),
            semantics,
        )
        result[name] = {
            "category": metadata.get("category"),
            "source": semantics.get("source"),
            "rows": metadata.get("rows", 0),
            "date_range": metadata.get("date_range"),
            "file": metadata.get("file"),
            "semantics": semantics,
            "research_ready": not limitations,
            "limitations": limitations,
        }
    return {"datasets": result, "count": len(result)}
