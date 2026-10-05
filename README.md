# commonground-core

Shared, public-safe core for both Commonground apps:

- **`docsync`**: keeps a current copy of public documentation (Databricks docs by default), detects exactly what changed, and prepares it however you want: markdown, JSON for Delta, built-in chunks, or PDF for `ai_parse_document`.
- **`spine`**: the concept format both apps share.
- **`teaching`**: how to craft an analogy card (with a check question that makes the reader apply the idea, not recall it), how to judge whether it teaches or just renames, and the gate that decides what's shown.
- **`glossary`**: plain-English definitions behind every technical term, with the term used in a sentence; finds first mentions so apps can show them as tooltips. Ships a cited starter glossary of public Databricks terms.

Both apps depend on this repo; it depends on neither. It must never contain internal material: content and code flow from here to the work app, never back.

## Quick start (laptop)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest

# First sync: try 50 pages, then let it run
cgdocs sync -c sources/databricks-docs.yaml --root ./data --limit 50
cgdocs sync -c sources/databricks-docs.yaml --root ./data

cgdocs status   -c sources/databricks-docs.yaml --sections
cgdocs changes  -c sources/databricks-docs.yaml                 # what changed, by run
cgdocs prepare  -c sources/databricks-docs.yaml --profile markdown
cgdocs prepare  -c sources/databricks-docs.yaml --profile chunks-heading
```

A full first sync of the AWS docs (about 4,000 pages with the default API-reference exclusions) at the default politeness settings takes 30 to 60 minutes; later runs mostly get `304 Not Modified` and finish quickly. If it's interrupted, run it again: it resumes.

Keep the copy private; back it up with `aws s3 sync ./data s3://<private-bucket>/docsync`. See [Where the copy lives](docs/docsync.md#where-the-copy-lives).

## On Databricks

Point `--root` at a Unity Catalog volume and use the notebooks in `databricks/`:

1. `01_run_docsync.py`: sync into the volume and write per-page JSON (schedule as a daily job).
2. `02_load_docs.sql`: MERGE into a Delta table with Change Data Feed.
3. `03_chunk_with_ai_prep_search.sql`: let `ai_prep_search` (Beta) chunk the markdown.
4. `04_create_ai_search_index.py`: AI Search (formerly Vector Search) Delta Sync index.

There are alternatives for owning chunking (`route_b_builtin_chunks.sql`) or the PDF route (`route_c_pdf_ai_parse_document.sql`). See [`docs/docsync.md`](docs/docsync.md) for the design and how to choose.

## Use as a library

```python
from commonground_core.docsync import HttpClient, Store, load_config, prepare, sync
from commonground_core.teaching import illumination_gate, judge_brief, shown_analogies
from commonground_core.glossary import default_glossary

cfg = load_config("sources/databricks-docs.yaml")
store = Store("./data", cfg.name)
sync(cfg, store, HttpClient(cfg.user_agent))
prepare(cfg, store, "docs-json")

html = default_glossary().annotate_html("RAG grounds an LLM in your documents.")  # <dfn> on first mentions
```

Install from another repo: `pip install "commonground-core @ git+https://github.com/AJFR0/commonground-core.git"`.

## Layout

```
src/commonground_core/
  docsync/   config, sitemap, fetch, extract, store, sync, prepare, chunkers, export, render_pdf, cli
  spine.py   concept + analogy-card shapes, validation
  teaching.py craft/judge briefs, scenario-check lint, illumination gate, display rule
  glossary.py terms, first-mention matching, validation, drafting brief
  data/       glossary.yaml (shared public glossary)
sources/     one YAML per documentation source
databricks/  notebooks and SQL for the Databricks routes
docs/        docsync design, anchor playbook
tests/       offline tests (fake website + a real Docusaurus page fixture)
```

## License

Code: MIT (see `LICENSE`). `tests/fixtures/page_ai_search.html` is a trimmed excerpt of a public docs.databricks.com page, kept only to test the parser against real markup; it remains Databricks' content. docsync fetches documentation for your own use. Check a site's terms before redistributing what it fetches.
