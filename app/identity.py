"""Synthetic guest identities.

LinkedIn does not require authentication to render a public profile — it requires
you to look like a first-time human visitor. Two things decide that:

1. The cookie set a real browser would already be carrying. We mint these locally
   rather than spending a round-trip bootstrapping them from linkedin.com.
2. The TLS/HTTP2 fingerprint, which is handled by curl_cffi's `impersonate`
   target, not by headers. A stock Python client is identifiable from the
   ClientHello alone, long before any header is read — that, not a login, is what
   actually walls off the page.

`bscookie` is deliberately *not* minted: it is server-signed, and an invalid
signature is a stronger bot signal than its absence on a first visit.
"""

from __future__ import annotations

import base64
import random
import time
import uuid
from dataclasses import dataclass, field

# curl_cffi impersonation targets paired with the Accept-Language a browser in
# that locale would send. The target drives the JA3/JA4 and HTTP2 SETTINGS
# fingerprint *and* the matching UA + client-hint headers, so we never set those
# by hand — a Chrome 124 UA over a Chrome 120 handshake is worse than neither.
_TARGETS = ("chrome124", "chrome123", "chrome120")

_ACCEPT_LANGUAGES = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
)

def mint_cookies(*, consent: bool = True) -> dict[str, str]:
    """Build a plausible fresh-visitor cookie jar with no network round-trip."""
    now_ms = int(time.time() * 1000)
    jar = {
        # LinkedIn's browser id. Any well-formed v=2 UUID is accepted.
        "bcookie": f'"v=2&{uuid.uuid4()}"',
        "lang": '"v=2&lang=en-us"',
        # Value doubles as the CSRF token on any /voyager call.
        "JSESSIONID": f'"ajax:{random.randrange(10**18, 10**19)}"',
        # Routing hint. Supplying one avoids an extra load-balancer redirect hop.
        "lidc": (
            f'"b=VGST00:s=V:r=V:a=V:p=V:g=3096:u=1:x=1:'
            f'i={now_ms // 1000}:t={now_ms // 1000 + 86400}:s={random.randrange(10**18, 10**19)}"'
        ),
    }
    if consent:
        # Consent already granted, so the cookie banner interstitial is skipped.
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
        h = {
            "accept-language": self.accept_language,
            "cookie": cookie_header(self.cookies),
        }
        if self.referer:
            h["referer"] = self.referer
        h.update(self.header_overrides)
        return h


def new_identity(
    *,
    referer: str | None,
    header_overrides: dict[str, str] | None = None,
    consent: bool = True,
) -> GuestIdentity:
    """A brand-new guest. Rotating per attempt resets LinkedIn's per-visitor
    profile-view counter, which is what triggers the wall for repeat guests.

    `referer` is explicit — each strategy picks one that matches its persona, and
    None genuinely means "send none" (Googlebot does not arrive via a search
    results page).
    """
    return GuestIdentity(
        impersonate=random.choice(_TARGETS),
        accept_language=random.choice(_ACCEPT_LANGUAGES),
        referer=referer,
        cookies=mint_cookies(consent=consent),
        header_overrides=dict(header_overrides or {}),
    )
