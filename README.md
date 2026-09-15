# LinkedIn Profile Scraper

Structured JSON for a LinkedIn profile over raw HTTPS. No Playwright, no
Chromium, no headless browser — one Python process, ~40 MB of RAM.

```bash
curl "http://localhost:8000/profile?url=https://www.linkedin.com/in/williamhgates"
```

Two independent endpoints, two different trust models:

| | Needs login? | Gets you |
|---|---|---|
| [`GET /profile`](#get-profileurlprofilerefreshbool) | No | Name, headline, location, about, experience, education, certs, languages, and more — everything LinkedIn's guest page renders |
| [`GET /contact`](#get-contacturlprofile--contact-info-authenticated) | Yes, your own session | Email, phone, websites, address, birthday — never on the guest page at all |

`/profile` is the default, always-available path. `/contact` is opt-in and
documented separately below, with the account-ban risk that comes with it.

## Table of contents

- [How it gets the page](#how-it-gets-the-page) — the fingerprinting/cookie mechanics behind `/profile`
- [What you get — and the ceiling](#what-you-get--and-the-ceiling) — what's reliably available vs. not, at all
- [API](#api) — `/profile`, `/contact`, `/health`, error codes
- [Deploying on Coolify](#deploying-on-coolify)
- [Rate limiting](#rate-limiting)
- [Running locally](#running-locally)
- [Legal note](#legal-note)

---

## How it gets the page

LinkedIn's profile pages are *published* — the same HTML is served
unauthenticated to Googlebot and to first-time human visitors, because LinkedIn
wants them indexed. What gets you walled is not the absence of a login, it is
looking like automation. Three signals decide it, and this service addresses each:

**1. TLS fingerprint — the one that actually matters.**
A stock Python HTTP client is identifiable from its ClientHello alone: cipher
order, extension order, ALPN and HTTP/2 SETTINGS differ from any real browser.
No amount of header spoofing survives that. `curl_cffi`'s `impersonate` target
reproduces Chrome's handshake byte-for-byte, which is why it — not the cookie
jar — is the load-bearing dependency here. The impersonation target also owns the
`User-Agent` and client-hint headers, so the code never sets them by hand; a
Chrome 124 UA over a Chrome 120 handshake is a louder signal than either alone.

**2. Guest cookies.** A real visitor already carries `bcookie`, `lang`,
`JSESSIONID`, `lidc` and a consent cookie (`li_gc`). All five are minted locally
with no bootstrap round-trip — LinkedIn accepts any well-formed value, and
`JSESSIONID`'s value doubles as the CSRF token on `/voyager` calls. `bscookie` is
deliberately **not** minted: it is server-signed, and a forged signature is a
stronger bot signal than its absence on a first visit. A *fresh* identity per
attempt matters, because the wall is driven largely by how many profiles a given
guest has already viewed.

**3. Provenance.** Each attempt arrives with a search-engine referer matched to
its edge. LinkedIn is markedly more permissive with traffic it believes its own
indexing earned.

### The escalation ladder

Each rung is a different edge, a different persona and a brand-new guest. The
first success returns; a definitive 404 stops immediately rather than wasting the
rate-limit budget on other edges.

| # | Strategy | Edge | Persona |
|---|------------|-----------------------|-----------------------------|
| 1 | `fr_guest` | `fr.linkedin.com` | Chrome, `google.fr` referer |
| 2 | `www_guest` | `www.linkedin.com` | Chrome, `google.com` referer |
| 3 | `intl_guest` | rotating country edge | Chrome, `bing.com` referer |
| 4 | `crawler` | `www.linkedin.com` | Googlebot UA, no referer |

Rung 1 leads because the deployment egresses from Paris and `fr.` is the
coherent pairing. Rung 4 is a cheap last resort, never relied upon: real
Googlebot verification is reverse-DNS, which we cannot pass.

**`/profile` uses no credentials at any point.** No `li_at` session cookie, no
account, no credential store — the whole mechanism above is what gets a guest
past the wall. The one exception in this service is the separate `/contact`
endpoint, which is opt-in, authenticated by a session *you* supply, and
documented on its own further down — it shares none of this section's mechanics.

---

## What you get — and the ceiling

This is the part worth reading before you deploy.

Fingerprinting changes **whether LinkedIn serves you the public page**. It does
not change **what is on that page**. Fields a member has made private are not
withheld by the wall — they are absent from the HTML entirely, so no amount of
TLS work reveals them.

**Reliably available** (populated from the guest page):

`full_name` · `first_name` · `last_name` · `headline` · `location` (locality,
region, country) · `about` · `avatar_url` · `banner_url` · `follower_count` ·
`connection_count` · `current_company` · `current_title` · `experience[]` (title,
company, company URL, location, description, date range) · `education[]` ·
`certifications[]` · `languages[]` · `volunteering[]` · `projects[]` ·
`publications[]` · `honors[]` · `courses[]` · `people_also_viewed[]`

**Contact info** (email, phone, websites, Twitter, address, birthday) is not on
the guest page at all — LinkedIn's structured contact-info feature is served
only to a signed-in member, and no fingerprint or cookie trick changes that,
because the fields are simply absent from the HTML sent to an anonymous
request. There are two ways to get at it here, and they are not the same thing:

- [`GET /contact`](#get-contacturlprofile--contact-info-authenticated) — the
  real feature, via a session you supply. Returns whatever that session can
  see. Opt-in, and carries account-ban risk.
- `public_contact_hints` on `/profile` (**no login**) — not LinkedIn's contact
  feature at all. It's a text scan of the bio, headline, and section
  descriptions the guest page *already* renders, looking for an email or phone
  the member typed into their own public text. Real, free, no account risk —
  but low-hit-rate: most people don't paste their email into their headline, so
  empty `{"emails": [], "phones": []}` is the common case, not a failure.

`/profile` never authenticates, with or without hints found.

**Not available by any method here, authenticated or not:**

- Full skills list and endorsement counts
- Connection lists, mutual connections
- Recommendation text
- Post/activity feed
- Contact fields the target shared with nobody, or a profile whose owner opted
  out of public profiles entirely (guest data) — private-by-choice data stays
  private

`meta.partial` flags responses where the page rendered but LinkedIn withheld most
sections, and `meta.fields_found` counts populated sections — treat a `partial`
record as a thin view, not as a person with an empty profile.

---

## API

### `GET /profile?url=<profile>&refresh=<bool>`
### `POST /profile` — `{"url": "...", "refresh": false}`

`url` accepts anything that identifies a profile:

```
https://www.linkedin.com/in/williamhgates
https://fr.linkedin.com/in/amelie-rousseau-42a1b3/
linkedin.com/in/williamhgates?trk=nav
https://www.linkedin.com/in/jos%C3%A9-garc%C3%ADa-123
https://www.linkedin.com/pub/john-doe/1/2a/3b      (legacy)
/in/williamhgates
williamhgates                                       (bare identifier)
```

`refresh=true` bypasses the cache for that call.

<details>
<summary>Example response</summary>

```json
{
  "url": "https://www.linkedin.com/in/amelie-rousseau-42a1b3",
  "public_id": "amelie-rousseau-42a1b3",
  "full_name": "Amélie Rousseau",
  "first_name": "Amélie",
  "last_name": "Rousseau",
  "headline": "Principal Data Engineer at Dataflux · Streaming systems",
  "location": {
    "raw": "Paris, Île-de-France, France",
    "locality": "Paris",
    "region": "Île-de-France",
    "country": "FR"
  },
  "about": "I build streaming data platforms...",
  "avatar_url": "https://media.licdn.com/dms/image/...",
  "follower_count": 8421,
  "connection_count": 500,
  "current_company": "Dataflux",
  "current_title": "Principal Data Engineer",
  "experience": [
    {
      "title": "Principal Data Engineer",
      "company": "Dataflux",
      "company_url": "https://www.linkedin.com/company/dataflux",
      "location": "Paris, France",
      "description": "Own the real-time ingestion path...",
      "dates": {
        "raw": "Mar 2021 - Present · 4 yrs 6 mos",
        "start": "Mar 2021",
        "end": "Present",
        "duration": "4 yrs 6 mos",
        "current": true
      }
    }
  ],
  "education": [ /* ... */ ],
  "public_contact_hints": { "emails": [], "phones": [] },
  "meta": {
    "fetched_at": "2026-09-15T10:22:41.512+00:00",
    "duration_ms": 1284,
    "strategy": "fr_guest",
    "source_url": "https://fr.linkedin.com/in/amelie-rousseau-42a1b3",
    "proxy_used": false,
    "cached": false,
    "partial": false,
    "fields_found": 9
  }
}
```
</details>

### `GET /contact?url=<profile>` — contact info (authenticated)

Contact info is **never** on the guest page — no fingerprint trick reveals it,
because LinkedIn only serves it to a signed-in member, and only as far as that
member's relationship to the target allows. This endpoint fetches it from
LinkedIn's modern **dash** API, using a session **you** supply:

```text
GET /voyager/api/identity/dash/profiles?q=memberIdentity&memberIdentity={id}
```

> **Why dash, not the endpoint everyone blogs about?** The legacy
> `/voyager/api/identity/profiles/{id}/profileContactInfo` (and the whole
> `/identity/profiles/...` REST family) now returns **`410 Gone`** — LinkedIn
> retired it. This was verified live in Sept 2026. The dash finder above is the
> current path, and crucially it needs **no rotating GraphQL `queryId` or
> `decorationId`**, so it doesn't break every few weeks the way those do.

It returns only what your session can already see. An empty result means your
relationship to that person exposes nothing (`meta.empty: true`) — not that the
lookup failed.

```json
{
  "public_id": "amelie-rousseau-42a1b3",
  "url": "https://www.linkedin.com/in/amelie-rousseau-42a1b3",
  "email": "amelie@dataflux.io",
  "phone_numbers": [{ "type": "MOBILE", "number": "+33 6 12 34 56 78" }],
  "websites": [{ "url": "https://amelie.dev", "category": "PORTFOLIO" }],
  "twitter": ["amelie_builds"],
  "address": "12 Rue de Rivoli, 75001 Paris, France",
  "birthday": "May 12",
  "meta": { "fetched_at": "…", "duration_ms": 412, "proxy_used": false, "empty": false }
}
```

Any field the target hasn't shared with your session comes back `null` / `[]` —
the dash Profile entity simply omits it.

**Read this before enabling it.** Authenticated automation is what LinkedIn's
defences are built to catch, and the penalty is **your account** being
restricted or banned — a much bigger loss than a blocked IP. Use a throwaway
account you can afford to lose, keep well under the rate limit, and don't point
it at strangers in bulk. The single shared outbound pacer means `/contact` and
`/profile` calls are throttled together, so one authed call can't slip the IP's
egress budget.

#### Supplying your session

Two cookies from a logged-in LinkedIn session: `li_at` (the credential) and
`JSESSIONID` (the CSRF pair). Get them once:

1. Log in to LinkedIn in your browser.
2. DevTools → **Application → Cookies → https://www.linkedin.com**.
3. Copy the **value** of `li_at` and of `JSESSIONID` (it looks like
   `"ajax:1234567890"` — the quotes are fine, they're stripped for you).

Then either set two secret env vars in Coolify:

```
LINKEDIN_LI_AT=AQEDAT...          # secret env var, never in git
LINKEDIN_JSESSIONID=ajax:1234567890
```

…or drop them in the persistent-storage file (survives redeploys, rotatable
without one). You can paste the **entire** `cookie:` header copied from a
DevTools request — `li_at` and `JSESSIONID` are extracted, the rest ignored:

```
# /data/session.txt
li_at=AQEDAT...
JSESSIONID="ajax:1234567890"
```

`li_at` sessions last up to ~1 year, but LinkedIn invalidates them on password
change, logout, or when it flags automation. When that happens `/contact`
returns `502 SESSION_INVALID` — swap in a fresh cookie, no code change.

**Why `JSESSIONID` matters:** Voyager guards its calls with a double-submit
cookie — the `csrf-token` header must equal the `JSESSIONID` cookie value. Send a
mismatched or absent one and you get a `403` even with a valid `li_at`. The code
keeps them in lockstep for you; you just have to supply the pair from the *same*
session.

**When LinkedIn moves the endpoint.** They retire APIs without notice (that's how
the legacy one became `410 Gone`). If `/contact` starts returning
`502 ENDPOINT_RETIRED`, the dash path has moved — point `CONTACT_ENDPOINT` at the
current one (capture it from your browser's network tab) without touching code.

### `GET /health`

Liveness plus live rate-limit, cache and proxy state. Never rate-limited, never
requires the API key — Coolify's health check uses it.

### Errors

Every failure returns `{"error": {"code", "message", "detail?"}}`.

| Status | Code | Meaning |
|--------|--------------------|-----------------------------------------------|
| 400 | `INVALID_URL` | Empty, malformed, non-LinkedIn, or a company/school/job URL |
| 401 | `UNAUTHORIZED` | `API_KEY` is set and the header is missing or wrong |
| 404 | `PROFILE_NOT_FOUND` | LinkedIn says no such profile |
| 422 | — | `url` parameter absent entirely |
| 429 | `RATE_LIMITED` | Local budget exhausted; carries `Retry-After` |
| 502 | `BLOCKED` | Every rung of the ladder was walled |
| 502 | `UPSTREAM_ERROR` | Could not reach LinkedIn |
| 504 | `UPSTREAM_TIMEOUT` | LinkedIn did not respond in time |
| 503 | `SESSION_NOT_CONFIGURED` | `/contact` called but no session cookie is set |
| 502 | `SESSION_INVALID` | `/contact` session expired or rejected — refresh `li_at` |
| 502 | `ENDPOINT_RETIRED` | LinkedIn 410'd the dash endpoint — update `CONTACT_ENDPOINT` |

`BLOCKED` is the one to alert on: sustained `BLOCKED` means the egress IP is
burnt and needs proxies or a cool-off, not a code change.

---

## Deploying on Coolify

1. **New Resource → Application → Dockerfile**, point it at this repo.
2. **Port**: `8000`.
3. **Health check path**: `/health`.
4. **Environment variables**: none are required. Set `API_KEY` if the service is
   internet-facing. See `.env.example` for the full list.
5. **Persistent storage** (optional, for proxies): mount a volume at `/data`.
   Drop a `proxies.txt` there, one per line. Edits are picked up within a request
   or two — no redeploy, no restart.

```
# /data/proxies.txt
http://user:pass@gw.provider.net:8000
1.2.3.4:8080:alice:secret      # host:port:user:pass also accepted
socks5://10.0.0.5:1080
# blank lines and comments ignored
```

Proxies are entirely optional; with none configured every request goes out
directly, which is the default and supported path. When configured they are
rotated round-robin, and one that returns a wall is cooled off for
`PROXY_COOLDOWN_S` before being tried again.

### Scale it with replicas, not workers

The image runs **one** uvicorn worker deliberately. The rate limiter, the
outbound pacer and the cache are all in-process — a second worker would double
the real egress rate against LinkedIn while each worker still reported "10/min".
If you need more throughput, run more instances behind distinct egress IPs and
keep the per-instance budget where it is.

---

## Rate limiting

Two independent throttles, because they do different jobs:

- **Inbound** — a 10 req/min token bucket, rejecting immediately with `429` +
  `Retry-After` so callers fail fast instead of queueing. Checked *before* any
  outbound call, so a rejected request never touches LinkedIn.
- **Outbound** — a jittered pacer enforcing a minimum gap between hits on
  LinkedIn. This is the one protecting the IP: a single inbound request can
  escalate through up to `MAX_ATTEMPTS` rungs, so the inbound budget alone would
  not bound the real egress rate.

A 15-minute response cache sits in front of both, so repeat lookups of the same
profile cost nothing.

---

## Running locally

```bash
docker build -t linkedin-scraper .
```

```bash
docker run --rm -p 8000:8000 linkedin-scraper
```

With proxies and a key:

```bash
docker run --rm -p 8000:8000 -e API_KEY=secret -v ./proxies.txt:/data/proxies.txt linkedin-scraper
```

### Tests

```bash
pip install -r requirements-dev.txt && pytest -q
```

All tests run with no network access — both fetchers' transports are injectable,
so the full escalation ladder, the authenticated contact path, every
block/404/timeout/session-invalid case and the whole parser run against fixtures.
Interactive docs at `/docs` once running.

---

## Legal note

Scraping public profile data is not settled-simple. Two things worth knowing
before this runs in production:

- It is against LinkedIn's Terms of Service regardless of technique. The
  practical risk is IP and account blocking.
- Profile data is **personal data** under GDPR, and a Paris deployment puts you
  squarely in scope. Bulk collection of EU residents' data generally needs a
  lawful basis and, under Article 14, notice to the people whose data you hold.
  Worth a look from whoever owns compliance for you.
