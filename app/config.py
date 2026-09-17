"""Runtime configuration, all overridable via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- server ---
    # Bind host/port are set by the Dockerfile's own CMD (reads $PORT directly),
    # not through Settings — nothing here needs to know them.
    log_level: str = "info"
    # When set, every scrape request must carry it as `X-API-Key`.
    api_key: str = ""

    # --- rate limiting ---
    # Inbound API budget. The user-facing contract: 10 requests per minute.
    rate_limit_per_min: int = 10
    # Minimum spacing between two outbound LinkedIn hits. One inbound request can
    # escalate through several strategies, so this — not the inbound bucket — is
    # what actually keeps the egress IP under LinkedIn's radar.
    upstream_min_interval_ms: int = 1500
    upstream_jitter_ms: int = 700

    # --- fetching ---
    # Use a real Chromium (Playwright) instead of the impersonated HTTP client.
    #
    # OFF, because it was measured WORSE on the deployed datacenter IP, not
    # better: with the browser, 0/6 ordinary profiles AND the known-good control
    # (williamhgates, which the HTTP transport fetches every time) all returned
    # 999. Headless Chromium is more detectable from a flagged IP than curl_cffi
    # impersonation — WebGL vendor strings, missing codecs and Chromium-vs-Chrome
    # branding are not patchable from an init script.
    #
    # Kept because it may win from a residential egress, where the trade-offs
    # differ. Measure before enabling; do not assume.
    use_browser: bool = False
    # Browser attempts cost seconds each, so the ladder is shorter by default.
    browser_max_attempts: int = 2
    # Overrides the ladder's per-rung personas with a comma-separated pool
    # chosen at random. Empty = the built-in per-rung assignment, which is
    # deliberately persona-diverse and is what the measurements support.
    impersonate_targets: str = ""
    # Fetch the edge root first to collect its cookies before the profile
    # request. ON because that is what the deployed datacenter egress rewards:
    # with it, lookups resolve on the first rung in ~1.0s. A residential vantage
    # measured the opposite (cold jar better, warm jar broke Chrome 6/6), so
    # this is genuinely environment-dependent — measure before flipping it.
    warm_cookie_jar: bool = True
    request_timeout_s: float = 15.0
    total_timeout_s: float = 45.0
    max_attempts: int = 4

    # --- proxies (optional; the scraper works fine with none) ---
    # Comma/newline separated, e.g. "http://user:pass@host:port,socks5://host:port"
    proxies: str = ""
    # Coolify persistent-storage mount. Re-read automatically when the file changes.
    proxy_file: str = "/data/proxies.txt"
    proxy_cooldown_s: float = 120.0

    # --- cache ---
    cache_ttl_s: float = 900.0
    cache_max: int = 512

    # --- authenticated session (only /contact needs it) ---
    # Your own LinkedIn session cookies. li_at is the credential — keep it in a
    # secret env var, never in the repo. Both come from a logged-in browser.
    linkedin_li_at: str = ""
    linkedin_jsessionid: str = ""
    # Alternative to the two vars above: a Coolify persistent-storage file. When
    # present it overrides them and is re-read on change, so you can rotate the
    # cookie (it expires) without a redeploy. Paste the whole `cookie:` header or
    # just the two values — li_at and JSESSIONID are extracted.
    session_file: str = "/data/session.txt"
    # The dash finder that returns contact info inline (verified Sept 2026, after
    # LinkedIn 410'd the legacy /voyager/api/identity/profiles/... REST family).
    # Overridable because LinkedIn retires endpoints without notice — if /contact
    # starts returning ENDPOINT_RETIRED, point this at the current path.
    # `{member}` is replaced with the URL-encoded public identifier.
    # When a session is configured, fetch /profile through the authenticated
    # GraphQL path instead of guest scraping. This is what reaches ordinary
    # profiles from a datacenter IP — guest mode cannot, at any fingerprint.
    prefer_authenticated: bool = True
    # Captured from the live web client. LinkedIn versions these hashes and they
    # rotate with their releases: when /profile starts returning QUERY_ID_STALE,
    # grab the current one from a browser network tab (filter "graphql") and set
    # this env var. No redeploy needed.
    profile_query_id: str = "voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a"
    contact_endpoint: str = (
        "https://www.linkedin.com/voyager/api/identity/dash/profiles"
        "?q=memberIdentity&memberIdentity={member}"
    )


settings = Settings()
