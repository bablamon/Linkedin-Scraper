from __future__ import annotations

import asyncio
import time

from app.ratelimit import Pacer, TokenBucket


async def test_allows_exactly_the_budget_then_rejects():
    bucket = TokenBucket(10, 60.0)
    results = [await bucket.acquire() for _ in range(10)]
    assert all(allowed for allowed, _ in results)

    allowed, retry_after = await bucket.acquire()
    assert allowed is False
    assert 0 < retry_after <= 6.0


async def test_retry_after_is_the_real_wait():
    bucket = TokenBucket(6, 60.0)  # one token every 10s
    for _ in range(6):
        await bucket.acquire()
    _, retry_after = await bucket.acquire()
    assert 9.0 < retry_after <= 10.0


async def test_tokens_refill_over_time():
    bucket = TokenBucket(2, 0.2)  # refills fully in 200ms
    assert (await bucket.acquire())[0]
    assert (await bucket.acquire())[0]
    assert not (await bucket.acquire())[0]
    await asyncio.sleep(0.25)
    assert (await bucket.acquire())[0]


async def test_never_accumulates_beyond_capacity():
    bucket = TokenBucket(3, 0.05)
    await asyncio.sleep(0.2)
    assert await bucket.tokens_left() == 3


async def test_concurrent_callers_cannot_oversubscribe():
    bucket = TokenBucket(10, 60.0)
    results = await asyncio.gather(*(bucket.acquire() for _ in range(40)))
    assert sum(1 for allowed, _ in results if allowed) == 10


# asyncio.sleep can return fractionally early (the loop clock and time.monotonic
# are not the same clock, and Windows timers are coarse), so wall-clock
# assertions carry a small tolerance rather than demanding the exact figure.
TIMER_TOLERANCE = 0.9


async def test_pacer_spaces_outbound_calls():
    interval = 0.05
    pacer = Pacer(interval, 0.0)
    started = time.monotonic()
    for _ in range(4):
        await pacer.wait()
    elapsed = time.monotonic() - started
    # First call is free; the next three each wait one interval.
    assert elapsed >= 3 * interval * TIMER_TOLERANCE


async def test_pacer_serialises_concurrent_callers():
    interval = 0.04
    pacer = Pacer(interval, 0.0)
    started = time.monotonic()
    await asyncio.gather(*(pacer.wait() for _ in range(5)))
    assert time.monotonic() - started >= 4 * interval * TIMER_TOLERANCE


async def test_pacer_with_zero_interval_is_a_noop():
    pacer = Pacer(0.0, 0.0)
    started = time.monotonic()
    for _ in range(20):
        await pacer.wait()
    assert time.monotonic() - started < 0.1


async def test_pacer_jitter_stays_within_bounds():
    pacer = Pacer(0.01, 0.02)
    started = time.monotonic()
    for _ in range(3):
        await pacer.wait()
    # Two waits, each at most interval+jitter.
    assert time.monotonic() - started <= 0.2
