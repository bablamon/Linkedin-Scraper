"""Authenticated profile fetching.

Why this exists: LinkedIn's 999 is the *guest* authwall. An authenticated request
never reaches it — verified live from the same datacenter IP that gets 999 for
every guest transport we tried (three TLS personas, warm/cold jars, four country
edges, headless Chromium). Authentication changes the regime; it is not an IP ban.

Two calls, because the GraphQL endpoint keys on the internal member id rather
than the vanity slug:

  1. dash REST  ?q=memberIdentity&memberIdentity=<slug>  -> entityUrn (ACoAA… id)
  2. GraphQL    variables=(memberIdentity:<ACoAA… id>)   -> the full profile

Both were captured from the real web client. The `queryId` hash is versioned by
LinkedIn and WILL rotate — it lives in config so it can be replaced from env
without a redeploy. When it goes stale the API returns a clear error naming the
fix rather than a mystery empty result.

Parsing walks the normalized `included` list by entity `$type` instead of
following nested paths. The nesting changes between LinkedIn releases; the type
names are far more stable.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote

from .errors import Blocked, ProfileNotFound, ScraperError, SessionInvalid, UpstreamError
from .models import DateRange, Education, Experience, Location
from .urls import canonical_url

_RESOLVE_URL = (
    "https://www.linkedin.com/voyager/api/identity/dash/profiles"
    "?q=memberIdentity&memberIdentity={slug}"
)
_MEMBER_ID_RE = re.compile(r"urn:li:fsd_profile:([A-Za-z0-9_-]+)")
_IMPERSONATE = "chrome124"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class QueryIdStale(ScraperError):
    status = 502
    code = "QUERY_ID_STALE"
    message = (
        "LinkedIn rejected the GraphQL queryId. It is versioned and rotates with "
        "their web releases — capture the current one from a browser network tab "
        "and set PROFILE_QUERY_ID."
    )


# --------------------------------------------------------------------------- #
# Extraction helpers — defensive, because field spellings vary by release
# --------------------------------------------------------------------------- #

def _first(entity: dict, *keys):
    for k in keys:
        v = entity.get(k)
        if v:
            return v
    return None


def _localized(value):
    """`multiLocaleFirstName: {"en_US": "Jane"}` -> "Jane"."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for k in ("en_US", "en", *value.keys()):
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k]
    return None


def _is_type(entity: dict, suffix: str) -> bool:
    return str(entity.get("$type", "")).endswith(suffix)


def _date(part) -> str | None:
    """`{"month": 3, "year": 2021}` -> "Mar 2021"."""
    if not isinstance(part, dict):
        return None
    year, month = part.get("year"), part.get("month")
    if year and month and 1 <= month <= 12:
        return f"{_MONTHS[month - 1]} {year}"
    return str(year) if year else None


def _date_range(entity: dict) -> DateRange:
    rng = entity.get("dateRange")
    if not isinstance(rng, dict):
        return DateRange()
    start, end = _date(rng.get("start")), _date(rng.get("end"))
    current = start is not None and end is None
    return DateRange(
        raw=" - ".join(p for p in (start, end or ("Present" if current else None)) if p) or None,
        start=start,
        end=end or ("Present" if current else None),
        current=current,
    )


def _location_of(entity: dict) -> str | None:
    loc = _first(entity, "locationName", "geoLocationName", "location")
    if isinstance(loc, dict):
        return _localized(loc.get("defaultLocalizedName")) or _localized(loc)
    return loc if isinstance(loc, str) else None


def parse_authed_profile(payload: dict, slug: str) -> dict:
    """Walk the normalized `included` list and assemble a profile payload.

    Matches the shape `parse_profile()` returns, so the service layer, response
    model and client-facing JSON are all unchanged.
    """
    included = [e for e in (payload.get("included") or []) if isinstance(e, dict)]

    profile = next(
        (e for e in included if e.get("publicIdentifier") == slug),
        next((e for e in included if _is_type(e, ".profile.Profile")), None),
    )

    merged: dict = {"public_id": slug, "url": canonical_url(slug)}

    if profile:
        first = _localized(_first(profile, "firstName", "multiLocaleFirstName"))
        last = _localized(_first(profile, "lastName", "multiLocaleLastName"))
        full = " ".join(p for p in (first, last) if p) or None
        merged.update(
            {
                "full_name": full,
                "first_name": first,
                "last_name": last,
                "headline": _localized(_first(profile, "headline", "multiLocaleHeadline")),
                "about": _localized(_first(profile, "summary", "multiLocaleSummary")),
            }
        )
        where = _location_of(profile)
        if where:
            merged["location"] = Location(raw=where)

    experience = [
        Experience(
            title=_localized(_first(e, "title", "multiLocaleTitle")),
            company=_localized(_first(e, "companyName", "multiLocaleCompanyName")),
            employment_type=_first(e, "employmentTypeUrn") and None,
            location=_location_of(e),
            description=_localized(_first(e, "description", "multiLocaleDescription")),
            dates=_date_range(e),
        )
        for e in included
        if _is_type(e, ".profile.Position")
    ]
    if experience:
        merged["experience"] = [x for x in experience if x.title or x.company]

    education = [
        Education(
            school=_localized(_first(e, "schoolName", "multiLocaleSchoolName")),
            degree=_localized(_first(e, "degreeName", "multiLocaleDegreeName")),
            field_of_study=_localized(_first(e, "fieldOfStudy", "multiLocaleFieldOfStudy")),
            description=_localized(_first(e, "description")),
            dates=_date_range(e),
        )
        for e in included
        if _is_type(e, ".profile.Education")
    ]
    if education:
        merged["education"] = [x for x in education if x.school]

    current = next((x for x in merged.get("experience", []) if x.dates.current), None)
    if current:
        merged.setdefault("current_company", current.company)
        merged.setdefault("current_title", current.title)

    merged.setdefault("location", Location())
    found = sum(
        1
        for k in ("full_name", "headline", "about", "experience", "education")
        if merged.get(k)
    )
    merged["_fields_found"] = found
    merged["_partial"] = found < 3 or not merged.get("full_name")
    return merged


# --------------------------------------------------------------------------- #

class PersistentTransport:
    """One curl_cffi session reused across calls, so `lidc` is retained.

    Voyager bounces a request between load-balancer hosts until the client
    echoes back the `lidc` cookie it sets. A session-per-call transport has to
    redo that dance every single time and sometimes never converges — observed
    live as "Maximum (20) redirects followed" on the second of two calls.

    Persisting is also the more honest fingerprint: a signed-in member keeps one
    session, they do not present a brand-new one for every request. That is the
    opposite of the guest path, where a fresh identity per attempt is the point.
    """

    def __init__(self) -> None:
        self._session = None
        self._lock = asyncio.Lock()

    async def _ensure(self, cookies):
        async with self._lock:
            if self._session is None:
                from curl_cffi.requests import AsyncSession

                self._session = AsyncSession(cookies=dict(cookies or {}))
            return self._session

    async def close(self) -> None:
        async with self._lock:
            session, self._session = self._session, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass

    async def get(self, url, *, headers, impersonate, proxy, timeout, cookies=None):
        session = await self._ensure(cookies)
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            response = await session.get(
                url,
                headers=headers,
                impersonate=impersonate,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
                max_redirects=20,
            )
        except Exception as exc:
            # Drop the session so a poisoned jar cannot wedge every later call.
            await self.close()
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                from .errors import UpstreamTimeout

                raise UpstreamTimeout(detail=str(exc)[:200]) from exc
            raise UpstreamError(detail=str(exc)[:200]) from exc

        return response.status_code, str(response.url), response.text


class AuthedProfileFetcher:
    """Resolve slug -> member id -> full profile. `transport` is injectable."""

    def __init__(self, *, pacer, proxy_pool, session_store, settings, transport=None):
        self.pacer = pacer
        self.proxy_pool = proxy_pool
        self.session_store = session_store
        self.settings = settings
        self._transport = PersistentTransport() if transport is None else transport

    async def fetch(self, slug: str) -> dict:
        session = self.session_store.get()
        if session is None:
            raise SessionInvalid(detail="No LinkedIn session configured.")

        member_id = await self._member_id(session, slug)
        payload = await self._graphql(session, slug, member_id)
        return parse_authed_profile(payload, slug)

    async def _member_id(self, session, slug: str) -> str:
        body = await self._call(session, _RESOLVE_URL.format(slug=quote(slug, safe="")), slug)
        match = _MEMBER_ID_RE.search(body)
        if not match:
            raise ProfileNotFound(detail=f"linkedin.com/in/{slug}")
        return match.group(1)

    async def _graphql(self, session, slug: str, member_id: str) -> dict:
        url = (
            "https://www.linkedin.com/voyager/api/graphql?includeWebMetadata=true"
            f"&variables=(memberIdentity:{member_id})"
            f"&queryId={self.settings.profile_query_id}"
        )
        body = await self._call(session, url, slug)
        try:
            return json.loads(body)
        except (ValueError, TypeError) as exc:
            raise SessionInvalid(
                detail="Expected JSON from the GraphQL endpoint; got something else."
            ) from exc

    async def _call(self, session, url: str, slug: str) -> str:
        await self.pacer.wait()
        proxy = self.proxy_pool.acquire()
        try:
            status, _final, body = await self._transport.get(
                url,
                headers=session.headers(referer=canonical_url(slug) + "/"),
                impersonate=_IMPERSONATE,
                proxy=proxy,
                timeout=self.settings.request_timeout_s,
                cookies=session.cookies(),
            )
        except (UpstreamError,):
            self.proxy_pool.penalise(proxy)
            raise

        if status in (401, 403):
            raise SessionInvalid(
                detail=f"LinkedIn returned HTTP {status} — the li_at cookie is "
                "expired or invalid."
            )
        if status == 404:
            raise ProfileNotFound(detail=f"linkedin.com/in/{slug}")
        # A rotated queryId is rejected as a bad request, not as auth failure.
        if status == 400 and "queryId" in url:
            raise QueryIdStale()
        if status in (429, 999):
            self.proxy_pool.penalise(proxy)
            raise Blocked(detail=f"LinkedIn rate-limited the session (HTTP {status}).")
        if status != 200:
            raise UpstreamError(detail=f"LinkedIn returned HTTP {status}.")

        self.proxy_pool.reward(proxy)
        return body
