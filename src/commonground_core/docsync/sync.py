"""The sync engine: discover -> plan -> fetch -> detect change -> store -> log.

Change detection is layered, cheapest first:

1. **Sitemap diff.** New URLs are `added`; URLs that left the sitemap are `removed`
   (raw history is kept). A sitemap's own hash is recorded each run.
2. **Sitemap <lastmod>**, when a site publishes it: newer than stored -> re-check now.
3. **HTTP validators.** Re-checks send If-None-Match / If-Modified-Since; a 304 costs
   almost nothing and means `unchanged`.
4. **Content hash** of the normalized main-content text. Same hash with different bytes
   (new script bundle, new footer, new "Last updated" date only) -> `touched`, metadata
   refreshed, no new version. Different hash -> `updated`, a new raw version is stored.

Modes: `incremental` (default) re-checks pages older than `recheck_after_hours` plus anything
added or with a newer lastmod; `full` re-checks everything; `new-only` fetches only added URLs.
"""

from __future__ import annotations

import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .config import SourceConfig
from .extract import extract
from .fetch import FetchError, HttpClient
from .sitemap import SitemapEntry, discover
from .store import Store, doc_path

MODES = ("incremental", "full", "new-only")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ts_compact(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


@dataclass
class SyncResult:
    run_id: str
    counts: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    planned: int = 0
    discovered: int = 0
    dry_run: bool = False

    def summary(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "discovered": self.discovered, "planned": self.planned,
                "counts": self.counts, "dry_run": self.dry_run}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def plan(cfg: SourceConfig, manifest: dict[str, Any], entries: list[SitemapEntry], mode: str,
         now: datetime, only: str | None = None, limit: int | None = None) -> tuple[list[SitemapEntry], list[str]]:
    """Return (entries to fetch, urls removed from the sitemap)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    docs = manifest.get("docs", {})
    wanted = {e.url: e for e in entries}
    removed = [u for u, d in docs.items() if d.get("status") == "active" and u not in wanted]
    only_re = re.compile(only) if only else None
    recheck = timedelta(hours=cfg.detect.recheck_after_hours)

    added, due = [], []
    for e in entries:
        if only_re and not only_re.search(e.url):
            continue
        d = docs.get(e.url)
        if d is None or d.get("status") in ("removed", None):
            added.append(e)
            continue
        if mode == "new-only":
            continue
        if mode == "full":
            due.append((None, e))
            continue
        last = _parse_iso(d.get("last_checked"))
        newer_lastmod = e.lastmod and (d.get("sitemap_lastmod") or "") < e.lastmod
        if d.get("status") == "error" or last is None or now - last >= recheck or newer_lastmod:
            due.append((last, e))
    due.sort(key=lambda t: t[0] or datetime.min.replace(tzinfo=timezone.utc))  # stalest first
    todo = added + [e for _, e in due]
    if limit is not None:
        todo = todo[:limit]
    return todo, removed


def sync(cfg: SourceConfig, store: Store, client: HttpClient, mode: str = "incremental",
         only: str | None = None, limit: int | None = None, dry_run: bool = False,
         now: Callable[[], datetime] = utcnow, log: Callable[[str], None] = lambda m: None) -> SyncResult:
    started = now()
    run_id = ts_compact(started)
    manifest = store.load_manifest()
    docs: dict[str, Any] = manifest.setdefault("docs", {})
    result = SyncResult(run_id=run_id, dry_run=dry_run)

    # 1) discover
    entries, map_bodies = discover(cfg.sitemaps, client.get_bytes) if cfg.sitemaps else ([], {})
    entries += [SitemapEntry(u) for u in cfg.urls if u not in {e.url for e in entries}]
    entries = [e for e in entries if cfg.wants(e.url)]
    result.discovered = len(entries)
    prev_maps = manifest.setdefault("sitemaps", {})
    for url, body in map_bodies.items():
        sha = hashlib.sha256(body).hexdigest()
        changed = prev_maps.get(url, {}).get("sha256") != sha
        if changed and not dry_run:
            store.save_sitemap(url, sha, body)
        prev_maps[url] = {"sha256": sha, "checked": started.isoformat(),
                          "changed": started.isoformat() if changed else prev_maps.get(url, {}).get("changed")}

    # 2) plan
    todo, removed = plan(cfg, manifest, entries, mode, started, only, limit)
    result.planned = len(todo)
    log(f"discovered {len(entries)} urls; fetching {len(todo)}; {len(removed)} left the sitemap")
    if dry_run:
        result.counts = {"would_fetch": len(todo), "would_remove": len(removed)}
        result.events = [{"url": e.url, "event": "planned"} for e in todo]
        return result

    lock = threading.Lock()
    events: list[dict[str, Any]] = []

    def emit(ev: dict[str, Any]) -> None:
        with lock:
            ev.setdefault("run_id", run_id)
            ev.setdefault("ts", now().isoformat())
            events.append(ev)
            result.counts[ev["event"]] = result.counts.get(ev["event"], 0) + 1

    for url in removed:
        d = docs[url]
        d["status"], d["removed_at"] = "removed", started.isoformat()
        emit({"url": url, "doc_id": doc_path(url), "event": "removed", "version": d.get("version")})

    # 3) fetch + detect
    def handle(entry: SitemapEntry) -> None:
        url = entry.url
        with lock:
            prev = dict(docs.get(url) or {})
        rec = dict(prev)
        rec.update({"url": url, "doc_id": doc_path(url), "section": cfg.section_for(url),
                    "sitemap_lastmod": entry.lastmod or prev.get("sitemap_lastmod")})
        rec.setdefault("first_seen", started.isoformat())
        is_new = prev.get("status") in (None, "removed") or not prev.get("content_hash")

        if cfg.respect_robots and not client.allowed(url):
            rec.update(status="blocked", last_checked=now().isoformat())
            with lock:
                docs[url] = rec
            emit({"url": url, "doc_id": rec["doc_id"], "event": "blocked"})
            return
        try:
            resp = client.get(url, etag=None if is_new else prev.get("etag"),
                              last_modified=None if is_new else prev.get("last_modified"))
        except FetchError as e:
            rec.update(status="error", error=str(e), last_checked=now().isoformat())
            with lock:
                docs[url] = rec
            emit({"url": url, "doc_id": rec["doc_id"], "event": "error", "error": str(e)})
            return

        checked = now()
        rec["last_checked"], rec["http_status"] = checked.isoformat(), resp.status
        if resp.status == 304 and not is_new:
            rec["status"] = "active"
            with lock:
                docs[url] = rec
            emit({"url": url, "doc_id": rec["doc_id"], "event": "unchanged", "via": "304"})
            return
        if resp.status in (404, 410):
            rec.update(status="removed", removed_at=checked.isoformat())
            with lock:
                docs[url] = rec
            emit({"url": url, "doc_id": rec["doc_id"], "event": "removed", "via": str(resp.status)})
            return
        if resp.status != 200:
            rec.update(status="error", error=f"HTTP {resp.status}")
            with lock:
                docs[url] = rec
            emit({"url": url, "doc_id": rec["doc_id"], "event": "error", "error": f"HTTP {resp.status}"})
            return

        html = resp.body.decode("utf-8", "replace")
        page = extract(html, url, cfg.detect.content_selectors, cfg.detect.strip_selectors)
        raw_hash = hashlib.sha256(resp.body).hexdigest()
        rec.update(status="active", error=None, etag=resp.header("etag"),
                   last_modified=resp.header("last-modified"), raw_hash=raw_hash,
                   title=page.title, h1=page.h1, canonical=page.canonical, description=page.description,
                   breadcrumbs=page.breadcrumbs, headings=page.headings, word_count=page.word_count,
                   page_last_updated=page.last_updated, selector=page.selector,
                   final_url=resp.url or url)

        if not is_new and prev.get("content_hash") == page.content_hash:
            event = "touched" if (prev.get("raw_hash") != raw_hash
                                  or prev.get("page_last_updated") != page.last_updated) else "unchanged"
            with lock:
                docs[url] = rec
            emit({"url": url, "doc_id": rec["doc_id"], "event": event, "version": rec.get("version"),
                  "content_hash": page.content_hash, "page_last_updated": page.last_updated})
            return

        version = int(prev.get("version") or 0) + 1
        meta = {k: rec.get(k) for k in ("url", "doc_id", "title", "h1", "canonical", "description",
                                        "breadcrumbs", "headings", "page_last_updated", "etag",
                                        "last_modified", "section", "word_count", "final_url")}
        meta.update(version=version, fetched_at=checked.isoformat(), content_hash=page.content_hash,
                    raw_hash=raw_hash, source=cfg.name)
        raw_rel = store.write_raw(url, version, ts_compact(checked), page.content_hash, resp.body, meta)
        rec.update(version=version, content_hash=page.content_hash, raw_path=raw_rel,
                   last_changed=checked.isoformat())
        with lock:
            docs[url] = rec
        emit({"url": url, "doc_id": rec["doc_id"], "event": "added" if is_new else "updated",
              "version": version, "content_hash": page.content_hash,
              "prev_content_hash": prev.get("content_hash"), "page_last_updated": page.last_updated,
              "raw_path": raw_rel})

    done = 0
    checkpoint = 50
    with ThreadPoolExecutor(max_workers=max(1, cfg.fetch.workers)) as pool:
        for _ in pool.map(handle, todo):
            done += 1
            if done % checkpoint == 0:  # survive interruptions on long first runs
                with lock:
                    store.save_manifest(manifest)
                log(f"  {done}/{len(todo)}")

    manifest["updated_at"] = now().isoformat()
    manifest["last_run"] = run_id
    store.save_manifest(manifest)
    interesting = [e for e in events if e["event"] != "unchanged"]
    store.write_events(run_id, interesting)
    result.events = events
    summary = result.summary() | {"started": started.isoformat(), "finished": now().isoformat(),
                                  "mode": mode, "source": cfg.name}
    store.write_run(run_id, summary)
    return result
