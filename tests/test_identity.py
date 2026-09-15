from __future__ import annotations

import base64
import re

from app.identity import cookie_header, mint_cookies, new_identity


def test_mints_the_cookies_a_guest_would_carry():
    jar = mint_cookies()
    assert set(jar) == {"bcookie", "lang", "JSESSIONID", "lidc", "li_gc"}


def test_bcookie_shape():
    value = mint_cookies()["bcookie"]
    assert re.fullmatch(
        r'"v=2&[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"', value
    )


def test_jsessionid_is_the_ajax_form_used_as_csrf_token():
    value = mint_cookies()["JSESSIONID"]
    assert re.fullmatch(r'"ajax:\d{19}"', value)


def test_li_gc_decodes_to_a_consent_record():
    raw = mint_cookies()["li_gc"]
    padded = raw + "=" * (-len(raw) % 4)
    decoded = base64.b64decode(padded).decode()
    assert decoded.startswith("1;1;")


def test_consent_can_be_omitted():
    assert "li_gc" not in mint_cookies(consent=False)


def test_bscookie_is_never_forged():
    # It is server-signed; a bad signature is a louder bot signal than absence.
    assert "bscookie" not in mint_cookies()


def test_identities_are_unique_per_call():
    jars = [mint_cookies()["bcookie"] for _ in range(50)]
    assert len(set(jars)) == 50


def test_cookie_header_format():
    header = cookie_header({"a": "1", "b": "2"})
    assert header == "a=1; b=2"


def test_identity_headers_include_cookie_and_locale():
    headers = new_identity(referer="https://www.google.com/").headers()
    assert "cookie" in headers and "bcookie=" in headers["cookie"]
    assert headers["accept-language"]
    assert headers["referer"] == "https://www.google.com/"


def test_identity_never_sets_user_agent_by_hand():
    # The impersonation target owns UA and client hints; overriding them by hand
    # is how you end up with a Chrome UA over a mismatched TLS handshake.
    assert "user-agent" not in new_identity(referer=None).headers()


def test_explicit_none_referer_sends_none():
    assert "referer" not in new_identity(referer=None).headers()


def test_crawler_strategy_may_override_user_agent():
    identity = new_identity(referer=None, header_overrides={"user-agent": "Googlebot"})
    headers = identity.headers()
    assert headers["user-agent"] == "Googlebot"
    assert "referer" not in headers


def test_impersonation_target_is_a_real_chrome_build():
    targets = {new_identity(referer=None).impersonate for _ in range(40)}
    assert targets
    assert all(t.startswith("chrome") for t in targets)
