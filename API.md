# LinkedIn Profile API

Structured JSON for a public LinkedIn profile. Send a profile URL, get back a parsed
profile — name, headline, location, experience, education and more.

No browser, no login, no setup on your side. One HTTPS call.

---

## Contents

- [Connection details](#connection-details)
- [Quick start](#quick-start)
- [Endpoints](#endpoints)
- [Response format](#response-format)
- [Field reference](#field-reference)
- [Errors](#errors)
- [Rate limits and recommended use](#rate-limits-and-recommended-use)
- [Scaling up: proxies](#scaling-up-proxies)
- [What's included and what isn't](#whats-included-and-what-isnt)
- [Known limitations](#known-limitations)
- [FAQ](#faq)

---

## Connection details

| | |
|---|---|
| **Base URL** | `http://jqnrcxsgqhwqqij0b9duyjvd.187.124.182.51.sslip.io` |
| **Auth** | Header `X-API-Key: linkedinscraper2026` |
| **Format** | JSON, UTF-8 |
| **Rate limit** | 10 requests per minute |
| **Recommended volume** | 2–3 profiles per day |

Every endpoint except `/health` requires the API key. Requests without it return `401`.

---

## Quick start

```bash
curl -H "X-API-Key: linkedinscraper2026" \
  "http://jqnrcxsgqhwqqij0b9duyjvd.187.124.182.51.sslip.io/profile?url=williamhgates"
```

### Accepted input formats

The `url` parameter is flexible. All of these refer to the same person:

```
https://www.linkedin.com/in/williamhgates
https://fr.linkedin.com/in/williamhgates/
linkedin.com/in/williamhgates?trk=nav
/in/williamhgates
williamhgates
```

It also handles:

| Format | Example |
|---|---|
| Country subdomains | `https://in.linkedin.com/in/someone` |
| Unicode / percent-encoded | `https://www.linkedin.com/in/jos%C3%A9-garc%C3%ADa-123` |
| Legacy `/pub/` URLs | `https://www.linkedin.com/pub/john-doe/1/2a/3b` |
| Query strings, trailing slashes | stripped automatically |

Company, school and job URLs are rejected with a clear message — this API only reads
member profiles.

---

## Endpoints

### `GET /profile`

The main endpoint. Returns the full public profile.

| Parameter | Required | Description |
|---|---|---|
| `url` | **Yes** | Profile URL, `/in/` path, or bare public identifier |
| `refresh` | No | `true` bypasses the 15-minute cache. Default `false` |

```bash
curl -H "X-API-Key: linkedinscraper2026" \
  "http://jqnrcxsgqhwqqij0b9duyjvd.187.124.182.51.sslip.io/profile?url=williamhgates&refresh=true"
```

### `POST /profile`

Identical result, JSON body instead of query parameters.

```bash
curl -X POST \
  -H "X-API-Key: linkedinscraper2026" \
  -H "Content-Type: application/json" \
  -d '{"url": "williamhgates", "refresh": false}' \
  "http://jqnrcxsgqhwqqij0b9duyjvd.187.124.182.51.sslip.io/profile"
```

### `GET /health`

Service status. **No API key needed**, and never counts against your rate limit —
safe to poll for monitoring.

```json
{
  "status": "ok",
  "rate_limit_per_min": 10,
  "tokens_available": 10,
  "cache_entries": 6
}
```

`tokens_available` shows how many requests you can make right now before hitting the
rate limit.

### `GET /contact` — not enabled

Would return email, phone and websites, but requires an authenticated LinkedIn
session that is **not configured on this deployment**. It currently returns
`503 SESSION_NOT_CONFIGURED`.

Do not build against this endpoint without checking with the API owner first.

---

## Response format

A real response (trimmed for readability):

```json
{
  "url": "https://www.linkedin.com/in/williamhgates",
  "public_id": "williamhgates",
  "full_name": "Bill Gates",
  "first_name": "Bill",
  "last_name": "Gates",
  "headline": "Co-chair",
  "location": {
    "raw": "Seattle, Washington, United States",
    "locality": "Seattle",
    "region": null,
    "country": "US"
  },
  "about": "Chair of the Gates Foundation. Founder of Breakthrough Energy...",
  "avatar_url": "https://media.licdn.com/dms/image/...",
  "banner_url": "https://media.licdn.com/dms/image/...",
  "follower_count": 40663061,
  "connection_count": 8,
  "current_company": "Gates Foundation",
  "current_title": "Co-chair",

  "experience": [
    {
      "title": "Co-chair",
      "company": "Gates Foundation",
      "company_url": "https://www.linkedin.com/company/gates-foundation",
      "employment_type": null,
      "location": null,
      "description": null,
      "dates": {
        "raw": "2000 - Present 26 years",
        "start": "2000",
        "end": "Present",
        "duration": "26 years",
        "current": true
      }
    }
  ],

  "education": [
    {
      "school": "Harvard University",
      "school_url": "https://www.linkedin.com/school/harvard-university/",
      "degree": null,
      "field_of_study": null,
      "description": null,
      "dates": { "start": "1973", "end": "1975", "current": false }
    }
  ],

  "certifications": [],
  "languages": [],
  "volunteering": [],
  "projects": [],
  "publications": [],
  "honors": [],
  "courses": [],
  "people_also_viewed": [],

  "public_contact_hints": { "emails": [], "phones": [] },

  "meta": {
    "fetched_at": "2026-09-16T11:04:22.117+00:00",
    "duration_ms": 1014,
    "strategy": "fr_guest",
    "source_url": "https://fr.linkedin.com/in/williamhgates",
    "proxy_used": false,
    "cached": false,
    "partial": false,
    "fields_found": 7
  }
}
```

**Every field is nullable.** A public LinkedIn profile is a partial view — if the
person didn't publish something, you get `null` or `[]` rather than a guessed value.
Write your integration to expect missing fields.

---

## Field reference

### Top level

| Field | Type | Notes |
|---|---|---|
| `url` | string | Canonical profile URL |
| `public_id` | string | The LinkedIn public identifier (slug) |
| `full_name` | string \| null | |
| `first_name`, `last_name` | string \| null | Split from `full_name` when not given separately |
| `headline` | string \| null | The line under the name |
| `location` | object | See below |
| `about` | string \| null | The "About" section |
| `avatar_url` | string \| null | Profile photo |
| `banner_url` | string \| null | Cover image |
| `follower_count` | int \| null | |
| `connection_count` | int \| null | LinkedIn caps display at 500, so `500` often means "500+" |
| `current_company` | string \| null | Derived from the current role |
| `current_title` | string \| null | Derived from the current role |

### `location`

| Field | Notes |
|---|---|
| `raw` | Full display string, e.g. `"Paris, Île-de-France, France"` |
| `locality` | City |
| `region` | State / region — often `null` |
| `country` | Two-letter country code |

### `experience[]` and `education[]`

Both carry a `dates` object:

| Field | Example | Notes |
|---|---|---|
| `raw` | `"2000 - Present 26 years"` | Exactly as LinkedIn displayed it |
| `start` | `"2000"` | |
| `end` | `"Present"` | |
| `duration` | `"26 years"` | |
| `current` | `true` | **Use this** to find the current role, not string matching on `end` |

### `public_contact_hints`

```json
"public_contact_hints": { "emails": [], "phones": [] }
```

Email or phone that the person typed into their **own public bio text** (about,
headline, or a job description). This is *not* LinkedIn's contact-info feature.

Most profiles return empty lists — very few people publish contact details in their
bio. Empty here is normal and does not indicate a problem.

### `meta`

| Field | Notes |
|---|---|
| `fetched_at` | UTC timestamp |
| `duration_ms` | How long the fetch took |
| `cached` | `true` if served from the 15-minute cache |
| `partial` | `true` = the page loaded but most sections were withheld. Treat the record as thin, **not** as a person with an empty profile |
| `fields_found` | 0–9, how many sections were populated. A quick quality score |
| `strategy`, `source_url`, `proxy_used` | Diagnostic; safe to ignore |

---

## Errors

Every failure returns the same envelope:

```json
{ "error": { "code": "BLOCKED", "message": "...", "detail": "..." } }
```

**Branch on `code`, never on `message` text** — messages may be reworded.

| Status | Code | Meaning | What to do |
|---|---|---|---|
| 400 | `INVALID_URL` | Empty, malformed, not LinkedIn, or a company/school/job URL | Fix the input. Don't retry |
| 401 | `UNAUTHORIZED` | Missing or wrong `X-API-Key` | Check the header |
| 404 | `PROFILE_NOT_FOUND` | LinkedIn says no such profile | Don't retry |
| 422 | — | The `url` parameter was missing entirely | Fix the request |
| 429 | `RATE_LIMITED` | More than 10 requests in a minute | Wait for the `Retry-After` header value |
| 502 | `BLOCKED` | LinkedIn refused the request | **Wait 30s+ and retry.** See below |
| 502 | `UPSTREAM_ERROR` | Couldn't reach LinkedIn | Retry after a pause |
| 504 | `UPSTREAM_TIMEOUT` | LinkedIn didn't respond in time | Retry after a pause |
| 503 | `SESSION_NOT_CONFIGURED` | `/contact` only — no session configured | Not usable on this deployment |
| 502 | `ENDPOINT_RETIRED` | LinkedIn moved an internal endpoint | Contact the API owner |

### Understanding `BLOCKED` — important

`BLOCKED` means **the request was refused**. It tells you *nothing* about the profile.

LinkedIn returns a byte-identical response whether the identifier is:

- a real, public profile,
- a private profile, or
- a string that was never a profile at all.

So when you see `BLOCKED`:

- ❌ Don't conclude the profile doesn't exist
- ❌ Don't conclude the profile is private
- ❌ Don't permanently blacklist that identifier
- ✅ **Do** wait and retry
- ✅ **Do** double-check the identifier is spelled correctly — a typo returns `BLOCKED`, not `404`

A mistyped slug is one of the most common causes of an unexpected `BLOCKED`.

---

## Rate limits and recommended use

| Limit | Value | Behaviour |
|---|---|---|
| API rate limit | **10 / minute** | Rejected instantly with `429` + `Retry-After`. Never queued |
| Recommended volume | **2–3 / day** | What this server sustains reliably |
| Response cache | **15 minutes** | Repeat lookups are free and instant |
| Typical latency | **1–3 seconds** | Up to ~10s if the first attempt is refused and it retries |

### Best practices

**Space your requests out.** Minutes apart, not seconds. Rapid bursts are what
trigger refusals — a handful of fast calls will degrade the success rate for
everything after them, even though you're under the stated rate limit.

**Retry `BLOCKED` once or twice, with a real gap** — 30 seconds or more. Immediate
retries usually fail the same way.

**Don't retry `400` or `404`.** Those are settled answers. Retrying only burns
budget that your real lookups need.

**Cache on your side too.** LinkedIn profiles change over weeks, not hours.
Re-fetching the same person daily wastes the capacity that keeps fresh lookups
working.

**Use `/health` for monitoring,** not `/profile`. It's free and unlimited.

### Suggested retry logic

```
try request
  ├─ 200                        → done
  ├─ 400 / 404 / 401            → stop, it's a settled answer
  ├─ 429                        → wait Retry-After seconds, retry
  └─ 502 / 504                  → wait 30–60s, retry (max 2 attempts)
```

---

## Scaling up: proxies

Both of this deployment's main constraints — the 2–3/day volume guidance and the
minority of profiles that never succeed — come from the same root cause, and both
are fixed by the same thing.

### Why the limits exist

The service currently runs from a **single server with one fixed IP address, in a
datacenter**. LinkedIn treats requests from datacenter addresses far more
suspiciously than requests from ordinary home/office connections. Two consequences:

1. **Volume is capped per IP.** All traffic shares one address, so its reputation is
   a shared budget. Push it and the success rate drops for everything.
2. **Some profiles are unreachable from that address.** Well-known profiles are
   served readily; lower-profile ones are much more likely to be refused.

This has been verified directly: profiles that consistently fail from the server
fetch successfully, first try, from an ordinary residential connection — same
identifier, same moment, same request. Nothing about the profile is wrong. The
difference is purely the network address the request comes from.

### What proxies fix

Adding **residential proxies** addresses both limits at once:

| Constraint | Without proxies | With residential proxies |
|---|---|---|
| Volume | 2–3 profiles/day | Scales roughly per IP in the pool |
| Lesser-known profiles | Often refused | Reliably reachable |
| Well-known profiles | Work fine | Work fine |

The service already supports this — proxies are configured, rotated automatically,
and a proxy that starts getting refused is rested before being tried again. No code
changes are needed; it's a configuration and subscription decision for the API owner.

### Residential, not datacenter

This distinction matters and is easy to get wrong when buying:

- ✅ **Residential proxies** route through real consumer ISP connections. These are
  what solve the problem.
- ❌ **Datacenter proxies** are usually cheaper, but they're the *same category of
  address* the server already has. They add IP rotation but not the credibility
  that's missing, so they help much less — and may not help at all.

If you're evaluating providers, "residential" or "mobile" is the tier to ask for.

### Realistic expectations

- Volume grows with the size of the proxy pool rather than being unlimited — each
  address still has its own sustainable rate, and spacing requests still matters.
- Residential proxies are billed by bandwidth or per IP and are meaningfully more
  expensive than datacenter ones. A profile fetch is roughly 0.5–1 MB.
- This won't unlock anything LinkedIn doesn't publish publicly. It improves *access
  reliability*, not *scope* — contact details and the other items in the "not
  included" list stay unavailable regardless.

**If your use case needs more than a few profiles a day, or needs to reach ordinary
(non-celebrity) profiles reliably, residential proxies are the prerequisite — talk
to the API owner before planning around higher volume.**

---

## What's included and what isn't

### Included

Name · first/last name · headline · location (city, region, country) · about
section · profile photo · banner image · follower count · connection count ·
current company and title · **full experience history** (title, company, company
URL, location, description, start/end dates, duration) · **education** (school,
degree, field of study, dates) · certifications · languages · volunteering ·
projects · publications · honours · courses · related profiles

### Not included

| Not available | Why |
|---|---|
| Email addresses, phone numbers, contact details | Requires an authenticated LinkedIn session. See `/contact` |
| Skills and endorsements | Not on the public profile |
| Connection lists, mutual connections | Not on the public profile |
| Recommendation text | Not on the public profile |
| Posts and activity feed | Not on the public profile |
| Anything the profile owner restricted | Respects the member's privacy settings |

---

## Known limitations

Worth understanding before you integrate.

**1. Not every profile can be fetched.** A minority of profiles return `BLOCKED`
consistently from this server, even though they are public and fetch fine from other
networks. This is a property of the server's network address, not of the profile or
of your request. There is no input change that fixes it — see
[Scaling up: proxies](#scaling-up-proxies) for what does.

Rule of thumb: prominent profiles with large followings are reliably reachable;
ordinary individual profiles are noticeably less so. If your use case is mostly the
latter, read the proxies section before planning around it.

**2. Results vary in completeness between people.** Two profiles can return very
different amounts of detail, because members choose how much to publish publicly.
A sparse result usually reflects that choice, not a failed request. Check
`meta.fields_found` and `meta.partial` to tell the difference.

**3. Volume is genuinely limited.** The 2–3 per day guidance isn't arbitrary
throttling — it's what this single-server, single-IP deployment sustains without its
success rate degrading. Higher volume needs residential proxies; see
[Scaling up: proxies](#scaling-up-proxies).

**4. Contact data is not available.** If your use case depends on emails or phone
numbers, this API does not currently provide them.

---

## FAQ

**Why did I get `BLOCKED` for a profile I can open in my browser?**
Your browser and this server reach LinkedIn from different network addresses, and
LinkedIn treats them differently. Wait and retry. If it fails consistently for one
specific profile, report it — but first double-check the identifier spelling.

**Is `500` connections a real number?**
LinkedIn stops displaying an exact count above 500, so `500` means "500 or more".

**How do I find someone's current job?**
Use `current_company` and `current_title`, or look for the entry in `experience[]`
where `dates.current` is `true`. Don't string-match on `dates.end`.

**How fresh is the data?**
Live from LinkedIn, unless `meta.cached` is `true` — then it's up to 15 minutes old.
Use `refresh=true` to force a live fetch.

**Can I request more than 2–3 profiles a day?**
The hard limit is 10/minute, so short bursts work. Sustained higher volume is what
degrades reliability. Raising it properly needs residential proxies — see
[Scaling up: proxies](#scaling-up-proxies).

**A lot of the profiles I need are ordinary people, not public figures. Will that work?**
Less reliably on the current setup. Prominent profiles are served readily; ordinary
ones are refused more often from this server's address. Residential proxies are what
makes them reliably reachable — worth raising with the API owner before you build
around it.

**What happens if I send a company URL?**
`400 INVALID_URL`, with a message saying it's a company page rather than a member
profile.

---

*Questions or an issue with a specific profile — contact the API owner.*
