"""Test fixtures for fmdata tests."""
import os
import pytest
from fastapi.testclient import TestClient

# Ensure test env vars are set before importing the app
os.environ.setdefault("FMDATA_DIR", "/home/ubuntu/fmdata")
os.environ.setdefault("TUSHARE_TOKEN", "test_token")
os.environ.setdefault("FMDATA_ADMIN_KEY", "test_admin_key_12345")


@pytest.fixture(scope="session")
def client():
    from fmdata.server import app
    return TestClient(app)


@pytest.fixture
def admin_headers():
    return {"X-API-Key": "test_admin_key_12345"}
