from commonground_core.spine import Claim, Concept, validate_cards
from commonground_core.teaching import (
    craft_brief, illumination_gate, judge, judge_brief, parse_judgement, shown_analogies,
)

C = {"id": "namespace", "name": "Three-level namespace", "plain": "catalog.schema.table addressing",
     "one_liner": "Every asset has a three-part address."}
CARD = {"bridge": "Department, patient, chart.", "where_it_breaks": "A patient is a person.",
        "check": {"q": "What are the levels?", "a": "catalog, schema, table"}}


def test_craft_brief_has_illumination_clause():
    b = craft_brief(C, "Hospital records", "- fact one")
    assert "SHARED STRUCTURE" in b and "Hospital records" in b and "- fact one" in b


def test_judge_brief_has_rubric_and_card():
    b = judge_brief(C, "Hospital records", CARD)
    assert "5 = Illuminating" in b and "1 = No fit" in b and "Department, patient, chart." in b


def test_parse_judgement_clamps():
    assert parse_judgement('ok {"score": 9, "reason": "x"}') == {"score": 5, "reason": "x"}
    assert parse_judgement('{"score": 0}')["score"] == 1


def test_gate_pass_fail_and_cut():
    g = illumination_gate({"a": {"cooking": 5, "gardening": 2}, "b": {"hockey": 3}, "c": {}})
    assert not g["passed"] and g["failing"] == ["b", "c"]
    assert g["concepts"]["a"] == {"best_anchor": "cooking", "best_score": 5, "passes": True,
                                  "lead": "cooking", "cut": ["gardening"]}
    assert g["concepts"]["b"]["lead"] is None
    assert illumination_gate({"a": {"x": 4}})["passed"]


def test_judge_uses_supplied_fn():
    seen = []
    scores = judge({"namespace": {"hospital": CARD}}, [C], {"hospital": "Hospital", "bank": "Bank"},
                   lambda prompt: seen.append(prompt) or {"score": 4})
    assert scores == {"namespace": {"hospital": 4}} and len(seen) == 1


def test_shown_analogies_is_conservative():
    r = shown_analogies({"hospital": 5, "library": 4, "bank": 3, "gardening": 2, "new": None})
    assert r == {"show": ["hospital", "library"], "lead": "hospital", "cut": ["gardening"],
                 "needs_scoring": ["new"], "plain_only": False}
    r = shown_analogies({"hockey": 3, "f1": None})
    assert r["show"] == [] and r["plain_only"] and r["needs_scoring"] == ["f1"]


def test_validate_cards_and_concept_problems():
    probs = validate_cards({"namespace": {"hospital": CARD, "bank": {"bridge": "x"}}}, ["namespace"],
                           ["hospital", "bank", "library"])
    assert "missing namespace x library" in probs and "empty where_it_breaks in namespace x bank" in probs
    c = Concept("x", "X", "def", claims=[Claim("t", [])], shape="blob")
    assert len(c.problems()) == 2
