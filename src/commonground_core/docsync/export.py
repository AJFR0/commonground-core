"""Copy prepared outputs (or raw HTML) to another location, e.g. a Unity Catalog volume.

Only files whose bytes changed since the last export are copied, and files whose source
disappeared are deleted at the destination, so a scheduled export is cheap.

If you run docsync directly with `--root /Volumes/<catalog>/<schema>/<volume>` you don't
need this at all; it's for syncing from a laptop to a volume (via the Databricks CLI-mounted
path or fsspec) or to any other store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .store import Store, open_fs


@dataclass
class ExportResult:
    copied: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0


def export(store: Store, what: str, dest: str, state_name: str | None = None) -> ExportResult:
    """what: 'prepared/<profile>' or 'raw'. dest: local path, /Volumes/..., or fsspec URL."""
    if not (what == "raw" or what.startswith("prepared/")):
        raise ValueError("what must be 'raw' or 'prepared/<profile>'")
    out = open_fs(dest)
    state_rel = f"_export_state/{state_name or what.replace('/', '_')}.json"
    state: dict[str, str] = json.loads(store.read(state_rel)) if store.exists(state_rel) else {}
    res = ExportResult()
    current: dict[str, str] = {}
    for rel in store.list(what):
        if rel.rsplit("/", 1)[-1].startswith("_"):
            continue
        data = store.read(rel)
        h = hashlib.sha256(data).hexdigest()
        target = rel[len(what) + 1:]
        current[target] = h
        if state.get(target) == h and out.exists(target):
            res.unchanged += 1
            continue
        out.write_bytes(target, data)
        res.copied.append(target)
    for target in set(state) - set(current):
        out.delete(target)
        res.deleted.append(target)
    store.write(state_rel, json.dumps(current, indent=0, sort_keys=True).encode())
    return res
