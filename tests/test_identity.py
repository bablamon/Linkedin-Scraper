from __future__ import annotations

import base64

from app.identity import cookie_header, mint_cookies, new_identity


def test_seeds_only_client_side_preference_cookies():
    # bcookie/bscookie/lidc/__cf_bm are issued by LinkedIn's edge and collected
    # by the transport's warm-up GET. Fabricating them produced a session the
    # edge did not recognise and answered with 999.
    assert set(mint_cookies()) == {"lang", "li_gc"}


def test_never_forges_edge_issued_cookies():
    jar = mint_cookies()
    for name in ("bcookie", "bscookie", "lidc", "JSESSIONID", "__cf_bm"):
        assert name not in jar


def test_li_gc_decodes_to_a_consent_record():
    raw = mint_cookies()["li_gc"]
    padded = raw + "=" * (-len(raw) % 4)
    decoded = base64.b64decode(padded).decode()
    assert decoded.startswith("1;1;")


def test_consent_can_be_omitted():
    assert "li_gc" not in mint_cookies(consent=False)


def test_identities_are_unique_per_call():
    jars = [mint_cookies()["li_gc"] for _ in range(50)]
    assert len(set(jars)) == 50


def test_cookie_header_format():
    header = cookie_header({"a": "1", "b": "2"})
    assert header == "a=1; b=2"


def test_identity_headers_carry_locale_and_referer_but_no_cookie_header():
    identity = new_identity(referer="https://www.google.com/")
    headers = identity.headers()
    assert headers["accept-language"]
    assert headers["referer"] == "https://www.google.com/"
    # The jar goes to the transport as `cookies`, not as a hand-built header —
    # a header would shadow the edge's Set-Cookie and keep every request cold.
    assert "cookie" not in headers
    assert identity.cookies["lang"]


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


def test_impersonation_targets_are_configurable(monkeypatch):
    # A tuning knob for experiments like "does Safari behave differently?" —
    # swappable via IMPERSONATE_TARGETS without a rebuild.
    from app import identity

    monkeypatch.setattr(identity.settings, "impersonate_targets", "safari17_0,safari15_5")
    assert set(identity._targets()) == {"safari17_0", "safari15_5"}
    assert new_identity(referer=None).impersonate.startswith("safari")


def test_impersonation_falls_back_to_chrome_when_unset(monkeypatch):
    from app import identity

    monkeypatch.setattr(identity.settings, "impersonate_targets", "")
    assert all(t.startswith("chrome") for t in identity._targets())
