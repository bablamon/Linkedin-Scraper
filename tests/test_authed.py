from __future__ import annotations

import json

import pytest

from app.authed import QueryIdStale, parse_authed_profile
from app.config import Settings
from app.errors import ProfileNotFound, SessionInvalid

# A normalized GraphQL response in the shape the live client receives: a flat
# `included` list of typed entities. The parser matches on $type rather than
# nested paths, because the nesting moves between LinkedIn releases.
PAYLOAD = json.loads(json.dumps({
    "data": {"data": {"identityDashProfilesByMemberIdentity": {"*elements": ["urn:li:fsd_profile:ACoAAX"]}}},
    "included": [
        {
            "entityUrn": "urn:li:fsd_profile:ACoAAX",
            "publicIdentifier": "jean-example",
            "firstName": "Jean",
            "lastName": "Example",
            "headline": "Staff Engineer at Acme",
            "summary": "I build distributed systems.",
            "geoLocationName": "Paris, Île-de-France, France",
            "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
        },
        {
            "title": "Staff Engineer",
            "companyName": "Acme",
            "locationName": "Paris, France",
            "description": "Owns the ingestion pipeline.",
            "dateRange": {"start": {"month": 3, "year": 2021}},
            "$type": "com.linkedin.voyager.dash.identity.profile.Position",
        },
        {
            "title": "Senior Engineer",
            "companyName": "Qonto",
            "dateRange": {"start": {"month": 6, "year": 2017}, "end": {"month": 2, "year": 2021}},
            "$type": "com.linkedin.voyager.dash.identity.profile.Position",
        },
        {
            "schoolName": "École Polytechnique",
            "degreeName": "Master's degree",
            "fieldOfStudy": "Applied Mathematics",
            "dateRange": {"start": {"year": 2012}, "end": {"year": 2016}},
            "$type": "com.linkedin.voyager.dash.identity.profile.Education",
        },
        # Noise the parser must ignore.
        {"badgeType": "VERIFIED", "$type": "com.linkedin.voyager.dash.identity.profile.MemberBadge"},
    ],
}))


class TestParse:
    def test_identity(self):
        d = parse_authed_profile(PAYLOAD, "jean-example")
        assert d["full_name"] == "Jean Example"
        assert d["first_name"] == "Jean"
        assert d["headline"] == "Staff Engineer at Acme"
        assert d["about"] == "I build distributed systems."
        assert d["location"].raw == "Paris, Île-de-France, France"

    def test_experience_extracted_by_entity_type(self):
        exp = parse_authed_profile(PAYLOAD, "jean-example")["experience"]
        assert [e.company for e in exp] == ["Acme", "Qonto"]
        assert exp[0].title == "Staff Engineer"
        assert exp[0].location == "Paris, France"
        assert exp[0].description == "Owns the ingestion pipeline."

    def test_open_ended_role_is_current(self):
        exp = parse_authed_profile(PAYLOAD, "x")["experience"]
        assert (exp[0].dates.start, exp[0].dates.end, exp[0].dates.current) == (
            "Mar 2021", "Present", True)
        assert (exp[1].dates.start, exp[1].dates.end, exp[1].dates.current) == (
            "Jun 2017", "Feb 2021", False)

    def test_education(self):
        edu = parse_authed_profile(PAYLOAD, "x")["education"]
        assert edu[0].school == "École Polytechnique"
        assert edu[0].degree == "Master's degree"
        assert edu[0].field_of_study == "Applied Mathematics"
        assert (edu[0].dates.start, edu[0].dates.end) == ("2012", "2016")

    def test_current_role_derived(self):
        d = parse_authed_profile(PAYLOAD, "x")
        assert (d["current_company"], d["current_title"]) == ("Acme", "Staff Engineer")

    def test_badge_noise_is_not_mistaken_for_the_profile(self):
        d = parse_authed_profile(PAYLOAD, "jean-example")
        assert d["full_name"] == "Jean Example"

    def test_multilocale_spellings_are_accepted(self):
        # Some releases return multiLocale* maps instead of plain strings.
        payload = {"included": [{
            "publicIdentifier": "x",
            "multiLocaleFirstName": {"en_US": "Ada"},
            "multiLocaleLastName": {"en_US": "Lovelace"},
            "multiLocaleHeadline": {"en_US": "Mathematician"},
            "$type": "com.linkedin.voyager.dash.identity.profile.Profile",
        }]}
        d = parse_authed_profile(payload, "x")
        assert d["full_name"] == "Ada Lovelace"
        assert d["headline"] == "Mathematician"

    def test_output_shape_matches_the_guest_parser(self):
        d = parse_authed_profile(PAYLOAD, "jean-example")
        # The service layer and response model are shared, so these must exist.
        for key in ("public_id", "url", "location", "_partial", "_fields_found"):
            assert key in d

    def test_empty_payload_is_flagged_partial_not_crashed(self):
        for payload in ({}, {"included": []}, {"included": [{"$type": "x.Other"}]}):
            d = parse_authed_profile(payload, "x")
            assert d["public_id"] == "x"
            assert d["_partial"] is True


class TestFetcher:
    """The authenticated path must fail loudly and specifically — a rotated
    queryId is the one failure mode guaranteed to happen eventually."""

    def make(self, script):
        from app.authed import AuthedProfileFetcher
        from app.contact import SessionStore
        from tests.conftest import FakeTransport, NoPacer, NoProxies

        return AuthedProfileFetcher(
            pacer=NoPacer(),
            proxy_pool=NoProxies(),
            session_store=SessionStore(li_at="tok", jsessionid="ajax:1"),
            settings=Settings(request_timeout_s=5),
            transport=FakeTransport(script),
        )

    async def test_resolves_slug_then_fetches_graphql(self):
        resolve = json.dumps({"data": {"*elements": ["urn:li:fsd_profile:ACoAAX"]}})
        fetcher = self.make([(200, "u", resolve), (200, "u", json.dumps(PAYLOAD))])
        data = await fetcher.fetch("jean-example")
        assert data["full_name"] == "Jean Example"
        calls = fetcher._transport.calls
        assert "q=memberIdentity" in calls[0]["url"]
        assert "ACoAAX" in calls[1]["url"] and "queryId=" in calls[1]["url"]

    async def test_unresolvable_slug_is_not_found(self):
        fetcher = self.make([(200, "u", json.dumps({"included": []}))])
        with pytest.raises(ProfileNotFound):
            await fetcher.fetch("ghost")

    async def test_expired_session_is_reported_as_such(self):
        fetcher = self.make([(403, "u", "")])
        with pytest.raises(SessionInvalid):
            await fetcher.fetch("x")

    async def test_rotated_query_id_names_its_own_fix(self):
        resolve = json.dumps({"data": {"*elements": ["urn:li:fsd_profile:ACoAAX"]}})
        fetcher = self.make([(200, "u", resolve), (400, "u", "")])
        with pytest.raises(QueryIdStale) as exc:
            await fetcher.fetch("x")
        assert "PROFILE_QUERY_ID" in exc.value.message
