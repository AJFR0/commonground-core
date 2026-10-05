-- Route A (recommended): let Databricks chunk the markdown with ai_prep_search.
--
-- ai_prep_search (Beta) accepts plain text or markdown and returns semantic chunks with
-- chunk_to_embed (enriched with document context such as title and section headers) and
-- chunk_to_retrieve. Requires Databricks Runtime 18.2+ or serverless env v3+.
-- Docs: https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_prep_search
--
-- VERIFY on your workspace: the VARIANT paths below follow the documented output schema
-- (document.contents[] with chunk_id, chunk_position, chunk_to_retrieve, chunk_to_embed, metadata).
-- Beta functions change; check the doc page above if a path errors.

CREATE OR REPLACE TABLE commonground.corpus.doc_chunks
TBLPROPERTIES (delta.enableChangeDataFeed = true) AS
WITH prepped AS (
  SELECT d.doc_id, d.url, d.title, d.section, d.breadcrumbs, d.page_last_updated, d.version,
         ai_prep_search(d.markdown) AS result
  FROM commonground.corpus.docs AS d
)
SELECT
  concat(p.doc_id, '#', c.value:chunk_position::string)   AS chunk_id,
  p.doc_id, p.url, p.title, p.section, p.breadcrumbs, p.page_last_updated, p.version,
  c.value:chunk_position::int                            AS chunk_position,
  c.value:chunk_to_embed::string                         AS chunk_to_embed,
  c.value:chunk_to_retrieve::string                      AS chunk_to_retrieve,
  c.value:metadata                                       AS chunk_metadata
FROM prepped AS p,
LATERAL variant_explode(p.result:document.contents) AS c
WHERE p.result:error_status IS NULL;

-- Incremental variant: instead of CREATE OR REPLACE, read changed docs from the Change Data
-- Feed (table_changes('commonground.corpus.docs', <last_version>)), delete their old chunks
-- by doc_id, and insert the new ones.
