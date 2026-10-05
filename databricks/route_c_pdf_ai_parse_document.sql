-- Route C: PDFs + ai_parse_document. Only if you specifically want layout-aware parsing.
-- ai_parse_document reads PDF, JPG/JPEG, PNG, TIFF, DOC/DOCX, PPT/PPTX; not HTML.
-- 1) `pip install "commonground-core[pdf]" && playwright install chromium`
-- 2) `cgdocs render-pdf -c sources/databricks-docs.yaml --root /Volumes/commonground/corpus/docsync`
--    (writes a .pdf next to each changed page's raw .html)
-- 3) Parse and chunk on Databricks (DBR 17.3+ for ai_parse_document, 18.2+ for ai_prep_search):

CREATE OR REPLACE TABLE commonground.corpus.doc_chunks_pdf AS
WITH parsed AS (
  SELECT path, ai_parse_document(content) AS parsed
  FROM read_files('/Volumes/commonground/corpus/docsync/databricks-docs/raw/',
                  format => 'binaryFile', recursiveFileLookup => true, pathGlobFilter => '*.pdf')
)
SELECT path, ai_prep_search(parsed) AS result
FROM parsed;
