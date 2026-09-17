"""Boots the app exactly as the container does.

Every other API test injects a fake transport; this one runs the real lifespan so
a broken config default, a bad dependency wiring or an import-time error in the
production path is caught here rather than in Coolify.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def _http_transport(monkeypatch):
    """Boot the real lifespan but on the HTTP transport — launching a real
    Chromium in unit tests would be slow and require the browser installed."""
    monkeypatch.setattr(settings, "use_browser", False)


def test_lifespan_boots_and_health_reports_real_state():
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["rate_limit_per_min"] == 10
    assert body["proxies"] == {"configured": 0, "cooling_down": 0, "available": 0}


def test_real_wiring_rejects_bad_input_without_touching_the_network():
    with TestClient(app) as client:
        response = client.get("/profile", params={"url": "https://example.com/in/x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_URL"


def test_missing_proxy_file_does_not_prevent_startup():
    # The default PROXY_FILE lives on a Coolify volume that may not be mounted.
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_contact_without_a_session_is_a_clean_503_not_a_crash():
    # Default config carries no session, and no /data/session.txt is mounted.
    with TestClient(app) as client:
        response = client.get("/contact", params={"url": "someone"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SESSION_NOT_CONFIGURED"


def test_openapi_schema_is_generated():
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    assert "/profile" in schema["paths"]
    assert "/contact" in schema["paths"]
    assert "Profile" in schema["components"]["schemas"]
    assert "ContactInfo" in schema["components"]["schemas"]
