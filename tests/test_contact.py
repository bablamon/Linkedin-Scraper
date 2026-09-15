from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.contact import (
    AuthSession,
    ContactFetcher,
    SessionStore,
    find_profile_entity,
    parse_contact,
)
from app.errors import (
    Blocked,
    EndpointRetired,
    ProfileNotFound,
    SessionInvalid,
    SessionNotConfigured,
    UpstreamError,
    UpstreamTimeout,
)
from tests.conftest import FakeTransport, NoPacer, NoProxies

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def dash_body() -> str:
    return (FIXTURES / "contact_dash.json").read_text(encoding="utf-8")


# A dash response for a member who shared nothing with this viewer.
EMPTY_DASH = json.dumps(
    {
        "data": {"*elements": ["urn:li:fsd_profile:ACoAAX"]},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:ACoAAX",
                "publicIdentifier": "somebody",
                "emailAddress": None,
                "phoneNumbers": None,
                "websites": [],
                "twitterHandles": [],
                "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
            }
        ],
    }
)

# A dash response where the identifier resolved to nothing.
NO_PROFILE_DASH = json.dumps({"data": {"*elements": []}, "included": []})


def make_fetcher(script, *, session=("li_at_value", "ajax:123"), proxy_pool=None):
    store = SessionStore(li_at=session[0], jsessionid=session[1]) if session else SessionStore()
    fetcher = ContactFetcher(
        pacer=NoPacer(),
        proxy_pool=NoProxies() if proxy_pool is None else proxy_pool,
        session_store=store,
        settings=Settings(request_timeout_s=5),
        transport=FakeTransport(script),
    )
    return fetcher, fetcher._transport


# --------------------------------------------------------------------------- #
# AuthSession / CSRF  (validated against real captured traffic)
# --------------------------------------------------------------------------- #

class TestAuthSession:
    def test_csrf_token_strips_quotes_from_jsessionid(self):
        assert AuthSession("x", '"ajax:98765"').csrf_token == "ajax:98765"

    def test_csrf_token_matches_the_cookie_it_sends(self):
        # Voyager's double-submit check: header must equal the cookie value.
        session = AuthSession("x", "ajax:555")
        headers = session.headers(referer="https://www.linkedin.com/in/x/")
        assert headers["csrf-token"] == "ajax:555"
        assert 'JSESSIONID="ajax:555"' in headers["cookie"]

    def test_li_at_is_sent_as_the_auth_cookie(self):
        headers = AuthSession("secret_token", "ajax:1").headers(referer="r")
        assert "li_at=secret_token" in headers["cookie"]

    def test_sends_the_dash_headers(self):
        headers = AuthSession("x", "ajax:1").headers(referer="https://ref/")
        assert headers["x-restli-protocol-version"] == "2.0.0"
        assert headers["accept"] == "application/vnd.linkedin.normalized+json+2.1"
        assert headers["referer"] == "https://ref/"


# --------------------------------------------------------------------------- #
# SessionStore
# --------------------------------------------------------------------------- #

class TestSessionStore:
    def test_unconfigured_by_default(self):
        store = SessionStore()
        assert store.get() is None
        assert store.configured() is False

    def test_env_values(self):
        session = SessionStore(li_at="tok", jsessionid="ajax:1").get()
        assert session.li_at == "tok" and session.jsessionid == "ajax:1"

    def test_both_values_required(self):
        assert SessionStore(li_at="tok").get() is None
        assert SessionStore(jsessionid="ajax:1").get() is None

    def test_file_overrides_env(self, tmp_path):
        path = tmp_path / "session.txt"
        path.write_text("li_at=fromfile\nJSESSIONID=ajax:file\n", encoding="utf-8")
        store = SessionStore(li_at="fromenv", jsessionid="ajax:env", file_path=str(path))
        assert store.get().li_at == "fromfile"

    def test_file_accepts_a_whole_cookie_header(self, tmp_path):
        path = tmp_path / "session.txt"
        path.write_text(
            'cookie: bcookie="v=2&x"; li_at=AQEDAABBCC; JSESSIONID="ajax:9988"; lidc="b=x"',
            encoding="utf-8",
        )
        session = SessionStore(file_path=str(path)).get()
        assert session.li_at == "AQEDAABBCC"
        assert session.csrf_token == "ajax:9988"

    def test_file_edits_are_picked_up_without_restart(self, tmp_path):
        import time

        path = tmp_path / "session.txt"
        path.write_text("li_at=first\nJSESSIONID=ajax:1\n", encoding="utf-8")
        store = SessionStore(file_path=str(path))
        assert store.get().li_at == "first"
        time.sleep(0.01)
        path.write_text("li_at=second\nJSESSIONID=ajax:2\n", encoding="utf-8")
        assert store.get().li_at == "second"

    def test_missing_file_falls_back_to_env(self):
        store = SessionStore(li_at="tok", jsessionid="ajax:1", file_path="/nope/session.txt")
        assert store.get().li_at == "tok"


# --------------------------------------------------------------------------- #
# Finding the Profile entity in the normalized response
# --------------------------------------------------------------------------- #

class TestFindProfile:
    def test_matches_the_urn_from_elements_not_the_noise(self, dash_body):
        entity = find_profile_entity(json.loads(dash_body), "jean-example-42a1b3")
        # The badge entity shares the fsd_profile prefix but is not the profile.
        assert entity["publicIdentifier"] == "jean-example-42a1b3"
        assert entity["$type"].endswith(".profile.Profile")

    def test_falls_back_to_public_identifier(self):
        payload = {
            "data": {"*elements": ["urn:li:fsd_profile:MISMATCH"]},
            "included": [{"publicIdentifier": "target", "emailAddress": None}],
        }
        assert find_profile_entity(payload, "target")["publicIdentifier"] == "target"

    def test_returns_none_when_no_profile_present(self):
        assert find_profile_entity(json.loads(NO_PROFILE_DASH), "ghost") is None


# --------------------------------------------------------------------------- #
# Parsing the dash Profile entity
# --------------------------------------------------------------------------- #

class TestParse:
    @pytest.fixture
    def profile(self, dash_body):
        return find_profile_entity(json.loads(dash_body), "jean-example-42a1b3")

    def test_email_unwrapped_from_handle_object(self, profile):
        assert parse_contact(profile, "jean-example-42a1b3")["email"] == "jean@example.com"

    def test_phone_numbers(self, profile):
        phones = parse_contact(profile, "x")["phone_numbers"]
        assert (phones[0].type, phones[0].number) == ("MOBILE", "+33 6 12 34 56 78")

    def test_websites_with_category(self, profile):
        websites = parse_contact(profile, "x")["websites"]
        assert (websites[0].url, websites[0].category) == ("https://jean.example.dev/", "PORTFOLIO")
        assert websites[1].category == "COMPANY"

    def test_twitter_and_address_and_birthday(self, profile):
        data = parse_contact(profile, "x")
        assert data["twitter"] == ["jean_builds"]
        assert data["address"].startswith("12 Rue de Rivoli")
        assert data["birthday"] == "May 12"

    def test_public_id_and_url(self, profile):
        data = parse_contact(profile, "jean-example-42a1b3")
        assert data["public_id"] == "jean-example-42a1b3"
        assert data["url"] == "https://www.linkedin.com/in/jean-example-42a1b3"
        assert data["_empty"] is False

    def test_empty_contact_is_flagged_not_failed(self):
        entity = find_profile_entity(json.loads(EMPTY_DASH), "somebody")
        data = parse_contact(entity, "somebody")
        assert data["_empty"] is True
        assert data["email"] is None
        assert data["phone_numbers"] == []

    def test_tolerates_missing_and_malformed_fields(self):
        for entity in ({}, {"phoneNumbers": [{}, "junk"], "websites": [{}]}):
            data = parse_contact(entity, "x")
            assert data["public_id"] == "x"
            assert data["_empty"] is True


# --------------------------------------------------------------------------- #
# Fetcher
# --------------------------------------------------------------------------- #

class TestFetch:
    async def test_returns_parsed_contact(self, dash_body):
        fetcher, _ = make_fetcher([(200, "u", dash_body)])
        result = await fetcher.fetch("jean-example-42a1b3")
        assert result.email == "jean@example.com"
        assert result.websites[0].category == "PORTFOLIO"
        assert result.meta.empty is False

    async def test_hits_the_dash_endpoint_with_the_session(self, dash_body):
        fetcher, transport = make_fetcher([(200, "u", dash_body)])
        await fetcher.fetch("jean-example-42a1b3")
        call = transport.calls[0]
        assert "identity/dash/profiles" in call["url"]
        assert "memberIdentity=jean-example-42a1b3" in call["url"]
        assert "li_at=li_at_value" in call["headers"]["cookie"]
        assert call["headers"]["csrf-token"] == "ajax:123"

    async def test_slug_is_url_encoded_in_the_query(self, dash_body):
        fetcher, transport = make_fetcher([(200, "u", dash_body)])
        await fetcher.fetch("josé-garcía")
        assert "memberIdentity=jos%C3%A9-garc%C3%ADa" in transport.calls[0]["url"]

    async def test_unconfigured_session_is_reported_clearly(self, dash_body):
        fetcher, transport = make_fetcher([(200, "u", dash_body)], session=None)
        with pytest.raises(SessionNotConfigured):
            await fetcher.fetch("x")
        assert transport.calls == []  # never touched the network

    async def test_empty_but_valid_result_sets_meta_empty(self):
        fetcher, _ = make_fetcher([(200, "u", EMPTY_DASH)])
        result = await fetcher.fetch("somebody")
        assert result.meta.empty is True
        assert result.email is None

    async def test_unresolved_identifier_is_not_found(self):
        fetcher, _ = make_fetcher([(200, "u", NO_PROFILE_DASH)])
        with pytest.raises(ProfileNotFound):
            await fetcher.fetch("ghost")

    async def test_expired_session_maps_to_session_invalid(self):
        for status in (401, 403):
            fetcher, _ = make_fetcher([(status, "u", "")])
            with pytest.raises(SessionInvalid):
                await fetcher.fetch("x")

    async def test_410_is_a_clear_endpoint_retired_signal(self):
        # The exact failure that killed the legacy endpoint — must be unambiguous.
        fetcher, _ = make_fetcher([(410, "u", '{"data":{"status":410}}')])
        with pytest.raises(EndpointRetired):
            await fetcher.fetch("x")

    async def test_html_login_redirect_is_session_invalid_not_a_crash(self):
        fetcher, _ = make_fetcher([(200, "u", "<html>sign in</html>")])
        with pytest.raises(SessionInvalid):
            await fetcher.fetch("x")

    async def test_http_404_is_not_found(self):
        fetcher, _ = make_fetcher([(404, "u", "")])
        with pytest.raises(ProfileNotFound):
            await fetcher.fetch("ghost")

    async def test_rate_limited_session_is_blocked(self):
        for status in (429, 999):
            fetcher, _ = make_fetcher([(status, "u", "")])
            with pytest.raises(Blocked):
                await fetcher.fetch("x")

    async def test_server_error_is_upstream_error(self):
        fetcher, _ = make_fetcher([(503, "u", "")])
        with pytest.raises(UpstreamError):
            await fetcher.fetch("x")

    async def test_timeout_propagates(self):
        fetcher, _ = make_fetcher([UpstreamTimeout()])
        with pytest.raises(UpstreamTimeout):
            await fetcher.fetch("x")
