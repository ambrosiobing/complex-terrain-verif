"""One HTTP client for every provider: retries, backoff, rate limits, cache, manifest.

Before this module each fetcher called ``requests.get`` and slept a fixed fraction of a
second between calls, which works until a provider is busy and then fails in a way that
looks like missing data rather than like a refused request. National services publish hard
limits and expect a client to respect them: a refusal carries ``Retry-After`` or a
``X-RateLimit-Reset``, a temporary fault carries a 5xx, and the correct response to each
is to wait and try again, not to give up and not to hammer.

Four rules, and each is a test:

* **Retry only what can succeed later.** A 429 or a 5xx or a dropped connection is worth
  another attempt. A 404 or a 400 is not: trying again just spends someone's quota to be
  told the same thing.
* **Honour the provider's own number first.** If it says how long to wait, that is the
  wait. The backoff curve is only for when it does not say.
* **Add jitter.** Several clients backing off on the same curve return together and
  recreate the burst that caused the refusal.
* **Cache by the request, not by the clock.** A repeated run must cost the provider
  nothing, and a cached file is accompanied by the request that produced it and a checksum,
  so a stale answer can be told from a fresh one.

Synchronous on purpose. The asynchronous version of this is worth writing when a domain
polls thousands of endpoints at once; these fetches are tens of chunked requests, where
concurrency would add a failure mode and save nothing. The structure below keeps the retry
policy separate from the transport so that an asynchronous backend can be added later
without touching the callers.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

#: Statuses worth another attempt. Everything else is the provider's final answer.
RETRIABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ApiError(RuntimeError):
    """A request failed in a way no further attempt would fix."""


class RateLimited(ApiError):
    """Attempts were exhausted while the provider was still refusing."""


@dataclass(frozen=True)
class RetryPolicy:
    """How long to wait, and how many times.

    ``base * 2 ** (attempt - 1)``, capped, then multiplied by a random factor in
    ``[1 - jitter, 1 + jitter]``. With the defaults the waits are about 1, 2, 4, 8, 16
    seconds, each give or take a quarter, and never more than ``cap_s``.
    """
    max_attempts: int = 5
    base_s: float = 1.0
    cap_s: float = 60.0
    jitter: float = 0.25

    def backoff(self, attempt: int, rng: random.Random | None = None) -> float:
        if attempt < 1:
            raise ValueError("attempt is one-based")
        raw = min(self.base_s * (2 ** (attempt - 1)), self.cap_s)
        r = rng or random
        return max(0.0, raw * (1.0 + r.uniform(-self.jitter, self.jitter)))


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header, which may be seconds or a date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    for fmt in ("%a, %d %b %Y %H:%M:%S GMT", "%A, %d-%b-%y %H:%M:%S GMT"):
        try:
            when = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        current = now or datetime.now(timezone.utc)
        return max(0.0, (when - current).total_seconds())
    return None


def cache_key(url: str, params: dict | None = None) -> str:
    """A stable name for a request, so the same request finds the same file."""
    blob = json.dumps({"url": url, "params": params or {}}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


@dataclass
class Attempt:
    """What happened on one try, kept so a report can explain a gap."""
    number: int
    status: int | None
    waited_s: float
    reason: str


@dataclass
class Client:
    """A cached, polite HTTP client.

    ``sleeper`` and ``rng`` are injected so that the retry behaviour can be tested without
    waiting and without flakiness. Nothing else in the repository calls ``requests.get``.
    """
    cache_dir: Path | None = None
    user_agent: str = "complex-terrain-verif (+https://github.com/ambrosiobing/complex-terrain-verif)"
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_s: float = 60.0
    session: Any = None
    sleeper: Callable[[float], None] = time.sleep
    rng: random.Random | None = None
    attempts: list[Attempt] = field(default_factory=list)

    def __post_init__(self):
        if self.cache_dir is not None:
            self.cache_dir = Path(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.session is None:
            self.session = requests.Session()

    # Transport ---------------------------------------------------------------------

    def _wait_for(self, response, attempt: int) -> float:
        """The provider's own number if it gave one, otherwise the backoff curve."""
        if response is not None:
            hinted = parse_retry_after(response.headers.get("Retry-After"))
            if hinted is not None:
                return hinted
            remaining = response.headers.get("X-RateLimit-Remaining")
            reset = response.headers.get("X-RateLimit-Reset")
            if remaining is not None and reset is not None:
                try:
                    if int(remaining) <= 0:
                        return max(0.0, float(reset))
                except ValueError:
                    pass
        return self.policy.backoff(attempt, self.rng)

    def request_bytes(self, url: str, params: dict | None = None, auth=None,
                      headers: dict | None = None) -> bytes:
        """Fetch, retrying what can succeed later and refusing what cannot."""
        hdrs = {"User-Agent": self.user_agent, **(headers or {})}
        last = ""
        for attempt in range(1, self.policy.max_attempts + 1):
            response = None
            try:
                response = self.session.get(url, params=params, auth=auth, headers=hdrs,
                                            timeout=self.timeout_s)
                status = response.status_code
                if status < 400:
                    self.attempts.append(Attempt(attempt, status, 0.0, "ok"))
                    return response.content
                if status not in RETRIABLE_STATUS:
                    # The provider's final answer. Another attempt would spend quota to be
                    # told the same thing, so it is reported rather than repeated.
                    raise ApiError(f"{url} returned {status}: {response.text[:200]}")
                last = f"status {status}"
            except requests.RequestException as e:
                status = None
                last = f"{type(e).__name__}: {e}"

            if attempt == self.policy.max_attempts:
                break
            wait = self._wait_for(response, attempt)
            self.attempts.append(Attempt(attempt, status, wait, last))
            self.sleeper(wait)

        self.attempts.append(Attempt(self.policy.max_attempts, None, 0.0, last))
        raise RateLimited(f"{url}: gave up after {self.policy.max_attempts} attempts, last was {last}")

    # Cache -------------------------------------------------------------------------

    def cached_bytes(self, url: str, params: dict | None = None, auth=None,
                     suffix: str = ".bin", key: str | None = None,
                     headers: dict | None = None) -> tuple[bytes, bool]:
        """Return the payload and whether it came from the cache.

        The file is written beside a manifest naming the request, its checksum and when it
        arrived, so a cached answer can always be traced to what produced it.
        """
        if self.cache_dir is None:
            return self.request_bytes(url, params, auth, headers), False
        name = (key or cache_key(url, params)) + suffix
        path = self.cache_dir / name
        if path.exists():
            return path.read_bytes(), True
        payload = self.request_bytes(url, params, auth, headers)
        path.write_bytes(payload)
        manifest = {
            "file": path.name,
            "url": url,
            "params": params or {},
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path.with_suffix(path.suffix + ".manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return payload, False

    def get_json(self, url: str, params: dict | None = None, auth=None,
                 headers: dict | None = None, cache: bool = True) -> dict:
        if cache and self.cache_dir is not None:
            payload, _ = self.cached_bytes(url, params, auth, suffix=".json", headers=headers)
        else:
            payload = self.request_bytes(url, params, auth, headers)
        return json.loads(payload.decode("utf-8"))

    # Provenance ----------------------------------------------------------------------

    def report(self) -> list[dict]:
        """Every attempt made, for the run manifest. A gap must be visible in the output."""
        return [vars(a) for a in self.attempts]
