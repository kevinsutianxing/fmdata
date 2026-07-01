"""Tests for /recipes security restrictions."""
import pytest


class TestRecipeSecurity:
    """Recipe 创建/执行的安全限制测试。"""

    def test_reject_agent_recipe_without_key(self, client):
        """没有 API key 不能创建 source: agent recipe。"""
        resp = client.post("/recipes", json={
            "name": "evil_agent",
            "source": "agent",
            "fetch": {"command": "rm -rf /"},
        })
        assert resp.status_code == 403

    def test_reject_remote_recipe_without_key(self, client):
        """没有 API key 不能创建 source: remote recipe。"""
        resp = client.post("/recipes", json={
            "name": "evil_remote",
            "source": "remote",
            "fetch": {"command": "cat /etc/passwd", "host": "evil"},
        })
        assert resp.status_code == 403

    def test_reject_invalid_name(self, client):
        """recipe name 必须匹配 [A-Za-z0-9_-]+。"""
        for bad_name in ["../../etc/passwd", "a b c", "name;rm", "dt$var"]:
            resp = client.post("/recipes", json={
                "name": bad_name,
                "source": "tushare",
                "fetch": {"func": "daily", "params": {}},
            })
            assert resp.status_code == 400, f"expected 400 for name '{bad_name}', got {resp.status_code}"

    def test_create_tushare_recipe_no_auth(self, client):
        """tushare recipe 不需要 auth（safe source）。"""
        # Use a unique name to avoid conflicts
        resp = client.post("/recipes", json={
            "name": "test_safe_recipe_xyz",
            "source": "tushare",
            "fetch": {"func": "daily", "params": {"ts_code": "000001.SZ"}},
            "_auto_fetch": False,
        })
        # Should succeed (201 or 200) or 409 if already exists
        assert resp.status_code in (200, 409)

    def test_fetch_agent_requires_key(self, client):
        """POST /fetch/{name} 对 agent recipe 需要鉴权。"""
        # Try to fetch an existing agent recipe without key
        resp = client.post("/fetch/tech_signals")
        assert resp.status_code == 403

    def test_fetch_stale_requires_key(self, client):
        """POST /fetch-stale 需要鉴权。"""
        resp = client.post("/fetch-stale")
        assert resp.status_code == 403

    def test_fetch_nonexistent_with_key_passes_auth(self, client, admin_headers):
        """带 API key 访问不存在的 recipe 应该返回 404（不是 403）——证明 auth 通过。"""
        resp = client.post("/fetch/nonexistent_recipe_xyz", headers=admin_headers)
        # Auth passed (not 403), but recipe not found
        assert resp.status_code == 404

    def test_wrong_key_rejected(self, client):
        """错误的 API key 应该被拒绝。"""
        resp = client.post("/recipes", json={
            "name": "evil_agent2",
            "source": "agent",
            "fetch": {"command": "echo pwned"},
        }, headers={"X-API-Key": "wrong_key"})
        assert resp.status_code == 403
