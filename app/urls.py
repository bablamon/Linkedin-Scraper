"""Normalise whatever the caller passes into a LinkedIn public identifier.

Accepts full URLs, scheme-less URLs, country subdomains, legacy /pub/ paths and
bare slugs. Rejects anything that is not a member profile with a clear reason.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from .errors import InvalidProfileURL

MAX_SLUG_LEN = 100

# Hosts we will follow. LinkedIn serves the same guest profile from every
# country subdomain (fr., in., uk., ...), which the fetcher exploits later.
_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)?linkedin\.com$", re.IGNORECASE)

# A public identifier after percent-decoding: unicode letters, digits, - and _.
_SLUG_RE = re.compile(r"^[^\W_]+(?:[-_][^\W_]+)*$", re.UNICODE)

# /in/<these> are not people.
_RESERVED = {
    "company",
    "school",
    "showcase",
    "jobs",
    "feed",
    "learning",
    "posts",
    "pulse",
    "groups",
    "events",
    "services",
    "newsletters",
    "help",
    "legal",
    "login",
    "signup",
    "authwall",
    "checkpoint",
    "uas",
}

_NON_PROFILE_MESSAGE = {
    "company": "That is a company page, not a member profile.",
    "school": "That is a school page, not a member profile.",
    "showcase": "That is a showcase page, not a member profile.",
    "jobs": "That is a job posting, not a member profile.",
    "posts": "That is a post, not a member profile.",
    "pulse": "That is an article, not a member profile.",
    "groups": "That is a group, not a member profile.",
    "events": "That is an event, not a member profile.",
}


def normalize_profile_input(raw: str | None) -> str:
    """Return the canonical public identifier, or raise InvalidProfileURL."""
    if raw is None:
        raise InvalidProfileURL("No profile URL supplied.")

    value = raw.strip().strip("\"'")
    if not value:
        raise InvalidProfileURL("Profile URL is empty.")
    if len(value) > 2048:
        raise InvalidProfileURL("Profile URL is unreasonably long.")
    if any(ord(c) < 32 for c in value):
        raise InvalidProfileURL("Profile URL contains control characters.")

    slug = _extract_slug(value)
    slug = unquote(slug).strip().strip("/")

    if not slug:
        raise InvalidProfileURL("Profile URL contains no public identifier.")
    if len(slug) > MAX_SLUG_LEN:
        raise InvalidProfileURL(
            f"Public identifier exceeds {MAX_SLUG_LEN} characters."
        )
    if len(slug) < 3:
        raise InvalidProfileURL("Public identifier is too short to be real.")
    if not _SLUG_RE.match(slug):
        raise InvalidProfileURL(
            "Public identifier contains characters LinkedIn does not use.",
            detail=slug[:120],
        )
    return slug


def _extract_slug(value: str) -> str:
    """Pull the identifier out of a URL, a path fragment, or a bare slug."""
    looks_like_url = "/" in value or "." in value or ":" in value
    if not looks_like_url:
        return value

    candidate = value
    if value.startswith("/"):
        # A bare path such as "/in/slug" — no host to parse.
        return _slug_from_segments([s for s in value.split("/") if s], value)
    if "://" not in candidate:
        candidate = "https://" + candidate.lstrip("/")

    parts = urlsplit(candidate)
    host = parts.netloc.split("@")[-1].split(":")[0].lower()

    # A bare slug containing a dot (rare but legal) parses as a host.
    if not _HOST_RE.match(host):
        if not parts.path and "linkedin" not in host:
            return value
        raise InvalidProfileURL(
            "URL does not point at linkedin.com.", detail=host or value[:120]
        )

    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        raise InvalidProfileURL("URL has no path — expected /in/<identifier>.")
    return _slug_from_segments(segments, parts.path)


def _slug_from_segments(segments: list[str], path: str) -> str:
    if any(s in ("..", ".") for s in segments):
        raise InvalidProfileURL("URL path contains traversal segments.", detail=path)

    head = segments[0].lower()
    if head == "in":
        if len(segments) < 2:
            raise InvalidProfileURL("URL is missing the identifier after /in/.")
        return segments[1]
    if head == "pub":
        # Legacy /pub/<name>/a/b/c — the name segment still resolves.
        if len(segments) < 2:
            raise InvalidProfileURL("Legacy /pub/ URL is missing its identifier.")
        return segments[1]
    if head == "mwlite" and len(segments) >= 3 and segments[1].lower() == "in":
        return segments[2]
    if head in _RESERVED:
        raise InvalidProfileURL(
            _NON_PROFILE_MESSAGE.get(head, "That LinkedIn URL is not a member profile."),
            detail=path,
        )
    raise InvalidProfileURL(
        "Unrecognised LinkedIn URL shape — expected /in/<identifier>.",
        detail=path,
    )


def canonical_url(slug: str, host: str = "www.linkedin.com") -> str:
    return f"https://{host}/in/{slug}"
