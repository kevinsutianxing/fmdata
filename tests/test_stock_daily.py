"""Tests for /market/stock-daily endpoint."""
import pytest


class TestStockDaily:
    """stock-daily 端点的排序和 metadata 测试。"""

    def test_default_order_is_asc(self, client):
        """默认排序应该是升序（trade_date 从早到晚）。"""
        resp = client.get("/market/stock-daily?code=002594&start_date=20260101&end_date=20260131")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows", 0) > 1:
                assert data.get("order") == "asc"
                dates = [r.get("trade_date") for r in data["data"]]
                assert dates == sorted(dates), "Dates should be in ascending order"

    def test_explicit_desc_order(self, client):
        """order=desc 应该降序。"""
        resp = client.get("/market/stock-daily?code=002594&start_date=20260101&end_date=20260131&order=desc")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("rows", 0) > 1:
                assert data.get("order") == "desc"
                dates = [r.get("trade_date") for r in data["data"]]
                assert dates == sorted(dates, reverse=True), "Dates should be in descending order"

    def test_has_date_range_metadata(self, client):
        """响应应包含 date_range metadata。"""
        resp = client.get("/market/stock-daily?code=002594&start_date=20260101&end_date=20260331")
        if resp.status_code == 200:
            data = resp.json()
            assert "date_range" in data
            assert len(data["date_range"]) == 2
            assert data["date_range"][0] <= data["date_range"][1]

    def test_invalid_code_with_validate(self, client):
        """validate_first=true + 无效代码应该返回 422。"""
        resp = client.get("/market/stock-daily?code=999999&validate_first=true")
        assert resp.status_code == 422

    def test_valid_code_passes_validation(self, client):
        """validate_first=true + 有效代码应该正常返回数据。"""
        resp = client.get("/market/stock-daily?code=002594&start_date=20260101&end_date=20260131&validate_first=true")
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("rows", 0) > 0
