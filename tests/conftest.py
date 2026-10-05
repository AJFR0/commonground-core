import gzip
from pathlib import Path

import pytest

from commonground_core.docsync import config_from_dict
from commonground_core.docsync.fetch import HttpClient, Response

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://docs.example.com"


def page_html(title: str, body: str, updated: str = "2026-09-14", bundle: str = "1") -> str:
    return f"""<!DOCTYPE html><html><head><title>{title} | Docs</title>
<meta property="og:title" content="{title} | Docs"><link rel="canonical" href="{BASE}/x">
</head><body><nav class="navbar">nav</nav><main><article>
<nav class="theme-doc-breadcrumbs"><ul><li><a href="/"></a></li><li><span>Guides</span></li><li><span>{title}</span></li></ul></nav>
<span class="theme-last-updated">Last updated on <b><time datetime="{updated}T00:00:00.000Z">x</time></b></span>
<div class="theme-doc-markdown markdown"><header><h1>{title}</h1></header>{body}</div></article></main>
<footer>footer</footer><script src="/bundle.{bundle}.js"></script></body></html>"""


class FakeSite:
    """In-memory website: robots.txt, gzipped sitemap index -> urlset, pages with ETags."""

    def __init__(self):
        self.pages: dict[str, str] = {}
        self.lastmod: dict[str, str] = {}
        self.gone: set[str] = set()
        self.honor_etag = True
        self.robots = "User-agent: *\nDisallow: /private/\n"
        self.requests: list[tuple[str, dict]] = []
        self.fail_once: dict[str, int] = {}

    def set(self, path: str, html: str, lastmod: str | None = None):
        self.pages[BASE + path] = html
        if lastmod:
            self.lastmod[BASE + path] = lastmod

    def sitemap_xml(self) -> bytes:
        urls = "".join(
            f"<url><loc>{u}</loc>" + (f"<lastmod>{self.lastmod[u]}</lastmod>" if u in self.lastmod else "") + "</url>"
            for u in sorted(self.pages))
        return (f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{urls}</urlset>").encode()

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers)))
        if self.fail_once.get(url):
            self.fail_once[url] -= 1
            return Response(429, b"slow down", {"retry-after": "0"}, url)
        if url == BASE + "/robots.txt":
            return Response(200, self.robots.encode(), {}, url)
        if url == BASE + "/sitemap_index.xml":
            body = (f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f"<sitemap><loc>{BASE}/sitemap.xml.gz</loc></sitemap></sitemapindex>").encode()
            return Response(200, body, {}, url)
        if url == BASE + "/sitemap.xml.gz":
            return Response(200, gzip.compress(self.sitemap_xml()), {}, url)
        if url in self.gone or url not in self.pages:
            return Response(404, b"", {}, url)
        body = self.pages[url].encode()
        etag = '"%08x"' % (hash(body) & 0xFFFFFFFF)
        if self.honor_etag and headers.get("If-None-Match") == etag:
            return Response(304, b"", {"etag": etag}, url)
        hdrs = {"etag": etag} if self.honor_etag else {}
        return Response(200, body, hdrs, url)

    def fetched(self) -> list[str]:
        return [u for u, _ in self.requests if not u.endswith((".xml", ".gz", "robots.txt"))]


@pytest.fixture()
def site():
    s = FakeSite()
    s.set("/guides/a", page_html("Alpha", "<p>Alpha body one.</p><h2>Part</h2><p>More alpha.</p>"))
    s.set("/guides/b", page_html("Beta", "<p>Beta body.</p>"))
    s.set("/private/secret", page_html("Secret", "<p>no</p>"))
    return s


@pytest.fixture()
def cfg():
    return config_from_dict({
        "name": "testdocs",
        "sitemaps": [BASE + "/sitemap_index.xml"],
        "include": [r"^https://docs\.example\.com/"],
        "exclude": [r"/archive/"],
        "fetch": {"delay_seconds": 0, "workers": 2},
        "detect": {"recheck_after_hours": 0},
        "sections": {"/guides/": "fundamentals"},
        "prepare": {"profiles": {
            "markdown": {"format": "markdown"},
            "docs-json": {"format": "json"},
            "chunks": {"format": "chunks", "chunker": "heading", "max_tokens": 40, "overlap_tokens": 8},
        }},
    })


@pytest.fixture()
def client(site):
    return HttpClient("test-agent", delay_seconds=0, transport=site, sleep=lambda s: None)
