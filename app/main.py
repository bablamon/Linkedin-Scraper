"""REST surface."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .cache import TTLCache
from .config import settings
from .contact import ContactFetcher, ContactInfo, SessionStore
from .errors import RateLimited, ScraperError, Unauthorized
from .fetcher import Fetcher
from .models import Profile
from .proxies import ProxyPool
from .ratelimit import Pacer, TokenBucket
from .service import ProfileService
from .urls import normalize_profile_input

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("linkedin-scraper")


class ScrapeRequest(BaseModel):
    url: str = Field(..., description="Profile URL, /in/ path, or bare public identifier.")
    refresh: bool = Field(False, description="Bypass the cache for this call.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bucket = TokenBucket(settings.rate_limit_per_min, 60.0)
    app.state.proxy_pool = ProxyPool(
        env_value=settings.proxies,
        file_path=settings.proxy_file,
        cooldown_s=settings.proxy_cooldown_s,
    )
    app.state.cache = TTLCache(maxsize=settings.cache_max, ttl=settings.cache_ttl_s)
    # One pacer shared by both fetchers: guest and authenticated calls leave the
    # same IP, so the egress rate that protects it must be bounded across both.
    pacer = Pacer(
        settings.upstream_min_interval_ms / 1000.0,
        settings.upstream_jitter_ms / 1000.0,
    )
    app.state.session_store = SessionStore(
        li_at=settings.linkedin_li_at,
        jsessionid=settings.linkedin_jsessionid,
        file_path=settings.session_file,
    )
    app.state.service = ProfileService(
        Fetcher(pacer=pacer, proxy_pool=app.state.proxy_pool, settings=settings),
        app.state.cache,
    )
    app.state.contact_fetcher = ContactFetcher(
        pacer=pacer,
        proxy_pool=app.state.proxy_pool,
        session_store=app.state.session_store,
        settings=settings,
    )
    log.info(
        "ready: %s req/min, %d proxies configured, session %s",
        settings.rate_limit_per_min,
        len(app.state.proxy_pool),
        "configured" if app.state.session_store.configured() else "not configured",
    )
    yield


app = FastAPI(
    title="LinkedIn Profile Scraper",
    version="1.0.0",
    description="Structured JSON for a public LinkedIn profile, no browser required.",
    lifespan=lifespan,
)


@app.exception_handler(ScraperError)
async def _scraper_error_handler(request: Request, exc: ScraperError) -> JSONResponse:
    headers = {}
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after)
    if exc.status >= 500:
        log.warning("%s on %s: %s", exc.code, request.url.path, exc.detail)
    return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content=ScraperError().payload())


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise Unauthorized()


async def enforce_rate_limit(request: Request) -> None:
    allowed, retry_after = await request.app.state.bucket.acquire()
    if not allowed:
        raise RateLimited(
            retry_after,
            f"Limit is {settings.rate_limit_per_min} requests per minute. "
            f"Retry in {retry_after:.0f}s.",
        )


@app.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    return {
        "status": "ok",
        "rate_limit_per_min": settings.rate_limit_per_min,
        "tokens_available": await state.bucket.tokens_left(),
        "cache_entries": len(state.cache),
        "proxies": state.proxy_pool.stats(),
        "contact_session_configured": state.session_store.configured(),
    }


@app.get(
    "/profile",
    response_model=Profile,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
async def get_profile(
    request: Request,
    url: str = Query(..., description="Profile URL, /in/ path, or bare public identifier."),
    refresh: bool = Query(False, description="Bypass the cache for this call."),
) -> Profile:
    return await request.app.state.service.scrape(url, refresh=refresh)


@app.post(
    "/profile",
    response_model=Profile,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
async def post_profile(request: Request, body: ScrapeRequest) -> Profile:
    return await request.app.state.service.scrape(body.url, refresh=body.refresh)


@app.get(
    "/contact",
    response_model=ContactInfo,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
async def get_contact(
    request: Request,
    url: str = Query(..., description="Profile URL, /in/ path, or bare public identifier."),
) -> ContactInfo:
    """Contact info only, via your configured LinkedIn session. Returns just what
    that session is allowed to see — an empty result means the viewer's
    relationship exposes nothing, not that the lookup failed (see meta.empty)."""
    slug = normalize_profile_input(url)
    return await request.app.state.contact_fetcher.fetch(slug)
