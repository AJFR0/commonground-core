"""Regressions from the first live sync (2026-10-05): docs.databricks.com tables omit end tags."""

from conftest import BASE, page_html

from commonground_core.docsync import Store, sync
import importlib

ex = importlib.import_module("commonground_core.docsync.extract")
from commonground_core.docsync.config import DEFAULT_CONTENT_SELECTORS as C, DEFAULT_STRIP_SELECTORS as S


def depth(node, d=0):
    kids = [c for c in node.children if isinstance(c, ex.Node)]
    return max([d] + [depth(k, d + 1) for k in kids])


# Shape copied from /aws/en/admin/account-settings/audit-logs: no </td>, </tr>, </p>, </tbody>.
ROW = ("<tr><td><p><code>{name}</code><td><p>Service principal is granted permissions.<td><ul>\n"
       "<li class=\"\"><code>securable_kind</code></li>\n<li class=\"\"><code>version</code></li>\n</ul>")
OMITTED_TABLE = ("<table><thead><tr><th>Action<th>Description<th>Request parameters<tbody>"
                 + "".join(ROW.format(name=f"action{i}") for i in range(400))
                 + "<tbody>" + ROW.format(name="next_section") + "</table>")


def test_omitted_end_tags_do_not_nest():
    root = ex.parse_html(f"<div class='theme-doc-markdown'>{OMITTED_TABLE}</div>")
    assert depth(root) < 20                      # was ~1200 before implied end tags
    table = ex.select_first(root, "table")
    rows = [n for n in table.iter() if n.tag == "tr"]
    assert len(rows) == 402
    assert all(len([c for c in r.children if isinstance(c, ex.Node) and c.tag in ("td", "th")]) == 3 for r in rows)


def test_omitted_end_tags_render_as_a_table():
    md = ex.extract(f"<div class='theme-doc-markdown'><h1>Audit</h1>{OMITTED_TABLE}</div>", "u", C, S).markdown
    lines = [line for line in md.splitlines() if line.startswith("|")]
    assert lines[0] == "| Action | Description | Request parameters |"
    assert lines[2].startswith("| `action0` | Service principal is granted permissions. |")
    assert len(lines) == 2 + 401


def test_implied_p_and_li_closing():
    root = ex.parse_html("<div><p>one<p>two<ul><li>a<li>b</ul><p>three<div>block</div></div>")
    div = root.children[0]
    assert [c.tag for c in div.children if isinstance(c, ex.Node)] == ["p", "p", "ul", "p", "div"]
    ul = ex.select_first(root, "ul")
    assert [c.tag for c in ul.children if isinstance(c, ex.Node)] == ["li", "li"]


def test_p_inside_td_is_scoped():
    root = ex.parse_html("<p>before<table><tr><td><p>cell<div>x</div></td></tr></table>")
    td = ex.select_first(root, "td")
    assert [c.tag for c in td.children if isinstance(c, ex.Node)] == ["p", "div"]


def test_pathological_nesting_is_capped():
    html = "<div class='theme-doc-markdown'>" + "<span>" * 5000 + "deep" + "</span>" * 5000 + "</div>"
    page = ex.extract(html, "u", C, S)              # must not raise RecursionError
    assert "deep" in page.text


def test_safe_extract_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("parser exploded")
    monkeypatch.setattr(ex, "extract", boom)
    page, err = ex.safe_extract("<html><title>T</title><body><p>Hello there</p></body></html>", "u", C, S)
    assert err.startswith("RuntimeError") and page.title == "T" and "Hello there" in page.text
    assert page.markdown == "" and page.content_hash


def test_one_bad_page_does_not_stop_the_run(cfg, tmp_path, client, site, monkeypatch):
    sy = importlib.import_module("commonground_core.docsync.sync")
    real = sy.safe_extract

    def flaky(html, url, *a):
        if url.endswith("/guides/a"):
            raise MemoryError("simulated")       # even non-parser failures are contained
        return real(html, url, *a)

    monkeypatch.setattr(sy, "safe_extract", flaky)
    res = sync(cfg, Store(tmp_path, cfg.name), client)
    assert res.counts.get("error") == 1 and res.counts.get("added") == 1
    m = Store(tmp_path, cfg.name).load_manifest()["docs"]
    assert m[BASE + "/guides/a"]["status"] == "error" and "MemoryError" in m[BASE + "/guides/a"]["error"]


def test_extract_error_is_recorded_and_raw_kept(cfg, tmp_path, client, site, monkeypatch):
    site.set("/guides/a", page_html("Alpha", "<p>ok</p>"))
    monkeypatch.setattr(ex, "extract", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad markup")))
    res = sync(cfg, Store(tmp_path, cfg.name), client)
    assert res.counts["added"] == 2
    store = Store(tmp_path, cfg.name)
    rec = store.load_manifest()["docs"][BASE + "/guides/a"]
    assert rec["extract_error"].startswith("ValueError") and store.read(rec["raw_path"])
