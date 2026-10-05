# docsync: design

docsync keeps a faithful copy of a public documentation site and tells you exactly what changed. How the pages are then prepared (markdown, JSON, chunks, PDF) is a separate, re-runnable choice.

## The pipeline

```
sitemap(s) ──► plan ──► fetch (polite, conditional) ──► detect change ──► store raw ──► events
                                                                              │
                                     prepare (any time, any profile) ◄────────┘
                                       ├─ markdown   (.md + front matter)
                                       ├─ json       (metadata + markdown → Delta)
                                       ├─ chunks     (built-in or your own chunker)
                                       └─ render-pdf (optional, for ai_parse_document)
```

## Why raw HTML

- **Exact.** It's what the server sent. Anything derived from it can be rebuilt without refetching.
- **Structured.** Headings, code blocks, tables, admonitions and links survive into markdown precisely. A PDF loses that structure and has to be parsed back.
- **Cheap and comparable.** Small files, and two versions can be diffed directly.
- **Databricks-compatible.** `ai_prep_search` (Beta) chunks plain text or markdown directly. `ai_parse_document` does not read HTML, which is why PDF rendering is an optional extra rather than the default.

## How change detection works

The Databricks docs sitemaps have no `<lastmod>`, so docsync can't trust the sitemap alone. It layers four signals, cheapest first:

| Signal | What it catches | Cost |
| --- | --- | --- |
| Sitemap diff | New pages (`added`), pages that left the sitemap (`removed`) | One request per sitemap |
| `<lastmod>` (when a site publishes it) | A page the site says changed | Free |
| HTTP validators: `If-None-Match` / `If-Modified-Since` | Unchanged pages answer `304` with no body | Tiny |
| Content hash of the main text | Real edits (`updated`) vs. cosmetic ones (`touched`) | One page download |

`touched` means the bytes changed but the main content didn't: a new script bundle, a reworded footer, or only the page's "Last updated on" date. Its metadata is refreshed but no new version is stored. `updated` stores a new raw version, so the full history of every page is kept.

The page's own "Last updated on" date is captured as `page_last_updated` on every record.

### Modes

- `incremental` (default): new pages, plus pages not checked in the last `recheck_after_hours`, plus anything with a newer `<lastmod>`. Stalest first.
- `full`: re-check every page (still conditional, so unchanged pages are 304s).
- `new-only`: just the pages that appeared in the sitemap. Useful when you only want to know if new pages showed up.

Add `--limit N` to spread a first sync over several runs, and `--only REGEX` to focus on a section.

## What's on disk

```
<root>/<source>/
  manifest.json                    latest state per URL
  raw/<host>/<path>/vNNNN_<ts>_<hash>.html   every content version
  raw/<host>/<path>/vNNNN_<ts>_<hash>.json   metadata for that version
  events/<run_id>.jsonl            added / updated / touched / removed / blocked / error
  runs/<run_id>.json               run summary
  sitemaps/<url>/<sha8>.xml        sitemap snapshots when they change
  prepared/<profile>/docs/...      prepare outputs
```

All writes are whole-file, never appends. So `<root>` can be a laptop folder, a Unity Catalog volume (`/Volumes/...` is an ordinary path on Databricks), or, with the `cloud` extra, an `s3://`, `abfss://` or `gs://` URL.

## Choosing how to process

| Route | When | How |
| --- | --- | --- |
| **A. `ai_prep_search`** (recommended on Databricks) | You want Databricks' current chunking practice, which keeps evolving | `prepare --profile docs-json` → `02_load_docs.sql` → `03_chunk_with_ai_prep_search.sql` → `04_create_ai_search_index.py` |
| **B. Built-in chunkers** | You want control, or you're not on Databricks | `prepare --profile chunks-heading` or `chunks-parent-child` → `route_b_builtin_chunks.sql` (or your own loader) |
| **C. PDF + `ai_parse_document`** | You specifically want layout-aware parsing | `render-pdf` → `route_c_pdf_ai_parse_document.sql` |
| **D. Your own** | Anything else | `prepare --profile markdown` and point any tool at the `.md` files, or `register_chunker("name", fn)` |

Re-run `prepare --full` after changing extraction or chunking settings. It rebuilds from stored raw HTML with no network calls.

### Built-in chunkers

| Name | Behavior |
| --- | --- |
| `heading` | Splits at h1–h3, keeps the heading path, packs paragraphs up to `max_tokens` with `overlap_tokens` carried over. Code fences are never split. |
| `parent_child` | 2048-token parents (by heading) and 512-token children that point at them, for small-to-big retrieval. |
| `fixed` | Paragraph packing with no regard for headings. |
| `none` | Whole page as one chunk. |

Every chunk record carries `text_to_embed` (page title + heading path + text) so retrieval keeps context. That follows the Databricks retrieval-quality guidance on enriching chunks with section headers.

## Robustness

- **Omitted end tags.** docs.databricks.com writes large tables with no `</td>`, `</tr>` or `</p>`, which is valid HTML. The parser infers them the way browsers do. Without that, one audit-log table nested 966 levels deep. A depth cap is the last-resort guard.
- **One bad page never stops a run.** If extraction fails, the raw HTML is still stored and the error is recorded on the page (`extract_error`), so a later `prepare` with a fixed parser picks it up. Any other per-page failure becomes an `error` event, and the run continues.
- **Interrupted first sync.** The manifest is checkpointed every 50 pages; re-running resumes.

## Where the copy lives

Keep the mirror **private**. It is a copy of someone else's copyrighted documentation, and anyone can already read the originals, so publishing it adds risk and egress cost without adding value. What's worth publishing, if anything, is derived data: the page index, section counts and the change feed.

| Where | How | Notes |
| --- | --- | --- |
| Laptop (default) | `--root ./data` (or `$CGDOCS_ROOT`) | Git-ignored. The working copy. |
| Private S3 backup | `aws s3 sync ./data s3://<bucket>/docsync --exclude "*/_export_state/*"` | Incremental by itself (only changed files upload). No extra Python dependencies. Bucket: Block Public Access on, default encryption. |
| Unity Catalog volume | Run docsync *on* Databricks with `--root /Volumes/<catalog>/<schema>/<volume>` (`databricks/01_run_docsync.py`) | Preferred for the work app: no dependency on a personal cloud account. |
| Volume, fed from a laptop | `cgdocs export --what prepared/docs-json --to /Volumes/...` | Copies only changed files and deletes vanished ones. |
| Object storage as the root | `--root s3://...` with the `cloud` extra plus the matching fsspec driver (`s3fs`, `adlfs`, `gcsfs`) | For running in a Lambda or Cloud Run job. |

## Running it elsewhere

- **Laptop:** `pip install -e .` then `cgdocs sync -c sources/databricks-docs.yaml`.
- **Databricks job:** `databricks/01_run_docsync.py`, with root on a volume.
- **Other clouds / functions:** the library has two dependencies (`requests`, `PyYAML`), and the HTTP transport is injectable. A Lambda or Cloud Run job can call `sync()` and `prepare()` with an `s3://` or `gs://` root.

## Politeness

docsync honors robots.txt, sends a descriptive User-Agent, waits `delay_seconds` between requests across all workers, backs off on 429/5xx (honoring `Retry-After`), and uses conditional requests so re-checks are nearly free for the server.

Python's robots parser doesn't support `*` wildcards inside rules, so wildcard rules are ignored. Use `exclude` patterns in the source config for those; the default config already excludes query strings.
