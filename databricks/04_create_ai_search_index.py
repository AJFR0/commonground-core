# Databricks notebook source
# MAGIC %md
# MAGIC # AI Search index over the doc chunks
# MAGIC Databricks AI Search is the new name for Vector Search. A Delta Sync index with
# MAGIC Databricks-managed embeddings stays in sync with `doc_chunks` (Change Data Feed on).
# MAGIC SDK: `databricks-ai-search` (`databricks.ai_search.client.AISearchClient`).

# COMMAND ----------
# MAGIC %pip install -q databricks-ai-search

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
from databricks.ai_search.client import AISearchClient

ENDPOINT = "commonground"                                  # TODO: your AI Search endpoint
SOURCE = "commonground.corpus.doc_chunks"                  # from 03_chunk_with_ai_prep_search.sql
INDEX = "commonground.corpus.doc_chunks_index"
EMBED_MODEL = "databricks-gte-large-en"                    # TODO: an embedding endpoint you have

client = AISearchClient()
index = client.create_delta_sync_index(
    endpoint_name=ENDPOINT,
    index_name=INDEX,
    primary_key="chunk_id",
    source_table_name=SOURCE,
    pipeline_type="TRIGGERED",                             # re-sync after each docsync run
    embedding_source_column="chunk_to_embed",
    embedding_model_endpoint_name=EMBED_MODEL,
    columns_to_sync=["chunk_id", "doc_id", "url", "title", "section", "page_last_updated",
                     "chunk_to_retrieve"],
)
print(index)

# COMMAND ----------
# After later syncs: client.get_index(ENDPOINT, INDEX).sync()
