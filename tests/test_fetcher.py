from __future__ import annotations

import pytest

from app.config import Settings
from app.errors import Blocked, ProfileNotFound, UpstreamError, UpstreamTimeout
from app.fetcher import Fetcher, Outcome, build_ladder, classify
from tests.conftest import FakeTransport, NoPacer, NoProxies


def make_fetcher(script, *, proxy_pool=None, max_attempts=4):
    settings = Settings(max_attempts=max_attempts, total_timeout_s=5, request_timeout_s=2)
    transport = FakeTransport(script)
    fetcher = Fetcher(
        pacer=NoPacer(),
        proxy_pool=NoProxies() if proxy_pool is None else proxy_pool,
        settings=settings,
        transport=transport,
    )
    return fetcher, transport


class TestClassify:
    def test_profile_page_is_ok(self, full_html):
        assert classify(200, "https://fr.linkedin.com/in/x", full_html) is Outcome.OK

    def test_jsonld_only_page_still_counts_as_ok(self, jsonld_only_html):
        assert classify(200, "https://www.linkedin.com/in/x", jsonld_only_html) is Outcome.OK

    def test_authwall_body_served_with_http_200(self, authwall_html):
        assert classify(200, "https://www.linkedin.com/in/x", authwall_html) is Outcome.AUTHWALL

    def test_redirect_to_authwall_url(self):
        assert classify(200, "https://www.linkedin.com/authwall?trk=x", "") is Outcome.AUTHWALL

    def test_redirect_to_login(self):
        assert classify(200, "https://www.linkedin.com/uas/login", "") is Outcome.AUTHWALL

    def test_checkpoint_challenge(self):
        assert classify(200, "https://www.linkedin.com/checkpoint/challenge", "") is Outcome.AUTHWALL

    @pytest.mark.parametrize("status", [999, 403, 429])
    def test_bot_blocks(self, status):
        assert classify(status, "https://www.linkedin.com/in/x", "") is Outcome.AUTHWALL

    def test_http_404(self, notfound_html):
        assert classify(404, "https://www.linkedin.com/in/x", notfound_html) is Outcome.NOT_FOUND

    def test_not_found_body_served_with_http_200(self, notfound_html):
        assert classify(200, "https://www.linkedin.com/in/x", notfound_html) is Outcome.NOT_FOUND

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_server_errors_are_transient(self, status):
        assert classify(status, "https://www.linkedin.com/in/x", "") is Outcome.TRANSIENT

    def test_empty_body_is_treated_as_a_wall_not_a_profile(self):
        assert classify(200, "https://www.linkedin.com/in/x", "") is Outcome.AUTHWALL


class TestLadder:
    def test_starts_on_the_french_edge(self):
        # The deployment egresses from Paris, so fr. is the coherent first hop.
        assert build_ladder()[0].host == "fr.linkedin.com"

    def test_every_rung_is_a_distinct_strategy(self):
        names = [s.name for s in build_ladder()]
        assert len(names) == len(set(names)) == 4

    def test_only_the_crawler_rung_overrides_user_agent(self):
        overriding = [s for s in build_ladder() if "user-agent" in s.header_overrides]
        assert [s.name for s in overriding] == ["crawler"]


class TestFetch:
    async def test_returns_on_first_success(self, full_html):
        fetcher, transport = make_fetcher([(200, "https://fr.linkedin.com/in/x", full_html)])
        result = await fetcher.fetch_profile("x")
        assert result.strategy == "fr_guest"
        assert result.html == full_html
        assert len(transport.calls) == 1

    async def test_escalates_past_an_authwall(self, authwall_html, full_html):
        fetcher, transport = make_fetcher(
            [
                (200, "https://fr.linkedin.com/in/x", authwall_html),
                (200, "https://www.linkedin.com/in/x", full_html),
            ]
        )
        result = await fetcher.fetch_profile("x")
        assert result.strategy == "www_guest"
        assert len(transport.calls) == 2

    async def test_each_attempt_uses_a_fresh_guest_identity(self, authwall_html, full_html):
        fetcher, transport = make_fetcher(
            [
                (200, "u", authwall_html),
                (200, "u", authwall_html),
                (200, "u", full_html),
            ]
        )
        await fetcher.fetch_profile("x")
        cookies = [c["headers"]["cookie"] for c in transport.calls]
        assert len(set(cookies)) == 3

    async def test_each_attempt_hits_a_different_host(self, authwall_html, full_html):
        fetcher, transport = make_fetcher(
            [(200, "u", authwall_html), (200, "u", authwall_html), (200, "u", full_html)]
        )
        await fetcher.fetch_profile("x")
        hosts = [c["url"] for c in transport.calls]
        assert len(set(hosts)) == 3

    async def test_gives_up_with_blocked_after_the_whole_ladder(self, authwall_html):
        fetcher, transport = make_fetcher([(200, "u", authwall_html)] * 4)
        with pytest.raises(Blocked) as exc:
            await fetcher.fetch_profile("x")
        assert len(transport.calls) == 4
        assert "authwall" in exc.value.detail

    async def test_not_found_stops_the_ladder_immediately(self, notfound_html):
        fetcher, transport = make_fetcher([(404, "u", notfound_html)])
        with pytest.raises(ProfileNotFound):
            await fetcher.fetch_profile("ghost")
        # Authoritative answer — escalating to other edges would be pointless.
        assert len(transport.calls) == 1

    async def test_max_attempts_is_respected(self, authwall_html):
        fetcher, transport = make_fetcher([(200, "u", authwall_html)] * 4, max_attempts=2)
        with pytest.raises(Blocked):
            await fetcher.fetch_profile("x")
        assert len(transport.calls) == 2

    async def test_recovers_from_a_timeout_on_an_early_rung(self, full_html):
        fetcher, transport = make_fetcher(
            [UpstreamTimeout(), (200, "u", full_html)]
        )
        result = await fetcher.fetch_profile("x")
        assert result.strategy == "www_guest"

    async def test_transport_failure_everywhere_reports_upstream_error(self):
        fetcher, _ = make_fetcher([UpstreamError()] * 4)
        with pytest.raises(UpstreamError):
            await fetcher.fetch_profile("x")

    async def test_timeouts_everywhere_report_a_timeout_not_a_generic_error(self):
        fetcher, _ = make_fetcher([UpstreamTimeout()] * 4)
        with pytest.raises(UpstreamTimeout):
            await fetcher.fetch_profile("x")

    async def test_transient_server_errors_report_upstream_error(self):
        fetcher, _ = make_fetcher([(503, "u", "")] * 4)
        with pytest.raises(UpstreamError):
            await fetcher.fetch_profile("x")


class TestProxyInteraction:
    class RecordingPool(NoProxies):
        def __init__(self, proxy="http://p:1"):
            self.proxy = proxy
            self.penalised: list[str] = []
            self.rewarded: list[str] = []

        def acquire(self):
            return self.proxy

        def penalise(self, proxy):
            self.penalised.append(proxy)

        def reward(self, proxy):
            self.rewarded.append(proxy)

    async def test_proxy_is_passed_to_the_transport(self, full_html):
        pool = self.RecordingPool()
        fetcher, transport = make_fetcher([(200, "u", full_html)], proxy_pool=pool)
        result = await fetcher.fetch_profile("x")
        assert transport.calls[0]["proxy"] == "http://p:1"
        assert result.proxy_used is True

    async def test_working_proxy_is_rewarded(self, full_html):
        pool = self.RecordingPool()
        fetcher, _ = make_fetcher([(200, "u", full_html)], proxy_pool=pool)
        await fetcher.fetch_profile("x")
        assert pool.rewarded == ["http://p:1"]

    async def test_walled_proxy_is_penalised(self, authwall_html):
        pool = self.RecordingPool()
        fetcher, _ = make_fetcher([(200, "u", authwall_html)] * 4, proxy_pool=pool)
        with pytest.raises(Blocked):
            await fetcher.fetch_profile("x")
        assert len(pool.penalised) == 4

    async def test_direct_connection_is_reported_as_such(self, full_html):
        fetcher, _ = make_fetcher([(200, "u", full_html)])
        assert (await fetcher.fetch_profile("x")).proxy_used is False
