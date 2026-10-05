import json

import pytest

from commonground_core.glossary import (Glossary, Term, default_glossary, load_glossary, merge, parse_term,
                                        term_brief)

D = "https://docs.databricks.com/aws/en"


def g():
    return Glossary([
        Term("unity-catalog", "Unity Catalog", "The governance layer.", "We manage access in Unity Catalog.",
             aliases=["UC"], source_urls=[f"{D}/data-governance/unity-catalog/"]),
        Term("catalog", "catalog", "The first level of the namespace.", "Put it in the sales catalog.",
             source_urls=[f"{D}/catalogs/"]),
        Term("api", "API", "A way for software to ask other software for things.", "Call the API.", general=True),
    ])


def test_longest_match_and_first_mention_only():
    text = "Unity Catalog has a catalog. Unity Catalog again; another catalog."
    hits = [(text[a:b], tid) for a, b, tid in g().mentions(text)]
    assert hits == [("Unity Catalog", "unity-catalog"), ("catalog", "catalog")]   # not "Catalog" inside UC


def test_all_mentions_and_skip():
    text = "UC and unity catalog"
    assert [t for *_, t in g().mentions(text, first_only=False)] == ["unity-catalog", "unity-catalog"]
    assert g().mentions(text, skip={"unity-catalog"}) == []


def test_word_boundaries_code_and_acronym_case():
    text = "catalogs, sub-catalog, `catalog` in code, ```\ncatalog\n``` api.example.com, the API."
    hits = [(text[a:b], tid) for a, b, tid in g().mentions(text, first_only=False)]
    assert hits == [("API", "api")]


def test_annotate_html_escapes_and_wraps():
    out = g().annotate_html("Use <UC> & the API")
    assert out.startswith("Use &lt;<dfn class=\"cg-term\" data-term=\"unity-catalog\"")
    assert "&amp; the <dfn" in out and 'title="A way for software' in out


def test_annotate_custom_markup_for_markdown():
    out = g().annotate("UC is here", wrap=lambda s, t: f"[{s}](#term-{t.id})")
    assert out == "[UC](#term-unity-catalog) is here"
    assert [t.id for t in g().used_in("the API and UC")] == ["api", "unity-catalog"]


@pytest.mark.parametrize("term,problem", [
    (Term("Bad Id", "x", "p.", "x here", general=True), "lowercase slug"),
    (Term("t", "thing", "", "a thing", general=True), "plain definition is empty"),
    (Term("t", "thing", "One. Two. Three.", "a thing", general=True), "over 2 sentences"),
    (Term("t", "thing", "x" * 300, "a thing", general=True), "over 280 characters"),
    (Term("t", "thing", "p.", "", general=True), "say_it is empty"),
    (Term("t", "thing", "p.", "nothing relevant", general=True), "doesn't use the term"),
    (Term("t", "thing", "p.", "a thing"), "needs a public source"),
])
def test_entry_problems(term, problem):
    assert any(problem in p for p in term.problems())


def test_sentence_count_ignores_dotted_names():
    t = Term("t", "namespace", "Written as catalog.schema.table, e.g. sales.emea.orders. Version 3.5 too.",
             "Use the namespace.", general=True)
    assert t.problems() == []


def test_glossary_problems_duplicates_related_and_live_sources():
    terms = g().terms + [Term("uc2", "UC", "Dup.", "UC again.", general=True, related=["nope"])]
    probs = Glossary(terms).problems(live_urls={f"{D}/data-governance/unity-catalog"})
    assert any("also belongs to unity-catalog" in p for p in probs)
    assert any("related term 'nope'" in p for p in probs)
    assert any("catalog: source not in the current docs" in p for p in probs)
    assert not any(p.startswith("unity-catalog: source") for p in probs)   # trailing slash tolerated


def test_load_yaml_json_and_merge(tmp_path):
    y = tmp_path / "g.yaml"
    y.write_text("terms:\n  - id: api\n    term: API\n    plain: A way in.\n    say_it: Use the API.\n    general: true\n")
    j = tmp_path / "g.json"
    j.write_text(json.dumps([{"id": "api", "term": "API", "plain": "Better.", "say_it": "The API.", "general": True}]))
    merged = merge(load_glossary(y), load_glossary(j))
    assert merged.get("api").plain == "Better."
    (tmp_path / "bad.json").write_text(json.dumps([{"id": "x", "term": "x", "plain": "p", "say_it": "x", "oops": 1}]))
    with pytest.raises(ValueError, match="unknown glossary fields"):
        load_glossary(tmp_path / "bad.json")


def test_default_glossary_is_clean_and_cited():
    gl = default_glossary()
    assert gl.problems() == []
    assert len(gl.terms) >= 15
    for t in gl.terms:
        assert t.general or all(u.startswith("https://docs.databricks.com/") for u in t.source_urls)
    assert gl.get("ai-search") and "Vector Search" in gl.get("ai-search").aliases


def test_term_brief_and_parse():
    b = term_brief("metastore", "- fact one", audience="business")
    assert "metastore" in b and "fact one" in b and "customer_line" in b and "STRICT JSON" in b
    assert "customer_line" not in term_brief("metastore", "- f")
    d = parse_term('Sure: {"plain": " Top level. ", "say_it": "Start at the metastore."}')
    assert d == {"plain": "Top level.", "say_it": "Start at the metastore."}
