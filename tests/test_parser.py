from __future__ import annotations

from app.parser import extract_contact_hints, parse_date_range, parse_profile


def test_full_profile_merges_jsonld_and_dom(full_html):
    data = parse_profile(full_html, "amelie-rousseau-42a1b3", "https://fr.linkedin.com/in/x")

    assert data["full_name"] == "Amélie Rousseau"
    assert data["first_name"] == "Amélie"
    assert data["last_name"] == "Rousseau"
    assert "Principal Data Engineer" in data["headline"]
    assert data["about"].startswith("I build streaming data platforms")
    assert data["follower_count"] == 8421
    assert data["connection_count"] == 500
    assert data["public_id"] == "amelie-rousseau-42a1b3"
    assert data["url"] == "https://www.linkedin.com/in/amelie-rousseau-42a1b3"


def test_location_keeps_structure_and_full_display_string(full_html):
    location = parse_profile(full_html, "x", "u")["location"]
    assert location.locality == "Paris"
    assert location.region == "Île-de-France"
    assert location.country == "FR"
    # DOM carries the fuller display string; JSON-LD carries the parts.
    assert location.raw == "Paris, Île-de-France, France"


def test_dom_experience_wins_over_jsonld(full_html):
    experience = parse_profile(full_html, "x", "u")["experience"]
    assert len(experience) == 2
    first = experience[0]
    assert first.title == "Principal Data Engineer"
    assert first.company == "Dataflux"
    assert first.company_url == "https://www.linkedin.com/company/dataflux"
    assert first.location == "Paris, France"
    assert first.description.startswith("Own the real-time ingestion path")
    assert first.dates.start == "Mar 2021"
    assert first.dates.current is True
    assert first.dates.duration == "4 yrs 6 mos"
    assert experience[1].dates.current is False


def test_education_splits_degree_and_field(full_html):
    education = parse_profile(full_html, "x", "u")["education"]
    assert len(education) == 1
    assert education[0].school == "École Polytechnique"
    assert education[0].degree == "Master's degree"
    assert education[0].field_of_study == "Applied Mathematics"
    assert education[0].dates.start == "2012"
    assert education[0].dates.end == "2016"


def test_long_tail_sections(full_html):
    data = parse_profile(full_html, "x", "u")
    assert data["certifications"][0].name.startswith("Confluent Certified")
    assert data["certifications"][0].issuer == "Confluent"
    assert [lang.name for lang in data["languages"]] == ["French", "English"]
    assert data["languages"][0].proficiency == "Native or bilingual proficiency"
    assert data["volunteering"][0].title == "Mentor"
    assert data["projects"][0].title == "flink-rewind"
    assert data["honors"][0].title == "Prix de l'Innovation 2023"
    assert data["people_also_viewed"][0].name == "Jean Moreau"


def test_avatar_is_never_the_lazy_load_placeholder(full_html):
    # LinkedIn puts a base64 pixel in src and the real image in data-delayed-url.
    avatar = parse_profile(full_html, "x", "u")["avatar_url"]
    assert not avatar.startswith("data:")
    assert avatar.startswith("https://media.licdn.com/")


def test_dom_avatar_used_when_jsonld_has_none():
    html = (
        '<html><body><section class="top-card-layout">'
        '<img class="top-card__profile-image" '
        'data-delayed-url="https://media.licdn.com/real.jpg" src="data:image/gif;base64,R0lGOD">'
        '<h1 class="top-card-layout__title">Ada Lovelace</h1>'
        "</section></body></html>"
    )
    assert parse_profile(html, "x", "u")["avatar_url"] == "https://media.licdn.com/real.jpg"


def test_banner_extracted(full_html):
    assert parse_profile(full_html, "x", "u")["banner_url"].endswith("banner.jpg")


def test_current_company_derived_from_current_role(full_html):
    data = parse_profile(full_html, "x", "u")
    assert data["current_company"] == "Dataflux"
    assert data["current_title"] == "Principal Data Engineer"


def test_full_profile_not_marked_partial(full_html):
    data = parse_profile(full_html, "x", "u")
    assert data["_partial"] is False
    assert data["_fields_found"] >= 7


def test_dom_only_profile(dom_only_html):
    data = parse_profile(dom_only_html, "sam", "u")
    assert data["full_name"] == "Sam Okonkwo"
    assert data["headline"] == "Product Designer at Figma"
    assert data["location"].raw == "Lagos, Nigeria"
    assert data["follower_count"] == 1204
    assert data.get("connection_count") is None
    assert data["experience"][0].company == "Figma"
    assert data["current_company"] == "Figma"


def test_jsonld_only_profile(jsonld_only_html):
    data = parse_profile(jsonld_only_html, "rin", "u")
    assert data["full_name"] == "Rin Takahashi"
    assert data["headline"] == "Head of Infrastructure"
    assert data["location"].locality == "Tokyo"
    assert data["experience"][0].company == "Mercari"
    # No DOM sections rendered, so the caller is told the view is thin.
    assert data["_partial"] is True


def test_authwall_yields_nothing_useful(authwall_html):
    data = parse_profile(authwall_html, "x", "u")
    assert data.get("full_name") is None
    assert data["_partial"] is True
    assert not data.get("experience")
    assert data["_fields_found"] == 0


def test_parser_never_raises_on_garbage():
    for junk in ("", "<html>", "not html at all", "<html><body><h1></h1></body></html>"):
        data = parse_profile(junk, "x", "u")
        assert data["public_id"] == "x"
        assert data["_partial"] is True


def test_malformed_jsonld_is_skipped():
    html = '<html><head><script type="application/ld+json">{ broken</script></head>' \
           '<body><h1 class="top-card-layout__title">Fallback Name</h1></body></html>'
    assert parse_profile(html, "x", "u")["full_name"] == "Fallback Name"


def test_og_title_fallback_when_no_h1():
    html = (
        '<html><head><meta property="og:title" '
        'content="Dana Wu - VP Engineering - Acme | LinkedIn"></head><body></body></html>'
    )
    data = parse_profile(html, "x", "u")
    assert data["full_name"] == "Dana Wu"
    assert data["headline"] == "VP Engineering - Acme"


class TestDateRange:
    def test_current_role(self):
        d = parse_date_range("Mar 2021 - Present · 4 yrs 6 mos")
        assert (d.start, d.end, d.duration, d.current) == ("Mar 2021", "Present", "4 yrs 6 mos", True)

    def test_past_role(self):
        d = parse_date_range("Jun 2017 - Feb 2021 · 3 yrs 9 mos")
        assert (d.start, d.end, d.current) == ("Jun 2017", "Feb 2021", False)

    def test_years_only(self):
        d = parse_date_range("2012 - 2016")
        assert (d.start, d.end) == ("2012", "2016")

    def test_en_dash(self):
        assert parse_date_range("2012 – 2016").end == "2016"

    def test_single_value(self):
        d = parse_date_range("Issued Sep 2022")
        assert d.start == "Issued Sep 2022" and d.end is None

    def test_empty(self):
        d = parse_date_range(None)
        assert d.raw is None and d.current is False


class TestPublicContactHints:
    """Bio-text mining: the only contact-adjacent data available without login —
    whatever the member chose to publish as plain text on the public page."""

    def test_empty_for_a_normal_profile(self, full_html):
        hints = parse_profile(full_html, "x", "u")["public_contact_hints"]
        assert hints.emails == [] and hints.phones == []

    def test_finds_email_in_about(self):
        html = (
            '<html><body><section class="summary core-section-container">'
            '<div class="core-section-container__content">'
            "<p>Reach me at jane.doe@example.com for consulting work.</p>"
            "</div></section></body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert hints.emails == ["jane.doe@example.com"]

    def test_email_matching_is_case_insensitive_and_deduped(self):
        html = (
            '<html><body><section class="summary core-section-container">'
            '<div class="core-section-container__content">'
            "<p>Email Jane@Example.com or jane@example.com — same inbox.</p>"
            "</div></section></body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert hints.emails == ["jane@example.com"]

    def test_finds_international_phone_in_headline(self):
        html = (
            '<html><body><section class="top-card-layout">'
            '<h1 class="top-card-layout__title">Jane Doe</h1>'
            '<h2 class="top-card-layout__headline">Freelance · +33 6 12 34 56 78</h2>'
            "</section></body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert hints.phones == ["+33 6 12 34 56 78"]

    def test_finds_keyword_phone_without_plus(self):
        html = (
            '<html><body><section class="summary core-section-container">'
            '<div class="core-section-container__content">'
            "<p>WhatsApp: 0612345678 for quick questions.</p>"
            "</div></section></body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert hints.phones == ["0612345678"]

    def test_scans_experience_and_education_descriptions(self):
        html = (
            '<html><body>'
            '<section class="experience"><ul class="experience__list"><li class="experience-item">'
            '<h3 class="profile-section-card__title">Consultant</h3>'
            '<p class="show-more-less-text__text--less">Contact: consulting@firm.io</p>'
            "</li></ul></section>"
            "</body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert hints.emails == ["consulting@firm.io"]

    def test_does_not_misread_dates_durations_or_counts_as_phones(self, full_html):
        # full_html contains "Mar 2021 - Present", "4 yrs 6 mos", "8,421 followers",
        # "500+ connections" — none of these are phone numbers.
        hints = parse_profile(full_html, "x", "u")["public_contact_hints"]
        assert hints.phones == []

    def test_bare_digit_run_without_keyword_or_plus_is_not_a_phone(self):
        html = (
            '<html><body><section class="summary core-section-container">'
            '<div class="core-section-container__content">'
            "<p>Grew revenue from 1000000 to 5000000 in two years.</p>"
            "</div></section></body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert hints.phones == []

    def test_caps_the_number_of_hints(self):
        emails = " ".join(f"person{i}@example.com" for i in range(20))
        html = (
            '<html><body><section class="summary core-section-container">'
            f'<div class="core-section-container__content"><p>{emails}</p></div>'
            "</section></body></html>"
        )
        hints = parse_profile(html, "x", "u")["public_contact_hints"]
        assert len(hints.emails) == 5

    def test_extract_contact_hints_ignores_non_text_fields(self):
        # Structured fields (urls, dates) must never be scanned for emails/phones.
        merged = {"about": None, "headline": None}
        assert extract_contact_hints(merged) == extract_contact_hints({})
