from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def full_html() -> str:
    return load("profile_full.html")


@pytest.fixture
def dom_only_html() -> str:
    return load("profile_dom_only.html")


@pytest.fixture
def jsonld_only_html() -> str:
    return load("profile_jsonld_only.html")


@pytest.fixture
def authwall_html() -> str:
    return load("authwall.html")


@pytest.fixture
def notfound_html() -> str:
    return load("notfound.html")


class FakeTransport:
    """Scripted responses so the ladder can be driven without a network.

    `script` is a list of (status, final_url, body) or an Exception to raise.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    async def get(self, url, *, headers, impersonate, proxy, timeout, cookies=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "impersonate": impersonate,
                "proxy": proxy,
                "timeout": timeout,
                "cookies": dict(cookies or {}),
            }
        )
        step = self.script.pop(0) if self.script else (200, url, "")
        if isinstance(step, Exception):
            raise step
        status, final_url, body = step
        return status, final_url or url, body


class NoPacer:
    async def wait(self) -> float:
        return 0.0


class NoProxies:
    def acquire(self):
        return None

    def penalise(self, proxy):
        pass

    def reward(self, proxy):
        pass

    def stats(self):
        return {"configured": 0, "cooling_down": 0, "available": 0}

    def __len__(self):
        return 0
