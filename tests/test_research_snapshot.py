from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from fmdata import research_snapshot


def configure_dataset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    semantics: dict | None = None,
    rows: list[dict] | None = None,
):
    store = tmp_path / "store"
    snapshot_root = store / "_snapshots"
    data_path = store / "market" / "sample.csv"
    data_path.parent.mkdir(parents=True)
    frame = pd.DataFrame(
        rows
        or [
            {
                "trade_date": "2026-06-27",
                "available_at": "2026-06-27T10:00:00Z",
                "ts_code": "000001.SZ",
                "close": 10.0,
            },
            {
                "trade_date": "2026-06-30",
                "available_at": "2026-06-30T10:00:00Z",
                "ts_code": "000002.SZ",
                "close": 20.0,
            },
        ]
    )
    frame.to_csv(data_path, index=False)

    metadata = {
        "file": "market/sample.csv",
        "category": "market",
        "rows": len(frame),
        "source": "tushare",
    }
    recipe = {
        "name": "sample",
        "category": "market",
        "source": "tushare",
        "fetch": {"func": "daily", "date_col": "trade_date"},
        "semantics": semantics
        or {
            "frequency": "daily",
            "timezone": "Asia/Shanghai",
            "revision_policy": "original_provider_response",
            "available_at_col": "available_at",
            "unit": "CNY/share",
            "currency": "CNY",
            "adjustment": "raw",
            "license_or_usage_note": "internal research use under provider terms",
        },
    }

    monkeypatch.setattr(research_snapshot, "STORE_DIR", store)
    monkeypatch.setattr(research_snapshot, "SNAPSHOT_ROOT", snapshot_root)
    monkeypatch.setattr(research_snapshot, "list_datasets", lambda: {"sample": metadata})
    monkeypatch.setattr(research_snapshot, "load_recipe", lambda name: recipe if name == "sample" else None)
    return store, snapshot_root, data_path


def test_snapshot_is_content_addressed_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _, root, _ = configure_dataset(monkeypatch, tmp_path)

    first = research_snapshot.create_dataset_snapshot(
        dataset="sample",
        as_of="2026-06-30",
        fields=["close"],
        entity_ids=["000001.SZ"],
        start_date="2026-06-01",
        end_date="2026-06-30",
        expected_semantics={"adjustment": "raw", "currency": "CNY"},
    )
    second = research_snapshot.create_dataset_snapshot(
        dataset="sample",
        as_of="2026-06-30",
        fields=["close"],
        entity_ids=["000001.SZ"],
        start_date="2026-06-01",
        end_date="2026-06-30",
        expected_semantics={"adjustment": "raw", "currency": "CNY"},
    )

    assert first["status"] == "OK"
    assert first["validation_status"] == "PENDING"
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["content_sha256"] == second["content_sha256"]
    assert first["row_count"] == 1

    normalized = root / first["snapshot_path"]
    raw = root / first["raw_snapshot_path"]
    assert hashlib.sha256(normalized.read_bytes()).hexdigest() == first["content_sha256"]
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == first["raw_content_sha256"]
    assert json.loads((root / first["manifest_path"]).read_text())["snapshot_id"] == first["snapshot_id"]


def test_missing_financial_semantics_returns_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    configure_dataset(
        monkeypatch,
        tmp_path,
        semantics={"date_col": "trade_date"},
    )
    result = research_snapshot.create_dataset_snapshot(
        dataset="sample",
        as_of="2026-06-30",
    )
    assert result["status"] == "PARTIAL"
    assert result["validation_status"] == "PENDING"
    assert any("point-in-time availability" in item for item in result["limitations"])
    assert any("adjustment" in item for item in result["limitations"])


def test_future_available_data_is_conflicted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    configure_dataset(
        monkeypatch,
        tmp_path,
        rows=[
            {
                "trade_date": "2026-06-30",
                "available_at": "2026-07-01T01:00:00Z",
                "ts_code": "000001.SZ",
                "close": 10.0,
            }
        ],
    )
    result = research_snapshot.create_dataset_snapshot(
        dataset="sample",
        as_of="2026-06-30",
    )
    assert result["status"] == "CONFLICTED"
    assert any("exceeds research as_of" in item for item in result["conflicts"])


def test_secret_values_are_not_written_to_request_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    configure_dataset(monkeypatch, tmp_path)
    result = research_snapshot.create_dataset_snapshot(
        dataset="sample",
        as_of="2026-06-30",
        parameters={"api_key": "top-secret", "nested": {"password": "hidden"}},
    )
    encoded = json.dumps(result["query_parameters"])
    assert "top-secret" not in encoded
    assert "hidden" not in encoded
    assert encoded.count("***REDACTED***") == 2


def test_path_escape_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    store = tmp_path / "store"
    monkeypatch.setattr(research_snapshot, "STORE_DIR", store)
    monkeypatch.setattr(research_snapshot, "SNAPSHOT_ROOT", store / "_snapshots")
    monkeypatch.setattr(
        research_snapshot,
        "list_datasets",
        lambda: {"bad": {"file": "../secret.csv", "category": "market"}},
    )
    monkeypatch.setattr(research_snapshot, "load_recipe", lambda name: None)
    with pytest.raises(research_snapshot.SnapshotError, match="escapes store root"):
        research_snapshot.create_dataset_snapshot(dataset="bad", as_of="2026-06-30")


def test_research_routes_fail_closed_without_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FMDATA_RESEARCH_KEY", raising=False)
    monkeypatch.delenv("FMDATA_ADMIN_KEY", raising=False)
    from fmdata.research_server import app

    client = TestClient(app)
    response = client.get("/research/catalog")
    assert response.status_code == 503


def test_research_routes_accept_configured_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FMDATA_RESEARCH_KEY", "research-only")
    from fmdata import research_server

    monkeypatch.setattr(
        research_server,
        "research_catalog",
        lambda: {"datasets": {}, "count": 0},
    )
    client = TestClient(research_server.app)
    response = client.get(
        "/research/catalog",
        headers={"X-Research-Key": "research-only"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0
