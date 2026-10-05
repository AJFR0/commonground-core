# Databricks notebook source
# MAGIC %md
# MAGIC # docsync on Databricks
# MAGIC Keeps a copy of the public docs in a Unity Catalog volume, then writes one JSON file per
# MAGIC page (metadata + markdown) for the Delta load in `02_load_docs.sql`.
# MAGIC
# MAGIC Schedule this as a Job (daily is plenty; unchanged pages cost one 304 each).
# MAGIC Prereqs: a volume, and egress to docs.databricks.com from the compute.
# MAGIC
# MAGIC TODO: pin the install to a tag once the repo has one. A private repo needs a token in the
# MAGIC URL or a Git credential on the workspace; a public repo needs neither.

# COMMAND ----------
# MAGIC %pip install -q "git+https://github.com/AJFR0/commonground-core.git"

# COMMAND ----------
dbutils.library.restartPython()

# COMMAND ----------
dbutils.widgets.text("volume_root", "/Volumes/commonground/corpus/docsync")
dbutils.widgets.dropdown("mode", "incremental", ["incremental", "full", "new-only"])
dbutils.widgets.text("limit", "")

import json
from commonground_core.docsync import HttpClient, Store, load_config, prepare, sync

ROOT = dbutils.widgets.get("volume_root")
# The config ships in the repo under sources/; copy yours into the volume to customize it.
CONFIG = f"{ROOT}/config/databricks-docs.yaml"

cfg = load_config(CONFIG)
store = Store(ROOT, cfg.name)
limit = int(dbutils.widgets.get("limit")) if dbutils.widgets.get("limit") else None

# COMMAND ----------
res = sync(cfg, store, HttpClient(cfg.user_agent, cfg.fetch.delay_seconds), mode=dbutils.widgets.get("mode"),
           limit=limit, log=print)
print(json.dumps(res.summary(), indent=1))

# COMMAND ----------
# One JSON per page under <ROOT>/databricks-docs/prepared/docs-json/docs/...
p = prepare(cfg, store, "docs-json", log=print)
print(f"wrote {len(p.written)} changed pages; {p.skipped} unchanged; {len(p.removed)} removed")
