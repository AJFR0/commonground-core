"""Source configuration for docsync (one YAML file per documentation source)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONTENT_SELECTORS = ["div.theme-doc-markdown", "article", "main"]
DEFAULT_STRIP_SELECTORS = ["nav", "script", "style", "svg", "button", "dialog", "form", "a.hash-link"]


@dataclass
class FetchConfig:
    delay_seconds: float = 0.5
    workers: int = 2
    timeout_seconds: float = 30.0
    max_retries: int = 3


@dataclass
class DetectConfig:
    recheck_after_hours: float = 24.0
    content_selectors: list[str] = field(default_factory=lambda: list(DEFAULT_CONTENT_SELECTORS))
    strip_selectors: list[str] = field(default_factory=lambda: list(DEFAULT_STRIP_SELECTORS))


@dataclass
class SourceConfig:
    name: str
    sitemaps: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)  # extra explicit URLs (no sitemap needed)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    respect_robots: bool = True
    user_agent: str = "commonground-docsync/0.1"
    fetch: FetchConfig = field(default_factory=FetchConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    sections: dict[str, str] = field(default_factory=dict)
    prepare: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.name):
            raise ValueError(f"source name must be filesystem-safe, got {self.name!r}")
        self._inc = [re.compile(p) for p in self.include]
        self._exc = [re.compile(p) for p in self.exclude]

    def wants(self, url: str) -> bool:
        if self._inc and not any(p.search(url) for p in self._inc):
            return False
        return not any(p.search(url) for p in self._exc)

    def section_for(self, url: str) -> str | None:
        """Longest matching path prefix in `sections` -> domain."""
        from urllib.parse import urlparse

        path = urlparse(url).path
        best = None
        for prefix, domain in self.sections.items():
            if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, domain)
        return best[1] if best else None

    def profile(self, name: str) -> dict[str, Any]:
        profiles = (self.prepare or {}).get("profiles", {})
        if name not in profiles:
            raise KeyError(f"unknown prepare profile {name!r}; have: {sorted(profiles)}")
        return dict(profiles[name])


def load_config(path: str | Path) -> SourceConfig:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return config_from_dict(data)


def config_from_dict(data: dict[str, Any]) -> SourceConfig:
    data = dict(data)
    fetch = FetchConfig(**(data.pop("fetch", None) or {}))
    detect = DetectConfig(**(data.pop("detect", None) or {}))
    return SourceConfig(fetch=fetch, detect=detect, **data)
