"""Orchestration: URL in, Profile out."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .cache import TTLCache
from .fetcher import Fetcher
from .models import Meta, Profile
from .parser import parse_profile
from .urls import normalize_profile_input


class ProfileService:
    def __init__(self, fetcher: Fetcher, cache: TTLCache):
        self.fetcher = fetcher
        self.cache = cache

    async def scrape(self, raw_url: str | None, *, refresh: bool = False) -> Profile:
        slug = normalize_profile_input(raw_url)
        started = time.perf_counter()

        if not refresh:
            cached = self.cache.get(slug)
            if cached is not None:
                return cached.model_copy(
                    update={
                        "meta": cached.meta.model_copy(
                            update={
                                "cached": True,
                                "duration_ms": int((time.perf_counter() - started) * 1000),
                            }
                        )
                    }
                )

        result = await self.fetcher.fetch_profile(slug)
        data = parse_profile(result.html, slug, result.final_url)

        profile = Profile(
            **{k: v for k, v in data.items() if not k.startswith("_")},
            meta=Meta(
                fetched_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                strategy=result.strategy,
                source_url=result.final_url,
                proxy_used=result.proxy_used,
                cached=False,
                partial=bool(data.get("_partial")),
                fields_found=int(data.get("_fields_found", 0)),
            ),
        )
        self.cache.set(slug, profile)
        return profile
