from __future__ import annotations

import time

from app.cache import TTLCache


def test_roundtrip():
    cache = TTLCache(maxsize=4, ttl=60)
    cache.set("a", 1)
    assert cache.get("a") == 1


def test_miss_returns_none():
    assert TTLCache().get("nope") is None


def test_entries_expire():
    cache = TTLCache(maxsize=4, ttl=0.05)
    cache.set("a", 1)
    time.sleep(0.08)
    assert cache.get("a") is None
    assert len(cache) == 0


def test_evicts_least_recently_used():
    cache = TTLCache(maxsize=2, ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")           # 'a' is now the most recently used
    cache.set("c", 3)        # evicts 'b'
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_zero_ttl_disables_caching():
    cache = TTLCache(maxsize=4, ttl=0)
    cache.set("a", 1)
    assert cache.get("a") is None


def test_clear():
    cache = TTLCache()
    cache.set("a", 1)
    cache.clear()
    assert len(cache) == 0
