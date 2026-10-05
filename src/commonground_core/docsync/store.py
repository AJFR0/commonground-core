"""Storage for raw pages, the manifest, the change log, and prepared outputs.

Layout under `<root>/<source>/`:

    manifest.json                       one record per URL (latest state)
    raw/<host>/<path>/v0003_20261005T140000Z_ab12cd34.html   every content version, as served
    raw/<host>/<path>/v0003_20261005T140000Z_ab12cd34.json   metadata sidecar for that version
    events/<run_id>.jsonl               what changed in each run (added/updated/touched/removed/...)
    runs/<run_id>.json                  run summary
    sitemaps/<url-slug>/<sha8>.xml      sitemap snapshots, kept when they change
    prepared/<profile>/...              outputs of `cgdocs prepare`

Writes are whole-file (no appends) so the same code works on a laptop, on a Databricks
Unity Catalog volume (/Volumes/... is a POSIX path), and, with `fsspec`, on object storage.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


# ------------------------------------------------------------------ filesystems
class LocalFS:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _p(self, rel: str) -> Path:
        return self.root / rel

    def read_bytes(self, rel: str) -> bytes:
        return self._p(rel).read_bytes()

    def write_bytes(self, rel: str, data: bytes) -> None:
        p = self._p(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        try:
            os.replace(tmp, p)
        except OSError:  # some FUSE mounts refuse rename-over; fall back to direct write
            p.write_bytes(data)
            tmp.unlink(missing_ok=True)

    def exists(self, rel: str) -> bool:
        return self._p(rel).exists()

    def delete(self, rel: str) -> None:
        try:
            self._p(rel).unlink()
        except FileNotFoundError:
            pass

    def list(self, rel: str) -> list[str]:
        base = self._p(rel)
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()
                      and not p.name.endswith(".tmp"))


class FsspecFS:  # pragma: no cover - exercised only with cloud storage
    def __init__(self, url: str):
        import fsspec

        self.fs, self.base = fsspec.core.url_to_fs(url)
        self.base = self.base.rstrip("/")

    def _p(self, rel: str) -> str:
        return f"{self.base}/{rel}"

    def read_bytes(self, rel):
        with self.fs.open(self._p(rel), "rb") as f:
            return f.read()

    def write_bytes(self, rel, data):
        with self.fs.open(self._p(rel), "wb") as f:
            f.write(data)

    def exists(self, rel):
        return self.fs.exists(self._p(rel))

    def delete(self, rel):
        if self.fs.exists(self._p(rel)):
            self.fs.rm(self._p(rel))

    def list(self, rel):
        if not self.fs.exists(self._p(rel)):
            return []
        return sorted(p[len(self.base) + 1:] for p in self.fs.find(self._p(rel)))


def open_fs(root: str | Path):
    return FsspecFS(str(root)) if "://" in str(root) else LocalFS(root)


# ------------------------------------------------------------------ helpers
def doc_path(url: str) -> str:
    """Stable, readable relative path for a URL: host/path, '/'-terminated paths -> _index."""
    p = urlparse(url)
    path = p.path or "/"
    if path.endswith("/"):
        path += "_index"
    path = re.sub(r"[^A-Za-z0-9._/-]", "_", path).strip("/")
    host = re.sub(r"[^A-Za-z0-9.-]", "_", p.netloc)
    return f"{host}/{path}"


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:80]


# ------------------------------------------------------------------ store
class Store:
    def __init__(self, root: str | Path, source: str):
        self.fs = open_fs(root)
        self.source = source
        self.prefix = f"{source}/"

    def _rel(self, *parts: str) -> str:
        return self.prefix + "/".join(parts)

    # manifest
    def load_manifest(self) -> dict[str, Any]:
        rel = self._rel("manifest.json")
        if not self.fs.exists(rel):
            return {"version": 1, "source": self.source, "docs": {}, "sitemaps": {}}
        return json.loads(self.fs.read_bytes(rel))

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        self.fs.write_bytes(self._rel("manifest.json"),
                            json.dumps(manifest, indent=1, sort_keys=True).encode())

    # raw versions
    def write_raw(self, url: str, version: int, ts: str, chash: str, body: bytes,
                  meta: dict[str, Any]) -> str:
        stem = f"v{version:04d}_{ts}_{chash[:8]}"
        base = self._rel("raw", doc_path(url))
        self.fs.write_bytes(f"{base}/{stem}.html", body)
        self.fs.write_bytes(f"{base}/{stem}.json", json.dumps(meta, indent=1, sort_keys=True).encode())
        return f"raw/{doc_path(url)}/{stem}.html"

    def read(self, rel_in_source: str) -> bytes:
        return self.fs.read_bytes(self.prefix + rel_in_source)

    def write(self, rel_in_source: str, data: bytes) -> None:
        self.fs.write_bytes(self.prefix + rel_in_source, data)

    def delete(self, rel_in_source: str) -> None:
        self.fs.delete(self.prefix + rel_in_source)

    def exists(self, rel_in_source: str) -> bool:
        return self.fs.exists(self.prefix + rel_in_source)

    def list(self, rel_in_source: str) -> list[str]:
        return [p[len(self.prefix):] for p in self.fs.list(self.prefix + rel_in_source)]

    def raw_versions(self, url: str) -> list[str]:
        return [p for p in self.list(f"raw/{doc_path(url)}") if p.endswith(".html")]

    # sitemaps
    def save_sitemap(self, url: str, sha: str, body: bytes) -> str:
        rel = f"sitemaps/{slug(url)}/{sha[:8]}.xml"
        if not self.exists(rel):
            self.write(rel, body)
        return rel

    # events / runs
    def write_events(self, run_id: str, events: list[dict[str, Any]]) -> None:
        if events:
            data = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
            self.write(f"events/{run_id}.jsonl", data.encode())

    def write_run(self, run_id: str, summary: dict[str, Any]) -> None:
        self.write(f"runs/{run_id}.json", json.dumps(summary, indent=1, sort_keys=True).encode())

    def runs(self) -> list[dict[str, Any]]:
        return [json.loads(self.read(p)) for p in self.list("runs") if p.endswith(".json")]

    def iter_events(self, since_run: str | None = None) -> Iterator[dict[str, Any]]:
        for p in self.list("events"):
            run_id = p.rsplit("/", 1)[-1][:-6]
            if since_run and run_id <= since_run:
                continue
            for line in self.read(p).decode().splitlines():
                if line.strip():
                    yield json.loads(line)
