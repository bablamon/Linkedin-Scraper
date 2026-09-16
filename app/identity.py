"""Synthetic guest identities.

LinkedIn does not require authentication to render a public profile — it requires
you to look like a first-time human visitor. This module supplies the two halves
of that persona that are safe to construct locally:

1. A TLS/HTTP2 persona, via curl_cffi's `impersonate` target. It covers the
   ClientHello and the matching UA/client hints together, so they can never
   disagree.
2. A seed cookie jar of genuine client-side preferences only (`lang`, `li_gc`).

What this module deliberately does NOT do is invent session cookies. `bcookie`,
`bscookie`, `lidc` and Cloudflare's `__cf_bm` are issued by LinkedIn's edge and
are collected by the transport's warm-up GET. An earlier version fabricated them
to save a round-trip; the edge does not recognise an invented session and
answered it with 999. See _CurlTransport in fetcher.py for the other half.
"""

from __future__ import annotations

import base64
import random
import time
import uuid
from dataclasses import dataclass, field

from .config import settings

# curl_cffi impersonation targets. The target drives the JA3/JA4 and HTTP2
# SETTINGS fingerprint *and* the matching UA + client-hint headers, so we never
# set those by hand — a Chrome 124 UA over a Chrome 120 handshake is worse than
# neither.
#
# Configurable because it is a tuning knob worth experimenting with: set
# IMPERSONATE_TARGETS to e.g. "safari17_0,safari15_5" to swap the whole persona
# without a rebuild. Note the evidence so far says TLS is NOT the binding
# constraint — stock curl with no impersonation fetched a profile this scraper
# could not — so treat a change here as an experiment, not a fix.
_DEFAULT_TARGETS = ("chrome124", "chrome123", "chrome120")


def _targets() -> tuple[str, ...]:
    configured = tuple(
        t.strip() for t in (settings.impersonate_targets or "").split(",") if t.strip()
    )
    return configured or _DEFAULT_TARGETS

_ACCEPT_LANGUAGES = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
)

def mint_cookies(*, consent: bool = True) -> dict[str, str]:
    """Seed jar for a fresh guest — client-side preferences only.

    Deliberately narrow. `bcookie`, `bscookie`, `lidc` and Cloudflare's
    `__cf_bm` are *issued by LinkedIn's edge* and are collected by the
    transport's warm-up GET; fabricating them locally produced a session the
    edge did not recognise, which it answered with 999. Only cookies a browser
    genuinely sets for itself belong here.
    """
    jar = {"lang": '"v=2&lang=en-us"'}
    if consent:
        # Consent already granted, so the cookie banner interstitial is skipped.
        now_ms = int(time.time() * 1000)
        raw = f"1;1;{now_ms};2;{uuid.uuid4().hex[:16]}"
        jar["li_gc"] = base64.b64encode(raw.encode()).decode().rstrip("=")
    return jar


def cookie_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


@dataclass(slots=True)
class GuestIdentity:
    """One disposable visitor: cookies, TLS persona and locale."""

    impersonate: str
    accept_language: str
    referer: str | None
    cookies: dict[str, str] = field(default_factory=dict)
    # Set only by the crawler strategy, which deliberately overrides the UA.
    header_overrides: dict[str, str] = field(default_factory=dict)

    def headers(self) -> dict[str, str]:
        # No `cookie` header: `cookies` is handed to the transport as a real jar
        # so the edge's own Set-Cookie values merge in and win. A hand-built
        # header would shadow them and keep every request cold.
        h = {"accept-language": self.accept_language}
        if self.referer:
            h["referer"] = self.referer
        h.update(self.header_overrides)
        return h


def new_identity(
    *,
    referer: str | None,
    header_overrides: dict[str, str] | None = None,
    consent: bool = True,
    impersonate: str | None = None,
) -> GuestIdentity:
    """A brand-new guest. Rotating per attempt resets LinkedIn's per-visitor
    profile-view counter, which is what triggers the wall for repeat guests.

    `referer` is explicit — each strategy picks one that matches its persona, and
    None genuinely means "send none" (Googlebot does not arrive via a search
    results page).
    """
    # An explicitly configured pool always wins, so a deployment can be retuned
    # from env alone. A rung's pinned persona is only a default.
    configured = tuple(
        t.strip() for t in (settings.impersonate_targets or "").split(",") if t.strip()
    )
    return GuestIdentity(
        impersonate=random.choice(configured) if configured
        else (impersonate or random.choice(_DEFAULT_TARGETS)),
        accept_language=random.choice(_ACCEPT_LANGUAGES),
        referer=referer,
        cookies=mint_cookies(consent=consent),
        header_overrides=dict(header_overrides or {}),
    )
