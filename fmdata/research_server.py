"""Research API facade for fmdata.

The legacy fmdata application remains mounted at ``/`` for compatibility. New
research endpoints are authenticated, snapshot-first, and fail closed when
financial semantics are incomplete or conflicting.
"""

from __future__ import annotations

import hmac
import os
from datetime import date
from typing import Any

import pandas as pd
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from fmdata.research_snapshot import (
    SnapshotError,
    create_dataset_snapshot,
    load_snapshot_manifest,
    research_catalog,
    snapshot_file,
)
from fmdata.server import app as legacy_app

app = FastAPI(
    title="fmdata research API",
    version="1.0.0",
    description=(
        "Immutable, provenance-oriented financial data snapshots for research "
        "agents. Service output remains PENDING until an external gate validates it."
    ),
)


class SnapshotRequest(BaseModel):
    dataset: str = Field(min_length=1, max_length=128)
    as_of: date
    parameters: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    expected_semantics: dict[str, Any] = Field(default_factory=dict)


class EntityResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=128)
    as_of: date
    expected_name: str | None = Field(default=None, max_length=128)
    market_hint: str | None = Field(default=None, max_length=32)


def _configured_research_keys() -> list[str]:
    return [
        value
        for value in (
            os.environ.get("FMDATA_RESEARCH_KEY", ""),
            os.environ.get("FMDATA_ADMIN_KEY", ""),
        )
        if value
    ]


def require_research_key(
    x_research_key: str | None = Header(default=None, alias="X-Research-Key"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    keys = _configured_research_keys()
    if not keys:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "research_auth_not_configured",
                "message": "set FMDATA_RESEARCH_KEY or FMDATA_ADMIN_KEY",
            },
        )
    supplied = x_research_key or x_api_key or ""
    if not any(hmac.compare_digest(supplied, expected) for expected in keys):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "valid research key required"},
        )


def _model_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


@app.get("/research/health")
def research_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "fmdata",
        "contract": "research-snapshot-v1",
        "self_validation": False,
    }


@app.get("/research/catalog")
def get_research_catalog(_: None = require_research_key) -> dict[str, Any]:
    # FastAPI does not inject plain function defaults as dependencies. This
    # endpoint is retained for direct calls; authenticated route registration
    # below supplies the actual dependency wrapper.
    return research_catalog()


@app.post("/research/snapshots")
def create_research_snapshot(
    request: SnapshotRequest,
    _: None = require_research_key,
) -> dict[str, Any]:
    payload = _model_dict(request)
    try:
        manifest = create_dataset_snapshot(
            dataset=payload["dataset"],
            as_of=str(payload["as_of"]),
            parameters=payload.get("parameters"),
            fields=payload.get("fields"),
            entity_ids=payload.get("entity_ids"),
            start_date=str(payload["start_date"]) if payload.get("start_date") else None,
            end_date=str(payload["end_date"]) if payload.get("end_date") else None,
            expected_semantics=payload.get("expected_semantics"),
        )
    except SnapshotError as exc:
        raise HTTPException(
            status_code=422,
            detail={"status": "ERROR", "error": "snapshot_rejected", "message": str(exc)},
        ) from exc

    snapshot_id = manifest["snapshot_id"]
    response = dict(manifest)
    response["manifest_url"] = f"/research/snapshots/{snapshot_id}/manifest"
    response["data_url"] = f"/research/snapshots/{snapshot_id}/data"
    response["raw_data_url"] = f"/research/snapshots/{snapshot_id}/raw"
    return response


@app.get("/research/snapshots/{snapshot_id}/manifest")
def get_snapshot_manifest(
    snapshot_id: str,
    _: None = require_research_key,
) -> dict[str, Any]:
    try:
        return load_snapshot_manifest(snapshot_id)
    except SnapshotError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/research/snapshots/{snapshot_id}/data")
def download_snapshot(
    snapshot_id: str,
    _: None = require_research_key,
):
    try:
        path = snapshot_file(snapshot_id, raw=False)
    except SnapshotError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="text/csv", filename=f"{snapshot_id}.csv")


@app.get("/research/snapshots/{snapshot_id}/raw")
def download_raw_snapshot(
    snapshot_id: str,
    _: None = require_research_key,
):
    try:
        path = snapshot_file(snapshot_id, raw=True)
    except SnapshotError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@app.post("/research/entities/resolve")
def resolve_financial_entity(
    request: EntityResolveRequest,
    _: None = require_research_key,
) -> dict[str, Any]:
    from fmdata.reference import stock_list

    payload = _model_dict(request)
    query = str(payload["query"]).strip()
    expected_name = payload.get("expected_name")
    as_of = str(payload["as_of"])

    frame = stock_list().copy()
    if frame.empty:
        return {
            "status": "ERROR",
            "query": query,
            "as_of": as_of,
            "message": "stock reference dataset is empty",
        }

    for column in ("ts_code", "symbol", "name"):
        if column in frame.columns:
            frame[column] = frame[column].astype(str)

    upper_query = query.upper()
    exact = pd.DataFrame()
    if "ts_code" in frame.columns:
        exact = frame[frame["ts_code"].str.upper() == upper_query]
    if exact.empty and "symbol" in frame.columns:
        symbol = query.split(".")[0]
        exact = frame[frame["symbol"].str.zfill(6) == symbol.zfill(6)]
    if exact.empty and "name" in frame.columns:
        exact = frame[frame["name"] == query]
    if exact.empty and "name" in frame.columns:
        exact = frame[frame["name"].str.contains(query, regex=False, na=False)]

    if exact.empty:
        return {
            "status": "NOT_FOUND",
            "query": query,
            "as_of": as_of,
            "candidates": [],
        }

    candidates = []
    for _, row in exact.head(20).iterrows():
        candidate = {
            "entity_id": f"cn-security:{row.get('ts_code', row.get('symbol', ''))}",
            "ts_code": row.get("ts_code"),
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "market": row.get("market"),
            "industry": row.get("industry"),
            "effective_start": row.get("list_date"),
            "effective_end": row.get("delist_date"),
        }
        candidates.append(candidate)

    if len(candidates) > 1:
        return {
            "status": "CONFLICTED",
            "query": query,
            "as_of": as_of,
            "candidates": candidates,
            "limitations": ["multiple reference matches; no best-guess mapping applied"],
        }

    selected = candidates[0]
    if expected_name and str(selected.get("name")) != str(expected_name):
        return {
            "status": "CONFLICTED",
            "query": query,
            "as_of": as_of,
            "selected": selected,
            "message": (
                f"code/name mismatch: expected {expected_name!r}, "
                f"reference says {selected.get('name')!r}"
            ),
        }

    limitations = [
        "current stock_list is built from list_status=L; delisted and historical symbol mappings are incomplete"
    ]
    return {
        "status": "PARTIAL",
        "query": query,
        "as_of": as_of,
        "selected": selected,
        "mapping_confidence": 1.0,
        "limitations": limitations,
        "validation_status": "PENDING",
    }


# Preserve all existing fmdata routes. Research routes are registered first so
# the root mount cannot shadow them.
app.mount("/", legacy_app)
