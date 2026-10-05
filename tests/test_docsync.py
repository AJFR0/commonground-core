import json
from datetime import datetime, timedelta, timezone

from conftest import BASE, FIXTURES, page_html

from commonground_core.docsync import Store, extract, prepare, sync
from commonground_core.docsync.chunkers import chunk_heading, chunk_parent_child, estimate_tokens
from commonground_core.docsync.config import DEFAULT_CONTENT_SELECTORS, DEFAULT_STRIP_SELECTORS, config_from_dict
from commonground_core.docsync.export import export
from commonground_core.docsync.fetch import HttpClient
from commonground_core.docsync.sitemap import parse_sitemap
from commonground_core.docsync.sync import plan


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def tick(self, **kw):
        self.t += timedelta(**kw)


def run(cfg, tmp_path, client, clock, **kw):
    return sync(cfg, Store(tmp_path, cfg.name), client, now=clock, **kw)


# ---------------------------------------------------------------- extraction
def test_extract_real_docusaurus_page():
    html = (FIXTURES / "page_ai_search.html").read_text()
    p = extract(html, "https://docs.databricks.com/aws/en/ai-search/ai-search",
                DEFAULT_CONTENT_SELECTORS, DEFAULT_STRIP_SELECTORS)
    assert p.title == "Databricks AI Search | Databricks on AWS"
    assert p.h1 == "Databricks AI Search"
    assert p.last_updated == "2026-09-14"
    assert p.breadcrumbs == ["Agents", "AI Search"]
    assert p.selector == "div.theme-doc-markdown"
    md = p.markdown
    assert md.startswith("# Databricks AI Search\n")
    assert "## How does AI Search work?\n" in md and "​" not in md
    assert "[Iceberg v3](https://docs.databricks.com/aws/en/iceberg/iceberg-v3)" in md
    assert "> **Note:** It is not possible" in md
    assert "```python\nfrom databricks.ai_search.client import AISearchClient\nclient = AISearchClient()\n```" in md
    assert "| Option | Embeddings |\n| --- | --- |" in md
    assert "TOP NAV" not in md and "Copyright" not in md and "Copy" not in md and "__bundle" not in md


def test_content_hash_ignores_chrome_and_last_updated():
    a = extract(page_html("T", "<p>same</p>", updated="2026-01-01", bundle="1"), "u",
                DEFAULT_CONTENT_SELECTORS, DEFAULT_STRIP_SELECTORS)
    b = extract(page_html("T", "<p>same</p>", updated="2026-02-02", bundle="2"), "u",
                DEFAULT_CONTENT_SELECTORS, DEFAULT_STRIP_SELECTORS)
    c = extract(page_html("T", "<p>different</p>"), "u", DEFAULT_CONTENT_SELECTORS, DEFAULT_STRIP_SELECTORS)
    assert a.content_hash == b.content_hash != c.content_hash
    assert (a.last_updated, b.last_updated) == ("2026-01-01", "2026-02-02")


def test_sitemap_index_and_gzip(site):
    kind, entries = parse_sitemap(site.sitemap_xml())
    assert kind == "urlset" and len(entries) == 3


# ---------------------------------------------------------------- sync lifecycle
def test_first_run_adds_and_respects_robots(cfg, tmp_path, client):
    clock = Clock()
    res = run(cfg, tmp_path, client, clock)
    assert res.counts == {"added": 2, "blocked": 1}
    store = Store(tmp_path, cfg.name)
    m = store.load_manifest()
    a = m["docs"][BASE + "/guides/a"]
    assert a["status"] == "active" and a["version"] == 1 and a["section"] == "fundamentals"
    assert a["page_last_updated"] == "2026-09-14" and a["breadcrumbs"] == ["Guides", "Alpha"]
    assert store.read(a["raw_path"]).startswith(b"<!DOCTYPE html>")
    assert m["docs"][BASE + "/private/secret"]["status"] == "blocked"
    assert len(store.list("sitemaps")) == 2  # the index and its child sitemap
    assert [e["event"] for e in store.iter_events()].count("added") == 2


def test_rerun_uses_304_and_stores_nothing_new(cfg, tmp_path, client, site):
    clock = Clock()
    run(cfg, tmp_path, client, clock)
    clock.tick(minutes=5)
    site.requests.clear()
    res = run(cfg, tmp_path, client, clock)
    assert res.counts == {"unchanged": 2, "blocked": 1}
    assert all("If-None-Match" in h for u, h in site.requests if u.endswith(("/a", "/b")))
    assert len(Store(tmp_path, cfg.name).raw_versions(BASE + "/guides/a")) == 1


def test_touched_vs_updated(cfg, tmp_path, client, site):
    clock = Clock()
    run(cfg, tmp_path, client, clock)
    site.honor_etag = False  # force full bodies so the content hash decides
    site.set("/guides/a", page_html("Alpha", "<p>Alpha body one.</p><h2>Part</h2><p>More alpha.</p>",
                                    updated="2026-10-01", bundle="9"))
    site.set("/guides/b", page_html("Beta", "<p>Beta body, now with a new sentence.</p>"))
    clock.tick(hours=1)
    res = run(cfg, tmp_path, client, clock)
    assert res.counts["touched"] == 1 and res.counts["updated"] == 1
    store = Store(tmp_path, cfg.name)
    m = store.load_manifest()["docs"]
    assert m[BASE + "/guides/a"]["version"] == 1 and m[BASE + "/guides/a"]["page_last_updated"] == "2026-10-01"
    assert m[BASE + "/guides/b"]["version"] == 2
    assert len(store.raw_versions(BASE + "/guides/b")) == 2
    upd = [e for e in store.iter_events() if e["event"] == "updated"][0]
    assert upd["prev_content_hash"] and upd["prev_content_hash"] != upd["content_hash"]


def test_removed_from_sitemap_and_404(cfg, tmp_path, client, site):
    clock = Clock()
    run(cfg, tmp_path, client, clock)
    del site.pages[BASE + "/guides/b"]          # leaves the sitemap
    site.set("/guides/c", page_html("Gamma", "<p>new page</p>"))
    clock.tick(hours=1)
    res = run(cfg, tmp_path, client, clock)
    assert res.counts.get("removed") == 1 and res.counts.get("added") == 1
    m = Store(tmp_path, cfg.name).load_manifest()["docs"]
    assert m[BASE + "/guides/b"]["status"] == "removed"
    # raw history is kept
    assert Store(tmp_path, cfg.name).raw_versions(BASE + "/guides/b")

    site.gone.add(BASE + "/guides/c")           # still in sitemap but 404s
    clock.tick(hours=1)
    res = run(cfg, tmp_path, client, clock)
    assert res.counts.get("removed") == 1


def test_recheck_window_and_new_only(cfg, tmp_path, client, site):
    cfg.detect.recheck_after_hours = 24
    clock = Clock()
    run(cfg, tmp_path, client, clock)
    site.set("/guides/c", page_html("Gamma", "<p>new</p>"))
    clock.tick(hours=1)
    site.requests.clear()
    res = run(cfg, tmp_path, client, clock)          # only the new page is due
    assert res.counts == {"added": 1}
    assert site.fetched() == [BASE + "/guides/c"]

    site.set("/guides/d", page_html("Delta", "<p>new</p>"))
    clock.tick(hours=30)
    site.requests.clear()
    res = run(cfg, tmp_path, client, clock, mode="new-only")
    assert res.counts == {"added": 1}


def test_sitemap_lastmod_triggers_recheck(cfg, site, tmp_path, client):
    cfg.detect.recheck_after_hours = 24
    clock = Clock()
    site.lastmod[BASE + "/guides/a"] = "2026-10-01"
    run(cfg, tmp_path, client, clock)
    site.lastmod[BASE + "/guides/a"] = "2026-10-05"
    clock.tick(hours=1)
    site.requests.clear()
    run(cfg, tmp_path, client, clock)
    assert site.fetched() == [BASE + "/guides/a"]


def test_dry_run_writes_nothing(cfg, tmp_path, client):
    res = run(cfg, tmp_path, client, Clock(), dry_run=True)
    assert res.counts == {"would_fetch": 3, "would_remove": 0}
    assert not (tmp_path / cfg.name / "manifest.json").exists()


def test_retry_on_429(cfg, tmp_path, site):
    site.fail_once[BASE + "/guides/a"] = 2
    client = HttpClient("t", delay_seconds=0, transport=site, sleep=lambda s: None)
    res = run(cfg, tmp_path, client, Clock())
    assert res.counts["added"] == 2


def test_limit_and_only(cfg, tmp_path, client):
    res = run(cfg, tmp_path, client, Clock(), only="/guides/", limit=1)
    assert res.planned == 1 and res.counts == {"added": 1}


# ---------------------------------------------------------------- prepare / export
def test_prepare_formats_and_incremental(cfg, tmp_path, client, site):
    clock = Clock()
    run(cfg, tmp_path, client, clock)
    store = Store(tmp_path, cfg.name)

    r = prepare(cfg, store, "markdown")
    assert len(r.written) == 2
    md = store.read("prepared/markdown/docs/docs.example.com/guides/a.md").decode()
    assert md.startswith("---\n") and "url: https://docs.example.com/guides/a" in md and "# Alpha" in md

    j = json.loads(store.read("prepared/docs-json/docs/docs.example.com/guides/b.json")
                   if prepare(cfg, store, "docs-json").written else b"{}")
    assert j["title"] == "Beta | Docs" and j["markdown"].startswith("# Beta")

    r = prepare(cfg, store, "markdown")             # nothing changed
    assert r.written == [] and r.skipped == 2

    site.honor_etag = False
    site.set("/guides/b", page_html("Beta", "<p>Beta changed.</p>"))
    clock.tick(hours=1)
    run(cfg, tmp_path, client, clock)
    r = prepare(cfg, store, "markdown")
    assert r.written == [BASE + "/guides/b"]

    del site.pages[BASE + "/guides/b"]
    clock.tick(hours=1)
    run(cfg, tmp_path, client, clock)
    r = prepare(cfg, store, "markdown")
    assert r.removed == [BASE + "/guides/b"]
    assert not store.exists("prepared/markdown/docs/docs.example.com/guides/b.md")


def test_prepare_chunks(cfg, tmp_path, client):
    run(cfg, tmp_path, client, Clock())
    store = Store(tmp_path, cfg.name)
    r = prepare(cfg, store, "chunks")
    assert r.chunks >= 2
    lines = store.read("prepared/chunks/docs/docs.example.com/guides/a.jsonl").decode().splitlines()
    recs = [json.loads(x) for x in lines]
    assert {"chunk_id", "text", "text_to_embed", "heading_path", "url", "content_hash"} <= set(recs[0])
    assert recs[0]["text_to_embed"].startswith("Alpha | Docs")
    assert any(r["heading_path"][-1:] == ["Part"] for r in recs)


def test_export_copies_only_changes(cfg, tmp_path, client):
    run(cfg, tmp_path / "data", client, Clock())
    store = Store(tmp_path / "data", cfg.name)
    prepare(cfg, store, "markdown")
    dest = tmp_path / "volume"
    r1 = export(store, "prepared/markdown", str(dest))
    assert len(r1.copied) == 2 and (dest / "docs/docs.example.com/guides/a.md").exists()
    assert not (dest / "_state.json").exists()
    r2 = export(store, "prepared/markdown", str(dest))
    assert r2.copied == [] and r2.unchanged == 2


# ---------------------------------------------------------------- chunkers
LONG_MD = "# Title\n\nIntro para.\n\n## Section A\n\n" + "\n\n".join(
    f"Paragraph {i} " + "word " * 40 for i in range(6)) + "\n\n```sql\nSELECT 1;\n\nSELECT 2;\n```\n\n## Section B\n\nShort."


def test_heading_chunker_limits_and_keeps_code_whole():
    chunks = chunk_heading(LONG_MD, max_tokens=120, overlap_tokens=20)
    assert all(c.tokens <= 120 or "\n\n" not in c.text for c in chunks)
    code = [c for c in chunks if "```sql" in c.text]
    assert code and "SELECT 2;\n```" in code[0].text
    assert chunks[-1].heading_path == ["Title", "Section B"]


def test_parent_child_links():
    chunks = chunk_parent_child(LONG_MD, child_tokens=80, parent_tokens=400)
    parents = [c for c in chunks if c.role == "parent"]
    children = [c for c in chunks if c.role == "child"]
    assert parents and children and all(c.parent_position is not None for c in children)
    rec = children[0].to_record({"doc_id": "d", "url": "u", "title": "T"})
    assert rec["parent_id"] and rec["role"] == "child"


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 10) == 10


def test_plan_orders_added_first():
    cfg = config_from_dict({"name": "x", "detect": {"recheck_after_hours": 1}})
    from commonground_core.docsync.sitemap import SitemapEntry
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    manifest = {"docs": {"u1": {"status": "active", "last_checked": "2026-01-01T00:00:00+00:00"},
                         "gone": {"status": "active", "last_checked": "2026-01-01T00:00:00+00:00"}}}
    todo, removed = plan(cfg, manifest, [SitemapEntry("u1"), SitemapEntry("u2")], "incremental", now)
    assert [e.url for e in todo] == ["u2", "u1"] and removed == ["gone"]
