"""docsync: keep a local (or volume, or bucket) copy of public documentation current.

    from commonground_core.docsync import load_config, Store, HttpClient, sync, prepare

    cfg = load_config("sources/databricks-docs.yaml")
    store = Store("./data", cfg.name)
    sync(cfg, store, HttpClient(cfg.user_agent))
    prepare(cfg, store, "markdown")
"""

from .config import SourceConfig, load_config, config_from_dict
from .export import export
from .extract import Page, extract
from .fetch import HttpClient, Response
from .prepare import prepare
from .store import Store, doc_path
from .sync import sync, plan
from .chunkers import CHUNKERS, register_chunker

__all__ = ["SourceConfig", "load_config", "config_from_dict", "export", "Page", "extract",
           "HttpClient", "Response", "prepare", "Store", "doc_path", "sync", "plan",
           "CHUNKERS", "register_chunker"]
