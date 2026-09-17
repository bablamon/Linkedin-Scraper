"""Fetching a guest-visible profile page.

The wall LinkedIn puts in front of anonymous traffic is not an authentication
check — the same page is served unauthenticated to search crawlers and to
first-time visitors, because LinkedIn wants it indexed. What gets you walled is
looking like automation: a Python TLS handshake, a stale guest cookie that has
already viewed N profiles, or arriving with no referring search engine.

So each attempt presents a brand-new visitor against a different LinkedIn edge,
escalating only as far as it needs to. No credentials are used or required at
any point.

Rungs differ in TLS persona as well as edge, because persona turned out to be
the strongest lever measured: with a warmed cookie jar, Chrome personas were
denied 6/6 while Safari and Firefox succeeded 6/6 under otherwise identical
conditions — same cookies, same headers, same IP. So the ladder covers the
persona space deliberately rather than leaving it to chance.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit

from .config import settings
from .errors import Blocked, ProfileNotFound, ScraperError, UpstreamError, UpstreamTimeout
from .identity import GuestIdentity, new_identity
from .urls import canonical_url

# Country edges. A Paris egress hitting fr.linkedin.com is the most coherent
# pairing, so it leads; the rest exist to spread load across edges.
_COUNTRY_HOSTS = ("fr.linkedin.com", "be.linkedin.com", "ch.linkedin.com",
                  "nl.linkedin.com", "uk.linkedin.com", "de.linkedin.com")

_GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


class Outcome(str, Enum):
    OK = "ok"
    AUTHWALL = "authwall"
    NOT_FOUND = "not_found"
    TRANSIENT = "transient"


@dataclass(slots=True)
class Strategy:
    name: str
    host: str
    referer: str | None = None
    header_overrides: dict[str, str] = field(default_factory=dict)
    # Pinned rather than random: persona is the strongest single lever we
    # measured, so the ladder spends its rungs covering the persona space
    # deliberately instead of possibly drawing the same one four times.
    impersonate: str | None = None

    def build_identity(self) -> GuestIdentity:
        return new_identity(
            referer=self.referer,
            header_overrides=self.header_overrides,
            impersonate=self.impersonate,
        )


def build_ladder() -> list[Strategy]:
    """Cheapest and most plausible first; each rung is a different edge/persona.

    Every rung carries a search-engine referer, matched to its edge. Arriving
    from search is the cheapest signal that we are organic traffic, and LinkedIn
    is markedly more permissive with visitors it believes its own indexing
    earned — the pages are published for exactly that audience.
    """
    rotating = random.choice(_COUNTRY_HOSTS[1:])
    return [
        # Personas are left to the configured pool rather than pinned per rung.
        # Pinning safari/firefox was measured as clearly better from a
        # residential IP — and clearly worse from this datacenter one, where it
        # pushed every lookup down to the crawler rung (~6.3s instead of ~1.0s,
        # four upstream hits instead of one). The two egresses are in opposite
        # regimes, so the deployment's own evidence wins. Override per
        # environment with IMPERSONATE_TARGETS.
        Strategy("fr_guest", "fr.linkedin.com", "https://www.google.fr/"),
        Strategy("www_guest", "www.linkedin.com", "https://www.google.com/"),
        Strategy("intl_guest", rotating, "https://www.bing.com/"),
        # Last resort: LinkedIn keeps public profiles readable for search
        # crawlers. We cannot pass reverse-DNS verification, so this only helps
        # on edges that check the UA alone — cheap to try, never relied upon.
        Strategy(
            "crawler",
            "www.linkedin.com",
            None,
            {"user-agent": _GOOGLEBOT_UA, "from": "googlebot(at)googlebot.com"},
        ),
    ]


@dataclass(slots=True)
class FetchResult:
    html: str
    final_url: str
    strategy: str
    status: int
    proxy_used: bool


def _looks_like_profile(body: str) -> bool:
    markers = (
        'application/ld+json',
        'top-card-layout__title',
        'top-card__title',
        'class="profile"',
        'og:title',
    )
    return any(m in body for m in markers)


def _has_person_payload(body: str) -> bool:
    return '"@type":"Person"' in body.replace(" ", "") or "top-card-layout__title" in body


def classify(status: int, final_url: str, body: str) -> Outcome:
    """Decide what LinkedIn actually gave us. Order matters: the authwall and the
    not-found page are both served with HTTP 200 in some edges."""
    url = (final_url or "").lower()
    low = body.lower()

    if status == 404:
        return Outcome.NOT_FOUND
    # 999 is LinkedIn's long-standing "request denied" for suspected automation.
    if status in (999, 403, 429):
        return Outcome.AUTHWALL
    if status >= 500:
        return Outcome.TRANSIENT

    if "/authwall" in url or "/uas/login" in url or "/checkpoint/" in url:
        return Outcome.AUTHWALL
    if "linkedin.com/404" in url or "/error/404" in url:
        return Outcome.NOT_FOUND

    if status == 200:
        if _has_person_payload(body):
            return Outcome.OK
        if "profile-unavailable" in low or "page not found" in low or "this page doesn" in low:
            return Outcome.NOT_FOUND
        if "authwall" in low or "join linkedin" in low or "sign in to view" in low:
            return Outcome.AUTHWALL
        if _looks_like_profile(body):
            return Outcome.OK
        return Outcome.AUTHWALL

    return Outcome.TRANSIENT


class Fetcher:
    """Runs the escalation ladder. `transport` is injectable for tests."""

    def __init__(self, *, pacer, proxy_pool, settings, transport=None):
        self.pacer = pacer
        self.proxy_pool = proxy_pool
        self.settings = settings
        # `is None`, not `or`: a pool defining __len__ is falsy when empty.
        self._transport = _CurlTransport() if transport is None else transport

    async def fetch_profile(self, slug: str) -> FetchResult:
        attempts = (
            self.settings.browser_max_attempts
            if getattr(self.settings, "use_browser", False)
            else self.settings.max_attempts
        )
        ladder = build_ladder()[: max(1, attempts)]
        deadline = time.monotonic() + self.settings.total_timeout_s
        last_outcome: Outcome | None = None
        # Kept so an all-timeouts run reports 504 rather than a vague 502.
        last_transient: ScraperError | None = None
        errors: list[str] = []

        for index, strategy in enumerate(ladder):
            if time.monotonic() >= deadline:
                break
            await self.pacer.wait()

            proxy = self.proxy_pool.acquire()
            url = canonical_url(slug, strategy.host)
            identity = strategy.build_identity()
            remaining = max(1.0, deadline - time.monotonic())
            timeout = min(self.settings.request_timeout_s, remaining)

            try:
                status, final_url, body = await self._transport.get(
                    url,
                    headers=identity.headers(),
                    impersonate=identity.impersonate,
                    proxy=proxy,
                    timeout=timeout,
                    cookies=identity.cookies,
                )
            except UpstreamTimeout as exc:
                self.proxy_pool.penalise(proxy)
                errors.append(f"{strategy.name}: timeout")
                last_outcome, last_transient = Outcome.TRANSIENT, exc
                continue
            except UpstreamError as exc:
                self.proxy_pool.penalise(proxy)
                errors.append(f"{strategy.name}: {exc.detail or 'transport error'}")
                last_outcome, last_transient = Outcome.TRANSIENT, exc
                continue

            outcome = classify(status, final_url, body)
            last_outcome = outcome

            if outcome is Outcome.OK:
                self.proxy_pool.reward(proxy)
                return FetchResult(
                    html=body,
                    final_url=final_url or url,
                    strategy=strategy.name,
                    status=status,
                    proxy_used=proxy is not None,
                )

            if outcome is Outcome.NOT_FOUND:
                # Authoritative: no point escalating to another edge.
                raise ProfileNotFound(detail=f"linkedin.com/in/{slug}")

            if outcome is Outcome.AUTHWALL:
                self.proxy_pool.penalise(proxy)
            errors.append(f"{strategy.name}: {outcome.value} (http {status})")

            if index < len(ladder) - 1:
                await asyncio.sleep(random.uniform(0.4, 1.2))

        detail = "; ".join(errors[-4:]) or "no attempt completed"
        if last_outcome is Outcome.TRANSIENT:
            # Re-raise the same kind we actually hit, so a run of timeouts
            # surfaces as 504 and a transport failure as 502.
            kind = type(last_transient) if last_transient else UpstreamError
            raise kind(detail=detail)
        raise Blocked(detail=detail)


class _CurlTransport:
    """curl_cffi in impersonation mode, over a cookie jar warmed against the edge.

    Two things are load-bearing here, and the second one was learned the hard way:

    1. The `impersonate` target reproduces Chrome's ClientHello, cipher order,
       extension order and HTTP/2 SETTINGS. Headers alone do not survive
       fingerprinting.
    2. The request must carry cookies LinkedIn's edge *issued*, not ones we made
       up. A root GET returns bcookie, bscookie, lidc and — critically —
       Cloudflare's `__cf_bm` bot-management token. A request without them is a
       cold session, and the edge answers cold sessions with 999 far more often.
       Measured: warming the jar flipped 8/8 otherwise-identical requests from
       999 to 200. An earlier version of this file skipped the warm-up to save a
       round-trip and called that an optimisation; it was the bug.
    """

    async def get(self, url, *, headers, impersonate, proxy, timeout, cookies=None):
        from curl_cffi.requests import AsyncSession

        proxies = {"http": proxy, "https": proxy} if proxy else None
        origin = "{0.scheme}://{0.netloc}/".format(urlsplit(url))
        try:
            # One session throughout, so any Set-Cookie is replayed rather than
            # thrown away. The warm-up itself is opt-in — see warm_cookie_jar.
            async with AsyncSession(cookies=dict(cookies or {})) as session:
                if settings.warm_cookie_jar:
                    await self._warm(session, origin, headers, impersonate, proxies, timeout)
                response = await session.get(
                    url,
                    headers=headers,
                    impersonate=impersonate,
                    proxies=proxies,
                    timeout=timeout,
                    allow_redirects=True,
                    # Voyager bounces a request between load-balancer hosts to
                    # get `lidc` accepted before serving it; 5 hops was not
                    # always enough and surfaced as TooManyRedirects.
                    max_redirects=20,
                )
        except Exception as exc:  # curl_cffi raises version-specific classes
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                raise UpstreamTimeout(detail=str(exc)[:200]) from exc
            raise UpstreamError(detail=str(exc)[:200]) from exc

        return response.status_code, str(response.url), response.text

    @staticmethod
    async def _warm(session, origin, headers, impersonate, proxies, timeout):
        """Collect the edge's own cookies. Never fatal: a failed warm-up just
        means we proceed cold, exactly as the previous behaviour did."""
        warm_headers = {k: v for k, v in headers.items() if k != "referer"}
        try:
            await session.get(
                origin,
                headers=warm_headers,
                impersonate=impersonate,
                proxies=proxies,
                timeout=min(timeout, 10.0),
                allow_redirects=True,
                max_redirects=3,
            )
        except Exception:
            pass
