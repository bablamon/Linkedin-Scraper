"""Authenticated contact-info lookup.

Contact info is never on the guest page — LinkedIn serves it only to a signed-in
member, and only as far as that member's relationship to the target allows. There
is no fingerprint trick that reveals it; you must *be* a signed-in member.

We fetch it from LinkedIn's modern **dash** API:

    GET /voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity={id}

which returns a normalized ``{data, included}`` document whose Profile entity
carries the contact fields inline — email, phones, websites, twitter, address,
birthday. This endpoint was verified live in Sept 2026 after LinkedIn returned
410 Gone on the whole legacy ``/voyager/api/identity/profiles/...`` REST family.
It needs no rotating GraphQL queryId or decorationId, which is what makes it
maintainable. The only non-obvious auth detail is CSRF: Voyager uses a
double-submit cookie, so the ``csrf-token`` header must equal the ``JSESSIONID``
cookie value (minus its quotes). ``li_at`` is the actual credential; treat it
like a password.

The session is supplied by the operator (env or a persistent-storage file), never
minted or guessed. With none configured, /contact reports that plainly.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

from pydantic import BaseModel, Field

from .errors import (
    Blocked,
    EndpointRetired,
    ProfileNotFound,
    SessionInvalid,
    SessionNotConfigured,
    UpstreamError,
    UpstreamTimeout,
)
from .urls import canonical_url

# The authed call still crosses LinkedIn's fingerprinting, so it goes out over a
# Chrome TLS persona like every other request.
_IMPERSONATE = "chrome124"

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

# Pluck the two cookies we need out of whatever the operator pasted — a bare
# value, a `name=value` line, or an entire `cookie:` header copied from DevTools.
_LI_AT_RE = re.compile(r'li_at=("?)([^;"\s]+)\1')
_JSESSION_RE = re.compile(r'JSESSIONID=("?)(ajax:[^;"\s]+)\1')


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class PhoneNumber(BaseModel):
    type: str | None = None
    number: str | None = None


class Website(BaseModel):
    url: str | None = None
    category: str | None = None


class ContactMeta(BaseModel):
    fetched_at: str
    duration_ms: int
    proxy_used: bool = False
    # True when the profile resolved but exposed no contact fields to this
    # viewer — distinct from an outright failure.
    empty: bool = False


class ContactInfo(BaseModel):
    public_id: str
    url: str
    email: str | None = None
    phone_numbers: list[PhoneNumber] = Field(default_factory=list)
    websites: list[Website] = Field(default_factory=list)
    twitter: list[str] = Field(default_factory=list)
    address: str | None = None
    birthday: str | None = None
    meta: ContactMeta


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class AuthSession:
    li_at: str
    jsessionid: str

    @property
    def csrf_token(self) -> str:
        # The header carries the JSESSIONID value without its surrounding quotes.
        return self.jsessionid.strip().strip('"')

    def cookies(self) -> dict[str, str]:
        """The jar, handed to the transport as cookies — never as a header.

        A hand-built `cookie` header shadows curl_cffi's jar, so LinkedIn's
        load-balancer `lidc` Set-Cookie is never echoed back and Voyager
        302-redirects to the same URL forever (observed: 30 redirects, then
        TooManyRedirects). Same failure mode the guest transport had.
        """
        return {"li_at": self.li_at, "JSESSIONID": f'"{self.csrf_token}"'}

    def headers(self, referer: str) -> dict[str, str]:
        return {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "csrf-token": self.csrf_token,
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "referer": referer,
        }


class SessionStore:
    """Holds the current session. A persistent-storage file, when present and
    non-empty, overrides the env values and is re-read when it changes — so the
    cookie can be rotated (it expires) without a redeploy."""

    def __init__(self, li_at: str = "", jsessionid: str = "", file_path: str = ""):
        self._env = _pair(li_at, jsessionid)
        self._file_path = file_path
        self._file: tuple[str, str] | None = None
        self._file_mtime: float | None = None
        self._lock = threading.Lock()
        self._reload_file()

    def _reload_file(self) -> None:
        path = self._file_path
        if not path:
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            self._file, self._file_mtime = None, None
            return
        if mtime == self._file_mtime:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                blob = fh.read()
        except OSError:
            return
        self._file_mtime = mtime
        li_at = _LI_AT_RE.search(blob)
        js = _JSESSION_RE.search(blob)
        self._file = _pair(
            li_at.group(2) if li_at else "",
            js.group(2) if js else "",
        )

    def get(self) -> AuthSession | None:
        with self._lock:
            self._reload_file()
            pair = self._file or self._env
        if not pair:
            return None
        return AuthSession(li_at=pair[0], jsessionid=pair[1])

    def configured(self) -> bool:
        return self.get() is not None


def _pair(li_at: str, jsessionid: str) -> tuple[str, str] | None:
    li_at = (li_at or "").strip().strip('"')
    jsessionid = (jsessionid or "").strip()
    if li_at and jsessionid:
        return li_at, jsessionid
    return None


# --------------------------------------------------------------------------- #
# Parsing the dash Profile entity
# --------------------------------------------------------------------------- #

def find_profile_entity(payload: dict, slug: str) -> dict | None:
    """Locate the Profile object inside the normalized dash response.

    `data.*elements` holds the profile URN; `included` holds every entity. We
    match the URN first, then fall back to the public identifier or the entity
    type — the response also carries badges, localized-content and image entities
    we must not mistake for the profile.
    """
    data = payload.get("data") or {}
    elements = data.get("*elements") or data.get("elements") or []
    target_urn = elements[0] if elements else None
    included = [e for e in (payload.get("included") or []) if isinstance(e, dict)]

    if target_urn:
        for entity in included:
            if entity.get("entityUrn") == target_urn:
                return entity
    for entity in included:
        if entity.get("publicIdentifier") == slug:
            return entity
    for entity in included:
        if str(entity.get("$type", "")).endswith(".profile.Profile"):
            return entity
    return None


def _email(value) -> str | None:
    if isinstance(value, dict):
        return value.get("emailAddress")
    return value if isinstance(value, str) else None


def _phones(value) -> list[PhoneNumber]:
    out: list[PhoneNumber] = []
    for entry in value or []:
        if not isinstance(entry, dict):
            continue
        # The reference profile had no phone, so the exact dash shape is unseen;
        # accept both the flat and the nested-handle forms defensively.
        number = entry.get("number")
        nested = entry.get("phoneNumber")
        if not number and isinstance(nested, dict):
            number = nested.get("number")
        if number:
            out.append(PhoneNumber(type=entry.get("type"), number=number))
    return out


def _websites(value) -> list[Website]:
    out: list[Website] = []
    for entry in value or []:
        if isinstance(entry, dict) and entry.get("url"):
            out.append(Website(url=entry.get("url"), category=entry.get("category")))
    return out


def _twitter(value) -> list[str]:
    out: list[str] = []
    for entry in value or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("credentialId")
            if name:
                out.append(name)
        elif isinstance(entry, str):
            out.append(entry)
    return out


def _birthday(value) -> str | None:
    if not isinstance(value, dict):
        return None
    month, day = value.get("month"), value.get("day")
    if month and day and 1 <= month <= 12:
        return f"{_MONTHS[month - 1]} {day}"
    return None


def parse_contact(profile: dict, slug: str) -> dict:
    """Extract the contact fields from a dash Profile entity."""
    email = _email(profile.get("emailAddress"))
    phones = _phones(profile.get("phoneNumbers"))
    websites = _websites(profile.get("websites"))
    twitter = _twitter(profile.get("twitterHandles"))
    address = profile.get("address") if isinstance(profile.get("address"), str) else None
    birthday = _birthday(profile.get("birthDateOn"))

    return {
        "public_id": slug,
        "url": canonical_url(slug),
        "email": email or None,
        "phone_numbers": phones,
        "websites": websites,
        "twitter": twitter,
        "address": address or None,
        "birthday": birthday,
        "_empty": not any((email, phones, websites, twitter, address, birthday)),
    }


# --------------------------------------------------------------------------- #
# Fetcher
# --------------------------------------------------------------------------- #

class ContactFetcher:
    """One authenticated GET against the dash API, classified. `transport` is
    injectable for tests."""

    def __init__(self, *, pacer, proxy_pool, session_store, settings, transport=None):
        self.pacer = pacer
        self.proxy_pool = proxy_pool
        self.session_store = session_store
        self.settings = settings
        if transport is None:
            from .fetcher import _CurlTransport

            transport = _CurlTransport()
        self._transport = transport

    async def fetch(self, slug: str) -> ContactInfo:
        session = self.session_store.get()
        if session is None:
            raise SessionNotConfigured()

        started = datetime.now(timezone.utc)
        await self.pacer.wait()
        proxy = self.proxy_pool.acquire()
        url = self.settings.contact_endpoint.format(member=quote(slug, safe=""))

        try:
            status, _final_url, body = await self._transport.get(
                url,
                headers=session.headers(referer=canonical_url(slug) + "/"),
                impersonate=_IMPERSONATE,
                proxy=proxy,
                timeout=self.settings.request_timeout_s,
                cookies=session.cookies(),
            )
        except (UpstreamTimeout, UpstreamError):
            self.proxy_pool.penalise(proxy)
            raise

        self._raise_for_status(status, slug, proxy)
        self.proxy_pool.reward(proxy)

        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as exc:
            # A 200 that is not JSON is almost always an HTML login redirect,
            # i.e. the session is no longer valid.
            raise SessionInvalid(
                detail="Expected JSON from the dash API; got a non-JSON body."
            ) from exc

        profile = find_profile_entity(payload, slug)
        if profile is None:
            raise ProfileNotFound(detail=f"linkedin.com/in/{slug}")

        fields = parse_contact(profile, slug)
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return ContactInfo(
            **{k: v for k, v in fields.items() if not k.startswith("_")},
            meta=ContactMeta(
                fetched_at=started.isoformat(),
                duration_ms=duration_ms,
                proxy_used=proxy is not None,
                empty=bool(fields.get("_empty")),
            ),
        )

    def _raise_for_status(self, status: int, slug: str, proxy) -> None:
        if status == 200:
            return
        if status in (401, 403):
            # 403 here means the session is not authenticated for the call; both
            # point the operator at the same fix — refresh the cookie.
            raise SessionInvalid(
                detail=f"LinkedIn returned HTTP {status}. The li_at cookie is "
                "expired or invalid — supply a fresh one."
            )
        if status == 410:
            # The exact failure that killed the legacy REST endpoint. Make it
            # unmistakable so the fix (update CONTACT_ENDPOINT) is obvious.
            raise EndpointRetired()
        if status == 404:
            raise ProfileNotFound(detail=f"linkedin.com/in/{slug}")
        if status in (429, 999):
            self.proxy_pool.penalise(proxy)
            raise Blocked(
                detail=f"LinkedIn rate-limited the authenticated session (HTTP {status})."
            )
        raise UpstreamError(detail=f"The dash API returned HTTP {status}.")
