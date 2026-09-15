"""Response schema.

Every field is optional: a guest-visible LinkedIn profile is a partial view, and
inventing values we did not actually read would be worse than returning null.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Location(BaseModel):
    raw: str | None = None
    locality: str | None = None
    region: str | None = None
    country: str | None = None


class DateRange(BaseModel):
    raw: str | None = None
    start: str | None = None
    end: str | None = None
    duration: str | None = None
    current: bool = False


class Experience(BaseModel):
    title: str | None = None
    company: str | None = None
    company_url: str | None = None
    employment_type: str | None = None
    location: str | None = None
    description: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Education(BaseModel):
    school: str | None = None
    school_url: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    grade: str | None = None
    description: str | None = None
    dates: DateRange = Field(default_factory=DateRange)


class Certification(BaseModel):
    name: str | None = None
    issuer: str | None = None
    issued: str | None = None
    credential_url: str | None = None


class Item(BaseModel):
    """Generic titled entry — volunteering, projects, publications, honours, courses."""

    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    url: str | None = None
    date: str | None = None


class Language(BaseModel):
    name: str | None = None
    proficiency: str | None = None


class RelatedProfile(BaseModel):
    name: str | None = None
    headline: str | None = None
    url: str | None = None


class PublicContactHints(BaseModel):
    """Email addresses / phone numbers the member chose to publish as plain text
    in their own public bio (about, headline, or a section description).

    This is not LinkedIn's structured contact-info feature — that data is never
    sent to an unauthenticated request, full stop, and reading it needs a real
    session via GET /contact. This is the one contact-adjacent thing an anonymous
    request *can* legitimately see: whatever the member wrote into public text
    themselves. Most profiles publish nothing this way, so empty lists here are
    the common case, not a sign anything went wrong.
    """

    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)


class Meta(BaseModel):
    """How this record was obtained — useful when debugging a thin result."""

    fetched_at: str
    duration_ms: int
    strategy: str | None = None
    source_url: str | None = None
    proxy_used: bool = False
    cached: bool = False
    # True when the page rendered but key sections were withheld by LinkedIn.
    partial: bool = False
    fields_found: int = 0


class Profile(BaseModel):
    url: str
    public_id: str
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    headline: str | None = None
    location: Location = Field(default_factory=Location)
    about: str | None = None
    avatar_url: str | None = None
    banner_url: str | None = None
    follower_count: int | None = None
    connection_count: int | None = None
    current_company: str | None = None
    current_title: str | None = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    volunteering: list[Item] = Field(default_factory=list)
    projects: list[Item] = Field(default_factory=list)
    publications: list[Item] = Field(default_factory=list)
    honors: list[Item] = Field(default_factory=list)
    courses: list[Item] = Field(default_factory=list)
    people_also_viewed: list[RelatedProfile] = Field(default_factory=list)
    public_contact_hints: PublicContactHints = Field(default_factory=PublicContactHints)
    meta: Meta
