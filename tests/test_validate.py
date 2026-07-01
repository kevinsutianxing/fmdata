"""Tests for /validate endpoint."""
import pytest


class TestValidate:
    """标的代码验证端点测试。"""

    def test_valid_code_with_suffix(self, client):
        """带后缀的有效代码应该返回 ok。"""
        resp = client.get("/validate?codes=000001.SZ")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"

    def test_valid_code_without_suffix(self, client):
        """不带后缀的有效代码应该自动补全。"""
        resp = client.get("/validate?codes=600519")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        # Should auto-complete to 600519.SH
        assert data["results"][0]["code"] == "600519.SH"
        assert data["results"][0]["name"] == "贵州茅台"

    def test_invalid_code(self, client):
        """无效代码应该返回 422 + valid=false。"""
        resp = client.get("/validate?codes=999999.SZ")
        assert resp.status_code == 422
        data = resp.json()
        assert data["valid"] is False

    def test_known_error_code_warning(self, client):
        """603377 应该触发 known_error warning（ST东时，常被误认为宏和科技）。"""
        resp = client.get("/validate?codes=603377")
        assert resp.status_code == 200
        data = resp.json()
        assert "warnings" in data
        assert any(w["code"].startswith("603377") for w in data["warnings"])

    def test_name_reverse_lookup(self, client):
        """按名称反查应该返回匹配的股票代码。"""
        resp = client.get("/validate?name=贵州茅台")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert any("600519" in r["code"] for r in data["results"])

    def test_multiple_codes(self, client):
        """多个代码逗号分隔。"""
        resp = client.get("/validate?codes=000001.SZ,600519.SH")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert len(data["results"]) == 2

    def test_missing_params(self, client):
        """缺少参数应该返回 400。"""
        resp = client.get("/validate")
        assert resp.status_code == 400
