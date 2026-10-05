-- Load docsync's per-page JSON (metadata + markdown) into a Delta table with Change Data Feed.
-- Re-run after each sync: MERGE only touches changed pages; removed pages are deleted.
-- Adjust the catalog/schema/volume names to yours.

CREATE TABLE IF NOT EXISTS commonground.corpus.docs (
  doc_id            STRING NOT NULL,
  url               STRING,
  title             STRING,
  h1                STRING,
  description       STRING,
  section           STRING,
  breadcrumbs       ARRAY<STRING>,
  headings          ARRAY<STRING>,
  page_last_updated STRING,
  version           INT,
  content_hash      STRING,
  fetched_at        STRING,
  word_count        INT,
  markdown          STRING,
  source            STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE OR REPLACE TEMP VIEW docs_incoming AS
SELECT doc_id, url, title, h1, description, section, breadcrumbs, headings, page_last_updated,
       CAST(version AS INT) AS version, content_hash, fetched_at, CAST(word_count AS INT) AS word_count,
       markdown, source
FROM read_files(
  '/Volumes/commonground/corpus/docsync/databricks-docs/prepared/docs-json/docs/',
  format => 'json',
  recursiveFileLookup => true,
  schemaHints => 'breadcrumbs ARRAY<STRING>, headings ARRAY<STRING>'
);

MERGE INTO commonground.corpus.docs AS t
USING docs_incoming AS s
ON t.doc_id = s.doc_id
WHEN MATCHED AND t.content_hash <> s.content_hash THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE THEN DELETE;
