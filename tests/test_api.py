from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.cache import TTLCache
from app.config import Settings
from app.errors import UpstreamTimeout
from app.fetcher import Fetcher
from app.ratelimit import TokenBucket
from app.service import ProfileService
from tests.conftest import FakeTransport, NoPacer, NoProxies


@pytest.fixture
def client(full_html):
    """App wired to a scripted transport, so no network is touched."""
    from app import main
    from app.contact import ContactFetcher, SessionStore

    app = main.app
    app.state.browser = None          # http transport in tests
    app.state.bucket = TokenBucket(10, 60.0)
    app.state.proxy_pool = NoProxies()
    app.state.cache = TTLCache(maxsize=16, ttl=60)
    app.state.service = ProfileService(
        Fetcher(
            pacer=NoPacer(),
            proxy_pool=NoProxies(),
            settings=Settings(max_attempts=4, total_timeout_s=5, use_browser=False),
            transport=FakeTransport([(200, "https://fr.linkedin.com/in/x", full_html)] * 8),
        ),
        app.state.cache,
    )
    app.state.session_store = SessionStore(li_at="tok", jsessionid="ajax:1")
    dash_body = json.dumps(
        {
            "data": {"*elements": ["urn:li:fsd_profile:X"]},
            "included": [
                {
                    "entityUrn": "urn:li:fsd_profile:X",
                    "publicIdentifier": "amelie",
                    "emailAddress": {"emailAddress": "a@b.co"},
                    "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
                }
            ],
        }
    )
    app.state.contact_fetcher = ContactFetcher(
        pacer=NoPacer(),
        proxy_pool=NoProxies(),
        session_store=app.state.session_store,
        settings=Settings(request_timeout_s=5),
        transport=FakeTransport([(200, "u", dash_body)] * 8),
    )
    # Deliberately not used as a context manager: that would run the lifespan
    # and replace the scripted wiring above with a real network-backed fetcher.
    return TestClient(app, raise_server_exceptions=False)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["rate_limit_per_min"] == 10


def test_get_profile_returns_full_json(client):
    response = client.get("/profile", params={"url": "https://www.linkedin.com/in/amelie"})
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Amélie Rousseau"
    assert body["public_id"] == "amelie"
    assert body["location"]["locality"] == "Paris"
    assert len(body["experience"]) == 2
    assert body["meta"]["strategy"] == "fr_guest"
    assert body["meta"]["cached"] is False
    assert body["meta"]["partial"] is False


def test_post_profile(client):
    response = client.post("/profile", json={"url": "amelie-rousseau"})
    assert response.status_code == 200
    assert response.json()["full_name"] == "Amélie Rousseau"


def test_response_shape_is_stable_and_complete(client):
    body = client.get("/profile", params={"url": "amelie"}).json()
    for key in (
        "url", "public_id", "full_name", "first_name", "last_name", "headline",
        "location", "about", "avatar_url", "banner_url", "follower_count",
        "connection_count", "current_company", "current_title", "experience",
        "education", "certifications", "languages", "volunteering", "projects",
        "publications", "honors", "courses", "people_also_viewed",
        "public_contact_hints", "meta",
    ):
        assert key in body, f"missing {key}"


def test_second_call_is_served_from_cache(client):
    first = client.get("/profile", params={"url": "amelie"}).json()
    second = client.get("/profile", params={"url": "amelie"}).json()
    assert first["meta"]["cached"] is False
    assert second["meta"]["cached"] is True
    assert second["full_name"] == first["full_name"]


def test_refresh_bypasses_the_cache(client):
    client.get("/profile", params={"url": "amelie"})
    refreshed = client.get("/profile", params={"url": "amelie", "refresh": "true"}).json()
    assert refreshed["meta"]["cached"] is False


def test_differing_url_shapes_share_one_cache_entry(client):
    client.get("/profile", params={"url": "https://www.linkedin.com/in/amelie/"})
    second = client.get("/profile", params={"url": "amelie"}).json()
    assert second["meta"]["cached"] is True


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "https://example.com/in/x",
        "https://www.linkedin.com/company/microsoft",
        "https://www.linkedin.com/in/",
        "ab",
    ],
)
def test_bad_input_is_400_with_a_reason(client, bad):
    response = client.get("/profile", params={"url": bad})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_URL"
    assert error["message"]


def test_missing_url_param_is_422(client):
    assert client.get("/profile").status_code == 422


def test_unknown_profile_is_404(client, notfound_html):
    client.app.state.service.fetcher._transport = FakeTransport([(404, "u", notfound_html)])
    response = client.get("/profile", params={"url": "definitely-not-a-real-person-xyz"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROFILE_NOT_FOUND"


def test_persistent_authwall_is_502_blocked(client, authwall_html):
    client.app.state.service.fetcher._transport = FakeTransport([(200, "u", authwall_html)] * 4)
    response = client.get("/profile", params={"url": "walled-profile"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "BLOCKED"


def test_timeout_is_504(client):
    client.app.state.service.fetcher._transport = FakeTransport([UpstreamTimeout()] * 4)
    response = client.get("/profile", params={"url": "slow-profile"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


def test_rate_limit_returns_429_with_retry_after(client):
    client.app.state.bucket = TokenBucket(3, 60.0)
    for _ in range(3):
        assert client.get("/profile", params={"url": "amelie", "refresh": "true"}).status_code == 200

    response = client.get("/profile", params={"url": "amelie", "refresh": "true"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) >= 1


def test_rate_limit_is_enforced_before_any_upstream_call(client, full_html):
    transport = FakeTransport([(200, "u", full_html)] * 20)
    client.app.state.service.fetcher._transport = transport
    client.app.state.bucket = TokenBucket(2, 60.0)
    for _ in range(5):
        client.get("/profile", params={"url": "amelie", "refresh": "true"})
    # Only the two permitted requests may reach LinkedIn.
    assert len(transport.calls) == 2


def test_health_is_not_rate_limited(client):
    client.app.state.bucket = TokenBucket(1, 60.0)
    client.get("/profile", params={"url": "amelie"})
    for _ in range(5):
        assert client.get("/health").status_code == 200


def test_unexpected_errors_do_not_leak_internals(client):
    class Boom:
        async def get(self, *a, **kw):
            raise RuntimeError("secret connection string")

    client.app.state.service.fetcher._transport = Boom()
    response = client.get("/profile", params={"url": "amelie", "refresh": "true"})
    assert response.status_code == 500
    assert "secret" not in response.text


class TestContact:
    def test_returns_contact_json(self, client):
        response = client.get("/contact", params={"url": "https://www.linkedin.com/in/amelie"})
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == "a@b.co"
        assert body["public_id"] == "amelie"
        assert body["meta"]["empty"] is False

    def test_bad_url_is_400_before_any_session_use(self, client):
        response = client.get("/contact", params={"url": "https://example.com/in/x"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_URL"

    def test_missing_url_is_422(self, client):
        assert client.get("/contact").status_code == 422

    def test_no_session_configured_is_503(self, client):
        from app.contact import SessionStore

        client.app.state.contact_fetcher.session_store = SessionStore()
        response = client.get("/contact", params={"url": "amelie"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SESSION_NOT_CONFIGURED"

    def test_expired_session_is_502_session_invalid(self, client):
        client.app.state.contact_fetcher._transport = FakeTransport([(403, "u", "")])
        response = client.get("/contact", params={"url": "amelie"})
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "SESSION_INVALID"

    def test_unknown_profile_is_404(self, client):
        client.app.state.contact_fetcher._transport = FakeTransport([(404, "u", "")])
        assert client.get("/contact", params={"url": "ghost"}).status_code == 404

    def test_contact_shares_the_inbound_rate_limit(self, client):
        client.app.state.bucket = TokenBucket(2, 60.0)
        assert client.get("/contact", params={"url": "amelie"}).status_code == 200
        assert client.get("/contact", params={"url": "amelie"}).status_code == 200
        assert client.get("/contact", params={"url": "amelie"}).status_code == 429

    def test_health_reports_session_state(self, client):
        assert client.get("/health").json()["contact_session_configured"] is True


class TestApiKey:
    @pytest.fixture
    def secured(self, client, monkeypatch):
        from app import main

        monkeypatch.setattr(main.settings, "api_key", "s3cret")
        return client

    def test_rejects_missing_key(self, secured):
        response = secured.get("/profile", params={"url": "amelie"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_rejects_wrong_key(self, secured):
        response = secured.get("/profile", params={"url": "amelie"}, headers={"X-API-Key": "nope"})
        assert response.status_code == 401

    def test_accepts_correct_key(self, secured):
        response = secured.get(
            "/profile", params={"url": "amelie"}, headers={"X-API-Key": "s3cret"}
        )
        assert response.status_code == 200

    def test_health_stays_open(self, secured):
        assert secured.get("/health").status_code == 200
