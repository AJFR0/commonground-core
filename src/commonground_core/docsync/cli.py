"""cgdocs: command line for docsync.

  cgdocs sync     -c sources/databricks-docs.yaml [--root ./data] [--mode incremental|full|new-only]
                  [--limit N] [--only REGEX] [--dry-run]
  cgdocs status   -c ... [--sections]
  cgdocs changes  -c ... [--since RUN_ID] [--event updated,added,removed]
  cgdocs prepare  -c ... --profile markdown [--full] [--only REGEX]
  cgdocs export   -c ... --what prepared/markdown --to /Volumes/cat/schema/vol/docs
  cgdocs render-pdf -c ... [--since RUN_ID] [--limit N]

--root defaults to $CGDOCS_ROOT, then ./data. It can be a local folder, a Unity Catalog
volume path (/Volumes/...), or (with the `cloud` extra) an s3://, abfss:// or gs:// URL.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

from .config import load_config
from .export import export
from .fetch import HttpClient
from .prepare import prepare
from .store import Store
from .sync import MODES, sync


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", required=True, help="source YAML")
    p.add_argument("--root", default=os.environ.get("CGDOCS_ROOT", "./data"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cgdocs", description="Sync and prepare public documentation.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="discover, fetch and detect changes")
    _common(s)
    s.add_argument("--mode", choices=MODES, default="incremental")
    s.add_argument("--limit", type=int)
    s.add_argument("--only", help="regex; only URLs matching it")
    s.add_argument("--dry-run", action="store_true")

    st = sub.add_parser("status", help="what's stored and when it last ran")
    _common(st)
    st.add_argument("--sections", action="store_true", help="page counts by section/domain")

    ch = sub.add_parser("changes", help="list change events")
    _common(ch)
    ch.add_argument("--since", help="run id; events after it")
    ch.add_argument("--event", default="added,updated,removed", help="comma list; 'all' for every kind")

    pr = sub.add_parser("prepare", help="build markdown / json / chunks from stored raw HTML")
    _common(pr)
    pr.add_argument("--profile", required=True)
    pr.add_argument("--full", action="store_true", help="rebuild everything, not just changes")
    pr.add_argument("--only", help="regex; only URLs matching it")

    ex = sub.add_parser("export", help="copy prepared outputs or raw HTML elsewhere")
    _common(ex)
    ex.add_argument("--what", required=True, help="'raw' or 'prepared/<profile>'")
    ex.add_argument("--to", required=True)

    rp = sub.add_parser("render-pdf", help="optional: render changed pages to PDF (needs [pdf] extra)")
    _common(rp)
    rp.add_argument("--since")
    rp.add_argument("--limit", type=int)

    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    store = Store(a.root, cfg.name)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731

    if a.cmd == "sync":
        client = HttpClient(cfg.user_agent, cfg.fetch.delay_seconds, cfg.fetch.timeout_seconds,
                            cfg.fetch.max_retries)
        res = sync(cfg, store, client, mode=a.mode, only=a.only, limit=a.limit, dry_run=a.dry_run, log=log)
        print(json.dumps(res.summary(), indent=1))
        return 0

    if a.cmd == "status":
        m = store.load_manifest()
        docs = m.get("docs", {})
        by_status = collections.Counter(d.get("status") for d in docs.values())
        out = {"source": cfg.name, "root": a.root, "last_run": m.get("last_run"),
               "docs": dict(by_status), "sitemaps": m.get("sitemaps", {})}
        if a.sections:
            out["sections"] = dict(collections.Counter(
                (d.get("section") or "unmapped") for d in docs.values() if d.get("status") == "active"))
            out["top_paths"] = dict(collections.Counter(
                "/".join(d["url"].split("/")[3:6]) for d in docs.values()
                if d.get("status") == "active").most_common(40))
        print(json.dumps(out, indent=1))
        return 0

    if a.cmd == "changes":
        kinds = None if a.event == "all" else set(a.event.split(","))
        for ev in store.iter_events(a.since):
            if kinds is None or ev["event"] in kinds:
                print(json.dumps(ev))
        return 0

    if a.cmd == "prepare":
        only = re.compile(a.only).search if a.only else None
        res = prepare(cfg, store, a.profile, full=a.full, only=only, log=log)
        print(json.dumps({"profile": res.profile, "written": len(res.written), "unchanged": res.skipped,
                          "removed": len(res.removed), "failed": res.failed, "chunks": res.chunks}, indent=1))
        return 0

    if a.cmd == "export":
        res = export(store, a.what, a.to)
        print(json.dumps({"copied": len(res.copied), "deleted": len(res.deleted),
                          "unchanged": res.unchanged}, indent=1))
        return 0

    if a.cmd == "render-pdf":
        from .render_pdf import render_pdfs

        written = render_pdfs(store, a.since, a.limit, log=log)
        print(json.dumps({"pdfs": len(written)}))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
