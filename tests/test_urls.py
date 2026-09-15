from __future__ import annotations

import pytest

from app.errors import InvalidProfileURL
from app.urls import canonical_url, normalize_profile_input


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.linkedin.com/in/williamhgates", "williamhgates"),
        ("https://www.linkedin.com/in/williamhgates/", "williamhgates"),
        ("http://linkedin.com/in/williamhgates", "williamhgates"),
        ("linkedin.com/in/williamhgates", "williamhgates"),
        ("www.linkedin.com/in/williamhgates?trk=nav", "williamhgates"),
        ("https://fr.linkedin.com/in/amelie-rousseau-42a1b3", "amelie-rousseau-42a1b3"),
        ("https://uk.linkedin.com/in/someone/en", "someone"),
        ("/in/williamhgates", "williamhgates"),
        ("williamhgates", "williamhgates"),
        ("  https://www.linkedin.com/in/williamhgates  ", "williamhgates"),
        ('"https://www.linkedin.com/in/williamhgates"', "williamhgates"),
        ("https://www.linkedin.com/in/jos%C3%A9-garc%C3%ADa-123", "josé-garcía-123"),
        ("https://www.linkedin.com/in/josé-garcía-123", "josé-garcía-123"),
        ("https://www.linkedin.com/pub/john-doe/1/2a/3b", "john-doe"),
        ("https://www.linkedin.com/mwlite/in/williamhgates", "williamhgates"),
        ("https://LINKEDIN.COM/IN/WilliamHGates", "WilliamHGates"),
        ("https://www.linkedin.com/in/a_b_c", "a_b_c"),
    ],
)
def test_accepts_valid_shapes(raw, expected):
    assert normalize_profile_input(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        '""',
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/in",
        "https://www.linkedin.com/",
        "https://example.com/in/williamhgates",
        "https://twitter.com/williamhgates",
        "https://www.linkedin.com/company/microsoft",
        "https://www.linkedin.com/school/mit",
        "https://www.linkedin.com/jobs/view/12345",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/posts/someone_abc",
        "ab",
        "https://www.linkedin.com/in/" + "x" * 150,
        "x" * 3000,
        "https://www.linkedin.com/in/bad name",
        "https://www.linkedin.com/in/bad/../etc",
        "https://www.linkedin.com/in/hello\nworld",
        "https://www.linkedin.com/in/<script>",
        "https://www.linkedin.com/in/--",
    ],
)
def test_rejects_bad_input(raw):
    with pytest.raises(InvalidProfileURL):
        normalize_profile_input(raw)


def test_rejection_messages_are_specific():
    with pytest.raises(InvalidProfileURL, match="company page"):
        normalize_profile_input("https://www.linkedin.com/company/microsoft")
    with pytest.raises(InvalidProfileURL, match="empty"):
        normalize_profile_input("   ")
    with pytest.raises(InvalidProfileURL, match="linkedin.com"):
        normalize_profile_input("https://example.com/in/x")


def test_canonical_url():
    assert canonical_url("abc") == "https://www.linkedin.com/in/abc"
    assert canonical_url("abc", "fr.linkedin.com") == "https://fr.linkedin.com/in/abc"
