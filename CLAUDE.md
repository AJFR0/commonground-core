# CLAUDE.md — commonground-core

Shared library for the public Commonground site and the internal work app. Read `README.md`, then `docs/docsync.md` and `docs/anchor-playbook.md`.

## Rules

- **Public-safe only.** No internal URLs, names, data, or Databricks-internal knowledge. The work app depends on this repo, never the reverse.
- **Dependencies stay minimal** (`requests`, `PyYAML`). Anything heavier is an optional extra (`pdf`, `cloud`). The library must run on a laptop, in a Databricks job, and in a serverless function.
- **No model calls in core.** `teaching` takes a `judge_fn`; each app brings its own model.
- **Raw HTML is the source of truth.** Never make preparation depend on refetching; `prepare --full` must work offline.
- **Whole-file writes only** (no appends), so volumes and object storage work.
- `ILLUMINATION_FLOOR` / `ILLUMINATION_CUT` live only in `teaching.py`.

## Tests

`pytest` runs offline: `tests/conftest.py` has a fake website (robots, gzipped sitemap index, ETags/304s, 429s), and `tests/fixtures/page_ai_search.html` is a trimmed real docs.databricks.com page. Add a test with any change to detection, extraction, chunking or the gate.

Extraction must never take down a sync: keep `safe_extract` and the per-page guard in `sync.py`. `tests/test_robustness.py` reproduces the real omitted-end-tag tables.

If docs.databricks.com changes its markup, refresh the fixture from a live page and adjust `detect.content_selectors` / `strip_selectors` in `sources/databricks-docs.yaml` before touching code.
