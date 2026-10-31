"""The shared HTTP client, tested without a network and without waiting.

The sleeper and the random source are injected, so these tests assert the exact waits the
policy produces rather than observing that something slow happened. Every retry rule in
the module docstring has a test here, and the refusals matter more than the successes: a
client that retries a 404 spends someone's quota to be told the same thing, and a client
that gives up on a 429 reports missing data when the data was there.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
import requests

from afv.api import (
    ApiError,
    Client,
    RateLimited,
    RetryPolicy,
    cache_key,
    parse_retry_after,
)


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"{}", headers: dict | None = None):
        self.status_code = status
        self.content = body
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")


class FakeSession:
    """Returns a scripted sequence, and records what it was asked for."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        item = self.script.pop(0) if self.script else FakeResponse(200)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(script, tmp_path=None, **kw):
    waits: list[float] = []
    client = Client(
        cache_dir=tmp_path,
        session=FakeSession(script),
        sleeper=waits.append,
        rng=random.Random(0),
        **kw,
    )
    return client, waits


# Backoff arithmetic -----------------------------------------------------------------

def test_backoff_doubles_and_is_capped():
    p = RetryPolicy(base_s=1.0, cap_s=8.0, jitter=0.0)
    assert [p.backoff(i) for i in range(1, 6)] == [1.0, 2.0, 4.0, 8.0, 8.0]


def test_backoff_applies_jitter_within_the_stated_band():
    p = RetryPolicy(base_s=10.0, cap_s=100.0, jitter=0.25)
    rng = random.Random(1)
    for attempt in (1, 2, 3):
        raw = min(10.0 * 2 ** (attempt - 1), 100.0)
        w = p.backoff(attempt, rng)
        assert 0.75 * raw <= w <= 1.25 * raw


def test_backoff_rejects_a_zero_attempt():
    with pytest.raises(ValueError):
        RetryPolicy().backoff(0)


# Retry-After ---------------------------------------------------------------------------

def test_retry_after_in_seconds():
    assert parse_retry_after("17") == 17.0


def test_retry_after_as_a_date():
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    header = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
    got = parse_retry_after(header, now=datetime.now(timezone.utc))
    assert 25.0 <= got <= 31.0


def test_retry_after_absent_or_unparsable():
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None


# What is retried, and what is not ---------------------------------------------------------

def test_a_rate_limit_is_retried_and_then_succeeds():
    script = [FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(200, b'{"ok":true}')]
    client, waits = make_client(script)
    assert json.loads(client.request_bytes("https://example.test/x")) == {"ok": True}
    # The provider said three seconds, so three seconds it is, not the backoff curve.
    assert waits == [3.0]


def test_the_providers_own_number_beats_the_backoff_curve():
    script = [FakeResponse(429, headers={"Retry-After": "42"}), FakeResponse(200)]
    client, waits = make_client(script, policy=RetryPolicy(base_s=1.0, jitter=0.0))
    client.request_bytes("https://example.test/x")
    assert waits == [42.0]


def test_a_rate_limit_reset_header_is_used_when_no_retry_after():
    script = [FakeResponse(429, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12"}),
              FakeResponse(200)]
    client, waits = make_client(script)
    client.request_bytes("https://example.test/x")
    assert waits == [12.0]


def test_a_server_fault_is_retried():
    script = [FakeResponse(503), FakeResponse(200, b'{"ok":1}')]
    client, waits = make_client(script, policy=RetryPolicy(base_s=2.0, jitter=0.0))
    client.request_bytes("https://example.test/x")
    assert waits == [2.0]


def test_a_dropped_connection_is_retried():
    script = [requests.ConnectionError("reset by peer"), FakeResponse(200)]
    client, waits = make_client(script, policy=RetryPolicy(base_s=1.0, jitter=0.0))
    client.request_bytes("https://example.test/x")
    assert waits == [1.0]


def test_a_not_found_is_not_retried():
    # The provider's final answer. Repeating it is rude and pointless.
    script = [FakeResponse(404, b"no such station"), FakeResponse(200)]
    client, waits = make_client(script)
    with pytest.raises(ApiError, match="404"):
        client.request_bytes("https://example.test/x")
    assert waits == []


def test_a_bad_request_is_not_retried():
    client, waits = make_client([FakeResponse(400, b"bad window")])
    with pytest.raises(ApiError, match="400"):
        client.request_bytes("https://example.test/x")
    assert waits == []


def test_attempts_are_exhausted_and_the_failure_says_so():
    script = [FakeResponse(429) for _ in range(4)]
    client, waits = make_client(script, policy=RetryPolicy(max_attempts=4, base_s=1.0, jitter=0.0))
    with pytest.raises(RateLimited, match="gave up after 4 attempts"):
        client.request_bytes("https://example.test/x")
    # Three waits for four attempts: nothing is slept after the last one.
    assert waits == [1.0, 2.0, 4.0]


def test_the_attempt_log_explains_a_gap():
    script = [FakeResponse(503), FakeResponse(200)]
    client, _ = make_client(script, policy=RetryPolicy(base_s=1.0, jitter=0.0))
    client.request_bytes("https://example.test/x")
    report = client.report()
    assert [a["status"] for a in report] == [503, 200]
    assert report[0]["waited_s"] == 1.0


# Identification and caching ---------------------------------------------------------------

def test_every_request_identifies_itself():
    client, _ = make_client([FakeResponse(200)])
    client.request_bytes("https://example.test/x")
    assert "complex-terrain-verif" in client.session.calls[0]["headers"]["User-Agent"]


def test_cache_key_is_stable_and_separates_different_params():
    a = cache_key("https://x/y", {"b": 2, "a": 1})
    b = cache_key("https://x/y", {"a": 1, "b": 2})
    c = cache_key("https://x/y", {"a": 1, "b": 3})
    assert a == b          # order of the parameters is not part of the request
    assert a != c


def test_a_second_identical_request_costs_the_provider_nothing(tmp_path):
    client, _ = make_client([FakeResponse(200, b'{"v":1}')], tmp_path=tmp_path)
    first, cached_first = client.cached_bytes("https://example.test/x", {"a": 1})
    second, cached_second = client.cached_bytes("https://example.test/x", {"a": 1})
    assert first == second == b'{"v":1}'
    assert cached_first is False and cached_second is True
    assert len(client.session.calls) == 1


def test_a_cached_file_carries_the_request_that_produced_it(tmp_path):
    client, _ = make_client([FakeResponse(200, b'{"v":1}')], tmp_path=tmp_path)
    client.cached_bytes("https://example.test/x", {"a": 1}, suffix=".json")
    manifests = list(tmp_path.glob("*.manifest.json"))
    assert len(manifests) == 1
    meta = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert meta["url"] == "https://example.test/x"
    assert meta["params"] == {"a": 1}
    assert len(meta["sha256"]) == 64
    assert meta["bytes"] == len(b'{"v":1}')


def test_get_json_round_trips(tmp_path):
    client, _ = make_client([FakeResponse(200, b'{"data":[1,2,3]}')], tmp_path=tmp_path)
    assert client.get_json("https://example.test/x")["data"] == [1, 2, 3]
