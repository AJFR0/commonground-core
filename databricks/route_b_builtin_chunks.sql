-- Route B: you own chunking. Run `cgdocs prepare --profile chunks-heading` (or chunks-parent-child,
-- or a chunker you registered), then load the per-page JSONL into a table for AI Search.
-- Each record has chunk_id, doc_id, url, title, heading_path, role, parent_id, text,
-- text_to_embed (title + heading path + text), tokens_est, version, content_hash.

CREATE OR REPLACE TABLE commonground.corpus.doc_chunks_builtin
TBLPROPERTIES (delta.enableChangeDataFeed = true) AS
SELECT *
FROM read_files(
  '/Volumes/commonground/corpus/docsync/databricks-docs/prepared/chunks-heading/docs/',
  format => 'json',
  recursiveFileLookup => true
);

-- Parent-child (small-to-big): index only role = 'child' rows; at query time fetch the parent
-- by parent_id for more context.
