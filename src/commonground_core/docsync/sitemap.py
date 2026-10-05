"""Sitemap discovery: urlset and sitemapindex, plain or gzipped."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str | None = None  # as published; many sites (incl. Databricks docs) omit it


def _decompress(body: bytes) -> bytes:
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def parse_sitemap(body: bytes) -> tuple[str, list[SitemapEntry]]:
    """Return ("urlset" | "sitemapindex", entries). Index entries point at child sitemaps."""
    root = ET.fromstring(_decompress(body))
    kind = root.tag.replace(NS, "")
    child = "url" if kind == "urlset" else "sitemap"
    out = []
    for node in root.iter(f"{NS}{child}"):
        loc = node.findtext(f"{NS}loc")
        if not loc:
            continue
        out.append(SitemapEntry(url=loc.strip(), lastmod=(node.findtext(f"{NS}lastmod") or "").strip() or None))
    return kind, out


def discover(
    sitemap_urls: list[str],
    get: Callable[[str], bytes],
    max_depth: int = 3,
) -> tuple[list[SitemapEntry], dict[str, bytes]]:
    """Walk sitemaps (following indexes). Returns (unique page entries, {sitemap_url: body})."""
    seen_maps: dict[str, bytes] = {}
    pages: dict[str, SitemapEntry] = {}

    def walk(url: str, depth: int) -> None:
        if url in seen_maps or depth > max_depth:
            return
        body = get(url)
        seen_maps[url] = _decompress(body)
        kind, entries = parse_sitemap(body)
        for e in entries:
            if kind == "sitemapindex":
                walk(e.url, depth + 1)
            else:
                pages.setdefault(e.url, e)

    for u in sitemap_urls:
        walk(u, 0)
    return list(pages.values()), seen_maps
