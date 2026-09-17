"""Turn a guest profile page into structured data.

Two independent sources are merged, cheapest and most stable first:

1. The `application/ld+json` block. It is schema.org, LinkedIn maintains it for
   search engines, and its shape barely moves — so it is the trustworthy spine.
2. The rendered DOM, which carries what JSON-LD omits (descriptions, locations,
   date ranges, the long tail of sections). Class names here *do* churn, so every
   lookup takes a list of candidate selectors and tolerates all of them missing.

Nothing is inferred or back-filled: a field we did not read stays null.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from selectolax.parser import HTMLParser, Node

from .models import (
    Certification,
    DateRange,
    Education,
    Experience,
    Item,
    Language,
    Location,
    PublicContactHints,
    RelatedProfile,
)
from .urls import canonical_url

_WS_RE = re.compile(r"\s+")
# Gate and error pages put their own copy in the same <h1>/<title> slots a
# profile uses. Without this guard a missed classification turns "Join LinkedIn"
# into a person's name, which is worse than returning nothing.
_NON_NAMES = frozenset(
    {
        "join linkedin",
        "sign up",
        "sign in",
        "log in",
        "login",
        "linkedin",
        "page not found",
        "this page doesn't exist",
        "this page doesn’t exist",
        "profile not found",
        "security verification",
        "something went wrong",
    }
)
_FOLLOWERS_RE = re.compile(r"([\d.,\s]+)\s*(?:followers|abonnés|seguidores)", re.I)
_CONNECTIONS_RE = re.compile(r"([\d.,\s]+\+?)\s*(?:connections?|relations)", re.I)
_DATE_SPLIT_RE = re.compile(r"\s*[-–—]\s*")
_CURRENT_WORDS = {"present", "current", "aujourd'hui", "actualidad", "heute"}
# LinkedIn doesn't always put a "·" before the duration — verified live against
# "2000 - Present 26 years" (no separator at all). Matches a trailing duration
# expression so it can be split off whether or not "·" is present.
_DURATION_TAIL_RE = re.compile(
    r"\s*(\d+\s*(?:yrs?|years?)(?:\s*\d+\s*(?:mos?|months?))?|\d+\s*(?:mos?|months?))\s*$",
    re.IGNORECASE,
)


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    text = _WS_RE.sub(" ", value.replace(" ", " ")).strip()
    if not text:
        return None
    # LinkedIn redacts some fields for guests by replacing the characters with
    # asterisks ("*** ******" where a job title would be). Returning the mask is
    # worse than returning nothing — it looks like real data to a caller.
    if text.replace("*", "").replace("•", "").strip() == "":
        return None
    return text


def _node_text(node: Node | None) -> str | None:
    return _clean(node.text()) if node is not None else None


def _first_text(root: Node | HTMLParser, selectors: Iterable[str]) -> str | None:
    for selector in selectors:
        text = _node_text(root.css_first(selector))
        if text:
            return text
    return None


def _first_attr(root: Node | HTMLParser, selectors: Iterable[str], attrs: Iterable[str]) -> str | None:
    for selector in selectors:
        node = root.css_first(selector)
        if node is None:
            continue
        for attr in attrs:
            value = _clean(node.attributes.get(attr))
            if value:
                return value
    return None


def _plausible_name(value: str | None) -> str | None:
    if not value:
        return None
    if value.strip().lower().rstrip(".!") in _NON_NAMES:
        return None
    return value


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def parse_date_range(raw: str | None) -> DateRange:
    """`Jan 2020 - Present · 3 yrs 2 mos` -> structured parts.

    Also handles `Jan 2020 - Present 3 yrs 2 mos` (no "·") — LinkedIn renders
    both forms depending on the section/locale.
    """
    text = _clean(raw)
    if not text:
        return DateRange()
    duration = None
    if "·" in text:
        range_part, _, duration_part = text.partition("·")
        duration = _clean(duration_part)
        range_part = _clean(range_part) or ""
    else:
        range_part = text
        match = _DURATION_TAIL_RE.search(range_part)
        if match:
            duration = _clean(match.group(1))
            range_part = _clean(range_part[: match.start()]) or ""
    pieces = [p for p in _DATE_SPLIT_RE.split(range_part) if p]
    start = pieces[0] if pieces else None
    end = pieces[1] if len(pieces) > 1 else None
    current = bool(end and end.strip().lower() in _CURRENT_WORDS)
    return DateRange(raw=text, start=start, end=end, duration=duration, current=current)


# --------------------------------------------------------------------------- #
# JSON-LD
# --------------------------------------------------------------------------- #

def _walk_ld(data: Any) -> Iterable[dict]:
    if isinstance(data, dict):
        yield data
        for key in ("@graph", "itemListElement"):
            for child in data.get(key, []) or []:
                yield from _walk_ld(child)
    elif isinstance(data, list):
        for child in data:
            yield from _walk_ld(child)


def extract_jsonld_person(tree: HTMLParser) -> dict | None:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except (ValueError, TypeError):
            continue
        for obj in _walk_ld(payload):
            if obj.get("@type") == "Person" and obj.get("name"):
                return obj
    return None


def _ld_str(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list) and value:
        return _ld_str(value[0])
    if isinstance(value, dict):
        return _clean(value.get("name") or value.get("contentUrl") or value.get("url"))
    return None


def _ld_org_entries(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _ld_member_dates(org: dict) -> DateRange:
    members = org.get("member")
    entry = members[0] if isinstance(members, list) and members else members
    if not isinstance(entry, dict):
        return DateRange()
    start = _clean(str(entry.get("startDate"))) if entry.get("startDate") else None
    end = _clean(str(entry.get("endDate"))) if entry.get("endDate") else None
    return DateRange(
        raw=" - ".join(p for p in (start, end) if p) or None,
        start=start,
        end=end,
        current=start is not None and end is None,
    )


def _from_jsonld(person: dict) -> dict:
    out: dict[str, Any] = {}
    out["full_name"] = _plausible_name(_ld_str(person.get("name")))
    out["first_name"] = _ld_str(person.get("givenName"))
    out["last_name"] = _ld_str(person.get("familyName"))
    out["headline"] = _ld_str(person.get("jobTitle")) or _ld_str(person.get("description"))

    image = person.get("image")
    out["avatar_url"] = _ld_str(image.get("contentUrl")) if isinstance(image, dict) else _ld_str(image)

    address = person.get("address")
    if isinstance(address, dict):
        out["location"] = Location(
            raw=_clean(
                ", ".join(
                    p
                    for p in (
                        address.get("addressLocality"),
                        address.get("addressRegion"),
                        address.get("addressCountry"),
                    )
                    if isinstance(p, str) and p.strip()
                )
            ),
            locality=_clean(address.get("addressLocality")),
            region=_clean(address.get("addressRegion")),
            country=_clean(address.get("addressCountry")),
        )

    stats = person.get("interactionStatistic")
    for stat in _ld_org_entries(stats):
        if "Follow" in str(stat.get("interactionType", "")):
            count = stat.get("userInteractionCount")
            if isinstance(count, (int, float)):
                out["follower_count"] = int(count)

    experience = []
    for org in _ld_org_entries(person.get("worksFor")):
        experience.append(
            Experience(
                title=_ld_str(org.get("description")) or None,
                company=_ld_str(org.get("name")),
                company_url=_ld_str(org.get("url")),
                location=_ld_str((org.get("location") or {}).get("name"))
                if isinstance(org.get("location"), dict)
                else _ld_str(org.get("location")),
                dates=_ld_member_dates(org),
            )
        )
    if experience:
        out["experience"] = experience

    education = []
    for org in _ld_org_entries(person.get("alumniOf")):
        education.append(
            Education(
                school=_ld_str(org.get("name")),
                school_url=_ld_str(org.get("url")),
                degree=_ld_str(org.get("description")),
                dates=_ld_member_dates(org),
            )
        )
    if education:
        out["education"] = education

    languages = [
        Language(name=name)
        for name in (_ld_str(x) for x in (person.get("knowsLanguage") or []))
        if name
    ]
    if languages:
        out["languages"] = languages

    awards = [Item(title=a) for a in (person.get("awards") or []) if isinstance(a, str)]
    if awards:
        out["honors"] = awards

    return {k: v for k, v in out.items() if v}


# --------------------------------------------------------------------------- #
# DOM
# --------------------------------------------------------------------------- #

_SECTION_SELECTORS = {
    "experience": ("section.experience li", "ul.experience__list > li", ".experience-item"),
    "education": ("section.education li", "ul.education__list > li", ".education__list-item"),
    "certifications": ("section.certifications li", "ul.certifications__list > li"),
    "languages": ("section.languages li", "ul.languages__list > li"),
    "volunteering": ("section.volunteering li", "ul.volunteering__list > li"),
    "projects": ("section.projects li", "ul.projects__list > li"),
    "publications": ("section.publications li", "ul.publications__list > li"),
    "honors": ("section.awards li", "section.honors li", "ul.awards__list > li"),
    "courses": ("section.courses li", "ul.courses__list > li"),
    "people_also_viewed": ("section.similar-profiles li", ".similar-profiles__list li"),
}

_TITLE_SELECTORS = ("h3", ".profile-section-card__title", ".experience-item__title", ".base-card__title")
_SUBTITLE_SELECTORS = ("h4", ".profile-section-card__subtitle", ".experience-item__subtitle", ".base-card__subtitle")
_DATE_SELECTORS = (".date-range", ".experience-item__duration", "time", ".profile-section-card__meta")
_DESC_SELECTORS = (
    ".show-more-less-text__text--less",
    ".profile-section-card__description",
    ".experience-item__description",
    "p.break-words",
)
_META_SELECTORS = (".experience-item__meta-item", ".profile-section-card__meta-item")


def _cards(tree: HTMLParser, selectors: Iterable[str]) -> list[Node]:
    for selector in selectors:
        nodes = tree.css(selector)
        if nodes:
            return nodes
    return []


def _card_parts(node: Node) -> dict:
    link = node.css_first("a[href]")
    meta_items = [t for t in (_node_text(n) for n in node.css(",".join(_META_SELECTORS))) if t]
    return {
        "title": _first_text(node, _TITLE_SELECTORS),
        "subtitle": _first_text(node, _SUBTITLE_SELECTORS),
        "date": _first_text(node, _DATE_SELECTORS),
        "description": _first_text(node, _DESC_SELECTORS),
        "url": _clean(link.attributes.get("href")) if link is not None else None,
        "meta": meta_items,
    }


def _split_degree(subtitle: str | None) -> tuple[str | None, str | None]:
    if not subtitle:
        return None, None
    if "," in subtitle:
        degree, _, field = subtitle.partition(",")
        return _clean(degree), _clean(field)
    return subtitle, None


def _dom_sections(tree: HTMLParser) -> dict:
    out: dict[str, Any] = {}

    experience = []
    for node in _cards(tree, _SECTION_SELECTORS["experience"]):
        parts = _card_parts(node)
        if not (parts["title"] or parts["subtitle"]):
            continue
        extra = [m for m in parts["meta"] if m != parts["date"]]
        experience.append(
            Experience(
                title=parts["title"],
                company=parts["subtitle"],
                company_url=parts["url"],
                location=extra[-1] if extra else None,
                description=parts["description"],
                dates=parse_date_range(parts["date"] or (extra[0] if extra else None)),
            )
        )
    if experience:
        out["experience"] = experience

    education = []
    for node in _cards(tree, _SECTION_SELECTORS["education"]):
        parts = _card_parts(node)
        if not parts["title"]:
            continue
        degree, field = _split_degree(parts["subtitle"])
        education.append(
            Education(
                school=parts["title"],
                school_url=parts["url"],
                degree=degree,
                field_of_study=field,
                description=parts["description"],
                dates=parse_date_range(parts["date"]),
            )
        )
    if education:
        out["education"] = education

    certifications = []
    for node in _cards(tree, _SECTION_SELECTORS["certifications"]):
        parts = _card_parts(node)
        if not parts["title"]:
            continue
        certifications.append(
            Certification(
                name=parts["title"],
                issuer=parts["subtitle"],
                issued=parts["date"],
                credential_url=parts["url"],
            )
        )
    if certifications:
        out["certifications"] = certifications

    languages = []
    for node in _cards(tree, _SECTION_SELECTORS["languages"]):
        parts = _card_parts(node)
        if parts["title"]:
            languages.append(Language(name=parts["title"], proficiency=parts["subtitle"]))
    if languages:
        out["languages"] = languages

    for key in ("volunteering", "projects", "publications", "honors", "courses"):
        items = []
        for node in _cards(tree, _SECTION_SELECTORS[key]):
            parts = _card_parts(node)
            if not parts["title"]:
                continue
            items.append(
                Item(
                    title=parts["title"],
                    subtitle=parts["subtitle"],
                    description=parts["description"],
                    url=parts["url"],
                    date=parts["date"],
                )
            )
        if items:
            out[key] = items

    related = []
    for node in _cards(tree, _SECTION_SELECTORS["people_also_viewed"]):
        parts = _card_parts(node)
        if parts["title"] or parts["url"]:
            related.append(
                RelatedProfile(name=parts["title"], headline=parts["subtitle"], url=parts["url"])
            )
    if related:
        out["people_also_viewed"] = related

    return out


def _dom_topcard(tree: HTMLParser) -> dict:
    out: dict[str, Any] = {}

    name = _plausible_name(
        _first_text(
            tree,
            (
                "h1.top-card-layout__title",
                ".top-card-layout__entity-info h1",
                "h1.top-card__title",
                "h1",
            ),
        )
    )
    if name:
        out["full_name"] = name

    headline = _first_text(
        tree, ("h2.top-card-layout__headline", ".top-card__headline", ".top-card-layout__headline")
    )
    if headline:
        out["headline"] = headline

    location = _first_text(
        tree,
        (
            ".top-card-layout__first-subline .top-card__subline-item",
            ".top-card__subline-item--muted",
            ".profile-info-subheader .not-first-middot",
        ),
    )
    if location and not _FOLLOWERS_RE.search(location) and not _CONNECTIONS_RE.search(location):
        out["location"] = Location(raw=location)

    about = _first_text(
        tree,
        (
            "section.summary .core-section-container__content",
            ".summary__info",
            'section[data-section="summary"] p',
            ".core-section-container__content .break-words",
        ),
    )
    if about:
        out["about"] = about

    avatar = _first_attr(
        tree,
        ("img.top-card__profile-image", ".profile-photo-edit__preview", 'meta[property="og:image"]'),
        ("data-delayed-url", "src", "content"),
    )
    if avatar:
        out["avatar_url"] = avatar

    banner = _first_attr(
        tree,
        (".profile-cover-image__image", ".cover-img__image", ".top-card-layout__cover-img"),
        ("data-delayed-url", "src"),
    )
    if banner:
        out["banner_url"] = banner

    # Counts are more reliably found by scanning the top card's text than by
    # chasing the span that currently happens to hold them.
    header = tree.css_first("section.top-card-layout") or tree.css_first("body")
    header_text = _node_text(header) or ""
    followers = _FOLLOWERS_RE.search(header_text)
    if followers:
        out["follower_count"] = _to_int(followers.group(1))
    connections = _CONNECTIONS_RE.search(header_text)
    if connections:
        out["connection_count"] = _to_int(connections.group(1))

    return out


def _og_fallback(tree: HTMLParser) -> dict:
    out: dict[str, Any] = {}
    title = _first_attr(tree, ('meta[property="og:title"]',), ("content",))
    if title:
        # "Jane Doe - Staff Engineer - Acme | LinkedIn"
        head = title.split("|")[0].strip()
        bits = [b.strip() for b in head.split(" - ") if b.strip()]
        name = _plausible_name(bits[0]) if bits else None
        if name:
            out["full_name"] = name
            if len(bits) > 1:
                out["headline"] = " - ".join(bits[1:])
    description = _first_attr(tree, ('meta[property="og:description"]',), ("content",))
    if description:
        out["about"] = _clean(description)
    return out


# --------------------------------------------------------------------------- #

_CORE_FIELDS = ("full_name", "headline", "about", "avatar_url", "location")
_RICH_FIELDS = ("experience", "education", "certifications", "languages")
# Below this many populated sections the page rendered, but LinkedIn withheld
# most of it — the caller should know the record is thin rather than assume the
# person simply has an empty profile.
_PARTIAL_BELOW = 5


def _is_populated(value: Any) -> bool:
    """An empty Location() is still an object, so truthiness alone would lie."""
    if value is None:
        return False
    if isinstance(value, Location):
        return any((value.raw, value.locality, value.region, value.country))
    return bool(value)


# --------------------------------------------------------------------------- #
# Self-published contact hints
#
# LinkedIn never sends structured contact info (email/phone/address) to an
# unauthenticated request — that's not a parsing gap, the fields are simply
# absent from the page. The one legitimate exception: free text the member
# wrote themselves is on the public page already, and occasionally that text
# contains an email or phone number they chose to publish. This mines the text
# we already parsed for that — nothing here is bypassing anything.
# --------------------------------------------------------------------------- #

_MAX_HINTS = 5
_EMAIL_HINT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Unambiguous: a leading "+" makes a digit run a phone number, not a date or count.
_PHONE_INTL_RE = re.compile(r"\+\d[\d \-.()]{6,18}\d")
# Everything else needs a keyword right before it, so "500+ connections" or
# "2021 - 2016" never qualifies — a bare digit run alone is too ambiguous.
_PHONE_KEYWORD_RE = re.compile(
    r"(?:phone|mobile|cell|whatsapp|call me|text me|tel)\s*[:\-]?\s*"
    r"(\+?\d[\d \-.()]{5,17}\d)",
    re.IGNORECASE,
)
_TEXT_FIELDS = ("about", "headline")
_TEXT_LIST_FIELDS = (
    "experience", "education", "volunteering", "projects", "publications",
    "honors", "courses",
)


def _bio_texts(merged: dict) -> list[str]:
    texts = [merged[f] for f in _TEXT_FIELDS if merged.get(f)]
    for key in _TEXT_LIST_FIELDS:
        for entry in merged.get(key) or []:
            description = getattr(entry, "description", None)
            if description:
                texts.append(description)
    return texts


def _dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen[:_MAX_HINTS]


def extract_contact_hints(merged: dict) -> PublicContactHints:
    blob = "\n".join(_bio_texts(merged))
    if not blob:
        return PublicContactHints()

    emails = _dedupe([m.lower() for m in _EMAIL_HINT_RE.findall(blob)])

    phones = _dedupe(
        [_clean(m.group(0)) for m in _PHONE_INTL_RE.finditer(blob)]
        + [_clean(m.group(1)) for m in _PHONE_KEYWORD_RE.finditer(blob)]
    )

    return PublicContactHints(emails=emails, phones=phones)


def parse_profile(html: str, slug: str, source_url: str) -> dict:
    """Merge every source into one profile payload (without `meta`)."""
    tree = HTMLParser(html or "")

    merged: dict[str, Any] = {}
    # Lowest precedence first; richer sources overwrite thinner ones.
    for layer in (_og_fallback(tree), _dom_sections(tree), _dom_topcard(tree)):
        merged.update({k: v for k, v in layer.items() if v})

    person = extract_jsonld_person(tree)
    if person:
        for key, value in _from_jsonld(person).items():
            # JSON-LD wins on identity, but never replaces a richer DOM list.
            if key in ("experience", "education", "languages", "honors") and merged.get(key):
                continue
            if key == "location" and isinstance(merged.get("location"), Location):
                existing = merged["location"]
                if existing.raw and not value.locality:
                    continue
                # JSON-LD has the structured parts; the DOM usually has the
                # fuller display string. Keep both.
                if existing.raw and len(existing.raw) > len(value.raw or ""):
                    value = value.model_copy(update={"raw": existing.raw})
            merged[key] = value

    if not merged.get("full_name"):
        merged.pop("first_name", None)
        merged.pop("last_name", None)
    if merged.get("full_name") and not (merged.get("first_name") or merged.get("last_name")):
        parts = merged["full_name"].split()
        if len(parts) >= 2:
            merged["first_name"] = parts[0]
            merged["last_name"] = " ".join(parts[1:])

    experience = merged.get("experience") or []
    if experience:
        current = next((e for e in experience if e.dates.current), experience[0])
        merged.setdefault("current_company", current.company)
        merged.setdefault("current_title", current.title)

    merged.setdefault("location", Location())
    merged["url"] = canonical_url(slug)
    merged["public_id"] = slug
    merged["public_contact_hints"] = extract_contact_hints(merged)

    found = sum(1 for f in _CORE_FIELDS + _RICH_FIELDS if _is_populated(merged.get(f)))
    merged["_fields_found"] = found
    merged["_partial"] = found < _PARTIAL_BELOW or not merged.get("full_name")
    return merged
