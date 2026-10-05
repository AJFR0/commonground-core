"""Optional: render pages to PDF for the ai_parse_document route.

`ai_parse_document` reads PDF, images, DOC/DOCX and PPT/PPTX, not HTML. If you want that
route (for its layout-aware parsing, figure descriptions, or parity with other PDF
pipelines), render each changed page's live URL to PDF and store it next to the raw HTML.

Most people should use the markdown route instead: `ai_prep_search` accepts markdown
directly, and the markdown keeps headings, code and tables exactly.

Requires: pip install "commonground-core[pdf]" && playwright install chromium
"""

from __future__ import annotations

from typing import Callable

from .store import Store


def render_pdfs(store: Store, since_run: str | None = None, limit: int | None = None,
                log: Callable[[str], None] = print) -> list[str]:  # pragma: no cover - needs a browser
    from playwright.sync_api import sync_playwright

    manifest = store.load_manifest()
    targets = []
    for ev in store.iter_events(since_run):
        if ev["event"] in ("added", "updated") and ev.get("raw_path"):
            targets.append((ev["url"], ev["raw_path"]))
    if since_run is None and not targets:
        targets = [(u, d["raw_path"]) for u, d in manifest["docs"].items()
                   if d.get("status") == "active" and d.get("raw_path")]
    seen, written = set(), []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for url, raw_rel in targets:
            if url in seen:
                continue
            seen.add(url)
            pdf_rel = raw_rel[:-5] + ".pdf"
            if store.exists(pdf_rel):
                continue
            page.goto(url, wait_until="networkidle")
            store.write(pdf_rel, page.pdf(format="Letter", print_background=True))
            written.append(pdf_rel)
            log(f"pdf {pdf_rel}")
            if limit and len(written) >= limit:
                break
        browser.close()
    return written
