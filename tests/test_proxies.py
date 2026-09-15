from __future__ import annotations

import time

import pytest

from app.proxies import ProxyPool, normalize_proxy, parse_proxy_list


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://host:8080", "http://host:8080"),
        ("socks5://host:1080", "socks5://host:1080"),
        ("http://user:pass@host:8080", "http://user:pass@host:8080"),
        ("host.example:8080", "http://host.example:8080"),
        ("1.2.3.4:8080", "http://1.2.3.4:8080"),
        ("1.2.3.4:8080:alice:secret", "http://alice:secret@1.2.3.4:8080"),
    ],
)
def test_normalises_provider_formats(raw, expected):
    assert normalize_proxy(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "# a comment", "garbage", "host-without-port"])
def test_rejects_junk(raw):
    assert normalize_proxy(raw) is None


def test_parses_mixed_separators_and_dedupes():
    proxies = parse_proxy_list("http://a:1, http://b:2\nhttp://a:1\n# note\n\nhttp://c:3")
    assert proxies == ["http://a:1", "http://b:2", "http://c:3"]


def test_no_proxies_means_direct_connection():
    pool = ProxyPool()
    assert len(pool) == 0
    assert pool.acquire() is None
    assert pool.stats() == {"configured": 0, "cooling_down": 0, "available": 0}


def test_round_robin():
    pool = ProxyPool(env_value="http://a:1,http://b:2,http://c:3")
    assert [pool.acquire() for _ in range(4)] == [
        "http://a:1",
        "http://b:2",
        "http://c:3",
        "http://a:1",
    ]


def test_penalised_proxy_is_skipped_until_cooldown_expires():
    pool = ProxyPool(env_value="http://a:1,http://b:2", cooldown_s=0.1)
    pool.penalise("http://a:1")
    assert {pool.acquire() for _ in range(4)} == {"http://b:2"}
    time.sleep(0.15)
    assert "http://a:1" in {pool.acquire() for _ in range(4)}


def test_reward_clears_cooldown():
    pool = ProxyPool(env_value="http://a:1,http://b:2", cooldown_s=60)
    pool.penalise("http://a:1")
    pool.reward("http://a:1")
    assert "http://a:1" in {pool.acquire() for _ in range(4)}


def test_falls_back_to_direct_when_everything_is_cooling_down():
    pool = ProxyPool(env_value="http://a:1,http://b:2", cooldown_s=60)
    pool.penalise("http://a:1")
    pool.penalise("http://b:2")
    assert pool.acquire() is None


def test_penalise_none_is_safe():
    pool = ProxyPool()
    pool.penalise(None)
    pool.reward(None)


def test_reads_the_persistent_storage_file(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text("http://file-a:1\nhttp://file-b:2\n", encoding="utf-8")
    pool = ProxyPool(file_path=str(path))
    assert len(pool) == 2


def test_merges_env_and_file_without_duplicates(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text("http://shared:1\nhttp://file-only:2\n", encoding="utf-8")
    pool = ProxyPool(env_value="http://shared:1,http://env-only:3", file_path=str(path))
    assert pool.all == ["http://shared:1", "http://env-only:3", "http://file-only:2"]


def test_file_changes_are_picked_up_without_a_restart(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text("http://a:1\n", encoding="utf-8")
    pool = ProxyPool(file_path=str(path))
    assert len(pool) == 1

    time.sleep(0.01)
    path.write_text("http://a:1\nhttp://b:2\n", encoding="utf-8")
    pool.acquire()  # triggers the mtime check
    assert len(pool) == 2


def test_missing_file_is_not_an_error():
    pool = ProxyPool(file_path="/nonexistent/path/proxies.txt")
    assert len(pool) == 0
    assert pool.acquire() is None


def test_stats_report_cooldowns():
    pool = ProxyPool(env_value="http://a:1,http://b:2", cooldown_s=60)
    pool.penalise("http://a:1")
    assert pool.stats() == {"configured": 2, "cooling_down": 1, "available": 1}
