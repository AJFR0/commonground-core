"""Polite HTTP: shared rate limit, retries with backoff, conditional GET, robots.txt.

The transport is injectable so tests (and other environments) can swap out `requests`.
"""

from __future__ import annotations

import email.utils
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlparse


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased keys
    url: str = ""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class Transport(Protocol):
    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> Response: ...


def requests_transport(url: str, headers: dict[str, str], timeout: float) -> Response:
    import requests

    r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    return Response(status=r.status_code, body=r.content,
                    headers={k.lower(): v for k, v in r.headers.items()}, url=r.url)


class RateLimiter:
    """At most one request start per `interval` seconds across all threads."""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            wait = self._next - now
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            self.sleep(wait)


class FetchError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, user_agent: str, delay_seconds: float = 0.5, timeout: float = 30.0,
                 max_retries: int = 3, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.ua, self.timeout, self.max_retries = user_agent, timeout, max_retries
        self.transport = transport or requests_transport
        self.sleep = sleep
        self.limiter = RateLimiter(delay_seconds, sleep=sleep)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()

    # -- core ---------------------------------------------------------------------------
    def get(self, url: str, etag: str | None = None, last_modified: str | None = None) -> Response:
        headers = {"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        attempt = 0
        while True:
            self.limiter.wait()
            try:
                resp = self.transport(url, headers, self.timeout)
            except Exception as e:  # network error
                if attempt >= self.max_retries:
                    raise FetchError(f"{url}: {e}") from e
                self.sleep(min(60, 2 ** attempt))
                attempt += 1
                continue
            if resp.status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                self.sleep(_retry_after(resp.header("retry-after")) or min(60, 2 ** attempt))
                attempt += 1
                continue
            return resp

    def get_bytes(self, url: str) -> bytes:
        r = self.get(url)
        if r.status != 200:
            raise FetchError(f"{url}: HTTP {r.status}")
        return r.body

    # -- robots -------------------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        with self._robots_lock:
            if base not in self._robots:
                self._robots[base] = self._load_robots(base)
            rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def _load_robots(self, base: str) -> urllib.robotparser.RobotFileParser | None:
        try:
            r = self.get(base + "/robots.txt")
        except FetchError:
            return None
        if r.status != 200:
            return None
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r.body.decode("utf-8", "replace").splitlines())
        return rp


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        dt = email.utils.parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time()) if dt else None
