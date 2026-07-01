"""Tests for /health and /health/data endpoints."""
import pytest


class TestHealth:
    """健康检查端点测试。"""

    def test_health_liveness(self, client):
        """GET /health 返回 200。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "fmdata"

    def test_health_data_structure(self, client):
        """GET /health/data 返回正确的结构。"""
        resp = client.get("/health/data")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_datasets" in data
        assert "empty_count" in data
        assert "stale_count" in data
        assert "empty_datasets" in data
        assert "stale_datasets" in data
        assert data["total_datasets"] >= 0

    def test_health_data_category_filter(self, client):
        """GET /health/data?category=overseas 过滤类别。"""
        resp = client.get("/health/data?category=overseas")
        assert resp.status_code == 200
        data = resp.json()
        # All reported datasets should be overseas
        for ds in data.get("empty_datasets", []):
            assert ds.get("category") == "overseas"
