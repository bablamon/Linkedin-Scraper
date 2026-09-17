"""Playwright transport — a real Chromium instead of an impersonated HTTP client.

Drop-in replacement for `_CurlTransport`: same `get()` signature, same
`(status, final_url, html)` return, so the ladder, parser and API are untouched.

Why a real browser at all. curl_cffi reproduces Chrome's TLS handshake, but it
cannot execute JavaScript, so it never satisfies a Cloudflare JS challenge and
has no canvas/WebGL/timing fingerprint behind it. A real browser does. That may
or may not be what LinkedIn's edge is checking from a datacenter IP — it is
measurable, not knowable in advance, hence the USE_BROWSER toggle.

Costs, honestly: ~1GB more image, a few hundred MB of RAM, and seconds per
request instead of ~1s. Worth it only if it actually raises the success rate.
"""

from __future__ import annotations

import asyncio

from .errors import UpstreamError, UpstreamTimeout

# Chromium flags that remove the loudest automation tells. Kept short on
# purpose: a long exotic flag list is itself a fingerprint.
_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",                    # required in most containers
    "--disable-dev-shm-usage",         # /dev/shm is tiny in Docker
    "--disable-gpu",
]

# navigator.webdriver is the single most-checked automation flag.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


class PlaywrightTransport:
    """One Chromium for the process, one fresh context per request.

    Launching per request would cost seconds; sharing a context would leak
    cookies between lookups and defeat the ladder's fresh-visitor design. A
    context per request is the middle ground.
    """

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True, args=_LAUNCH_ARGS
        )

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def _ensure(self):
        # Chromium can die (OOM, crash). Relaunch rather than failing forever.
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self.close()
                await self.start()
        return self._browser

    async def get(self, url, *, headers, impersonate, proxy, timeout, cookies=None):
        # `impersonate` is meaningless here — the fingerprint is a real browser's.
        browser = await self._ensure()

        ctx_args = {
            "user_agent": headers.get("user-agent", _UA),
            "locale": "en-US",
            "timezone_id": "Europe/Paris",
            "viewport": {"width": 1440, "height": 900},
            "extra_http_headers": {
                k: v for k, v in headers.items() if k.lower() != "user-agent"
            },
        }
        if proxy:
            ctx_args["proxy"] = {"server": proxy}

        context = None
        try:
            context = await browser.new_context(**ctx_args)
            await context.add_init_script(_STEALTH_JS)
            if cookies:
                await context.add_cookies(
                    [
                        {"name": k, "value": v, "domain": ".linkedin.com", "path": "/"}
                        for k, v in cookies.items()
                    ]
                )
            page = await context.new_page()
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout * 1000
            )
            # The profile body is server-rendered, but give lazy sections a
            # moment to appear before snapshotting.
            try:
                await page.wait_for_selector(
                    "section.top-card-layout, .authwall, .top-card-layout__title",
                    timeout=3000,
                )
            except Exception:
                pass
            html = await page.content()
            status = response.status if response is not None else 0
            return status, page.url, html
        except Exception as exc:
            message = str(exc).lower()
            if "timeout" in message or "timed out" in message:
                raise UpstreamTimeout(detail=str(exc)[:200]) from exc
            raise UpstreamError(detail=str(exc)[:200]) from exc
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
