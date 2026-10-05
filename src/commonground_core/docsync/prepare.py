"""Prepare: turn stored raw HTML into whatever a downstream pipeline wants.

Raw HTML is the source of truth. Preparation is a separate, re-runnable step, so you can
change extraction or chunking later and rebuild without fetching anything again.

Formats (set per profile in the source YAML):
  markdown  one .md per page with YAML front matter (feed to ai_prep_search or any tool)
  json      one .json per page: metadata + markdown (read_files / Auto Loader -> Delta)
  chunks    one .jsonl per page of chunk records from a built-in or registered chunker

Incremental by default: a page is re-prepared only when its stored version or content hash
changed since the last prepare of that profile. Pages that were removed have their outputs
deleted and are listed in `_removed.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import yaml

from .chunkers import CHUNKERS
from .config import SourceConfig
from .extract import safe_extract
from .store import Store

FORMATS = ("markdown", "json", "chunks")
EXT = {"markdown": ".md", "json": ".json", "chunks": ".jsonl"}


@dataclass
class PrepareResult:
    profile: str
    written: list[str] = field(default_factory=list)
    skipped: int = 0
    removed: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    chunks: int = 0


def _front_matter(meta: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=True, allow_unicode=True).strip() + "\n---\n\n"


def doc_meta(rec: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": source, "url": rec["url"], "doc_id": rec["doc_id"], "title": rec.get("title"),
        "h1": rec.get("h1"), "description": rec.get("description"), "section": rec.get("section"),
        "breadcrumbs": rec.get("breadcrumbs") or [], "headings": rec.get("headings") or [],
        "page_last_updated": rec.get("page_last_updated"), "version": rec.get("version"),
        "content_hash": rec.get("content_hash"), "fetched_at": rec.get("last_changed"),
        "word_count": rec.get("word_count"),
    }


def prepare(cfg: SourceConfig, store: Store, profile_name: str, full: bool = False,
            only: Callable[[str], bool] | None = None, log: Callable[[str], None] = lambda m: None) -> PrepareResult:
    prof = cfg.profile(profile_name)
    fmt = prof.get("format", "markdown")
    if fmt not in FORMATS:
        raise ValueError(f"profile {profile_name}: format must be one of {FORMATS}")
    chunker = None
    if fmt == "chunks":
        name = prof.get("chunker", "heading")
        if name not in CHUNKERS:
            raise ValueError(f"unknown chunker {name!r}; registered: {sorted(CHUNKERS)}")
        chunker = CHUNKERS[name]
    kwargs = {k: v for k, v in prof.items() if k not in ("format", "chunker")}

    base = f"prepared/{profile_name}"
    state_rel = f"{base}/_state.json"
    state: dict[str, Any] = json.loads(store.read(state_rel)) if store.exists(state_rel) and not full else {}
    manifest = store.load_manifest()
    res = PrepareResult(profile=profile_name)

    for url, rec in sorted(manifest.get("docs", {}).items()):
        doc_id = rec.get("doc_id")
        out_rel = f"{base}/docs/{doc_id}{EXT[fmt]}"
        if rec.get("status") == "removed":
            if doc_id in state:
                store.delete(out_rel)
                state.pop(doc_id, None)
                res.removed.append(url)
            continue
        if rec.get("status") != "active" or not rec.get("raw_path"):
            continue
        if only and not only(url):
            continue
        key = {"version": rec.get("version"), "content_hash": rec.get("content_hash")}
        if state.get(doc_id) == key:
            res.skipped += 1
            continue

        html = store.read(rec["raw_path"]).decode("utf-8", "replace")
        page, err = safe_extract(html, url, cfg.detect.content_selectors, cfg.detect.strip_selectors)
        if err:
            res.failed.append({"url": url, "error": err})  # not marked done; retried next prepare
            continue
        meta = doc_meta(rec, cfg.name)
        if fmt == "markdown":
            data = (_front_matter(meta) + page.markdown).encode()
        elif fmt == "json":
            data = json.dumps(meta | {"markdown": page.markdown}, ensure_ascii=False).encode()
        else:
            records = [c.to_record(meta) for c in chunker(page.markdown, **kwargs)]
            res.chunks += len(records)
            data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode()
        store.write(out_rel, data)
        state[doc_id] = key
        res.written.append(url)

    store.write(state_rel, json.dumps(state, indent=1, sort_keys=True).encode())
    if res.removed:
        store.write(f"{base}/_removed.json", json.dumps(
            {"at": datetime.now(timezone.utc).isoformat(), "urls": res.removed}, indent=1).encode())
    msg = f"{profile_name}: wrote {len(res.written)}, unchanged {res.skipped}, removed {len(res.removed)}"
    log(msg + (f", failed {len(res.failed)}" if res.failed else ""))
    return res
