"""The teaching contract: how to craft an analogy card, how to judge whether it teaches,
and the hard gate that decides what ships.

Ported from the public Commonground repo (pipeline/gen_cards.py) so both apps share one
definition of "a good analogy". Provider-neutral: no model calls here. Each app supplies a
`judge_fn(prompt) -> {"score": int, "reason": str}` backed by whatever model it uses.

Correctness (claims match the docs) and teaching (the anchor's *structure* gives an insight
a definition wouldn't) are separate gates. A card can be 100% correct and still teach
nothing, because it only renames the concept's parts in anchor vocabulary.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

ILLUMINATION_FLOOR = 4  # a concept's best anchor must reach this before an analogy is shown/shipped
ILLUMINATION_CUT = 2    # cards at or below this are cut candidates (hidden, flagged for review)

RUBRIC = {
    5: "Illuminating: true structural counterpart; reveals something non-obvious; clicks for a newcomer.",
    4: "Strong: real structural fit; genuinely aids understanding.",
    3: "Adequate / relabeling: tidy but mostly renames the parts; little insight beyond vocabulary.",
    2: "Weak: forced or generic mapping; adds little, or the fit is a stretch.",
    1: "No fit: obscures more than it clarifies.",
}


# The check question tests APPLYING the concept, not recalling it. A reader who can only repeat a
# definition hasn't learned it; one who can use it on a new situation has.
CHECK_RULES = {
    "learner": ("a short SCENARIO the reader hasn't seen, which they solve by APPLYING the concept "
                "(\"You need X; what would you use, and why?\", \"Y is happening; what's the likely cause?\"). "
                "Never a definition or recall question (\"What is ...?\", \"What does ... stand for?\")."),
    "business": ("a short CUSTOMER SCENARIO: a stakeholder says or wants something, and the reader decides how "
                 "this concept applies or what to ask next (\"A data leader says X. Does this help, and what "
                 "would you ask?\"). Never a definition or recall question."),
}


def craft_brief(concept: dict[str, Any], anchor_label: str, facts: str, audience: str = "learner") -> str:
    """Canonical instruction to craft ONE card. Both agent-crafted and API-generated cards follow it.

    audience: "learner" (how it works) or "business" (customer-facing scenarios for the check)."""
    if audience not in CHECK_RULES:
        raise ValueError(f"audience must be one of {sorted(CHECK_RULES)}")
    return f"""Teach this ONE concept through the anchor domain, as a Commonground analogy card.

CONCEPT: {concept['name']}
LITERAL DEFINITION (source of truth; never contradict): {concept['plain']}

GROUNDING FACTS (from the official documentation; assert nothing about the concept these
facts don't support):
{facts}

ANCHOR DOMAIN the learner already knows: "{anchor_label}"

ILLUMINATION REQUIREMENT (this is what makes a card good, not just correct): the bridge must TEACH
through SHARED STRUCTURE: map a specific part of the anchor onto a specific part of the concept so a
newcomer gains an insight a plain definition wouldn't. If the only thing you can do is rename the
concept's parts in anchor vocabulary ("the X is like the anchor's X"), the analogy has FAILED for
this pairing: either move the real teaching into where_it_breaks, or say the anchor doesn't fit. Aim
for a mapping that would score 4-5 on illumination, never settle for relabeling.

CHECK RULE: the check question is {CHECK_RULES[audience]} The answer gives the reasoning in one
sentence, using only the grounding facts.

Respond with STRICT JSON, exactly these keys:
{{
  "bridge": "2-3 sentences mapping the concept onto the anchor with concrete nouns; one-to-one.",
  "where_it_breaks": "1-2 sentences naming where the analogy stops being accurate.",
  "check": {{"q": "one short scenario question (see CHECK RULE)", "a": "the answer and why, in one sentence"}}
}}
Voice: a clever, encouraging tutor; plain words first, jargon second. Never invent capabilities."""


# Recall-style questions: short "What is X?" forms and definition phrasing.
_RECALL = [
    re.compile(r"^\s*(what|which)\s+(is|are|was|were)\s+(an?\s+|the\s+)?[\w .()/'-]{1,60}\?\s*$", re.I),
    re.compile(r"\bstands?\s+for\b", re.I),
    re.compile(r"\b(definition of|is defined as|best describes|best defines)\b", re.I),
    re.compile(r"^\s*(define|name)\b", re.I),
    re.compile(r"^\s*what\s+does\s+[\w .()/'-]{1,60}\s+(mean|do)\?\s*$", re.I),
    re.compile(r"\btrue or false\b", re.I),
]
# Words that signal a situation to reason about.
_SCENARIO = re.compile(r"\b(you|your|you're|customer|team|stakeholder|user|if|when|suppose|imagine|"
                       r"needs?|wants?|asks?|says?|should|would|why)\b", re.I)


def recall_smell(question: str) -> str | None:
    """Heuristic lint for a check question. Returns why it looks like recall, or None if it reads like a
    scenario. Advisory: a reviewer decides; it never blocks on its own."""
    q = (question or "").strip()
    if not q:
        return "empty question"
    for pat in _RECALL:
        if pat.search(q) and not (_SCENARIO.search(q) and len(q.split()) > 12):
            return "reads like definition recall; ask the reader to apply the concept to a situation"
    if len(q.split()) < 6 and not _SCENARIO.search(q):
        return "too short to describe a situation"
    return None


def check_style_problems(cards: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """Advisory lint over {concept_id: {anchor_id: card}}: check questions that test recall."""
    out = []
    for cid, by_anchor in cards.items():
        for aid, card in (by_anchor or {}).items():
            why = recall_smell((card.get("check") or {}).get("q", ""))
            if why:
                out.append(f"recall-style check in {cid} x {aid}: {why}")
    return out


def judge_brief(concept: dict[str, Any], anchor_label: str, card: dict[str, Any]) -> str:
    """Canonical instruction to JUDGE one card's illumination (1-5)."""
    rubric = "\n".join(f"{k} = {v}" for k, v in sorted(RUBRIC.items(), reverse=True))
    chk = card.get("check") or {}
    return f"""Score how much this analogy TEACHES the concept (its pedagogical lift), NOT whether
it is factually correct (correctness is verified separately; assume it holds).

CONCEPT: {concept['name']}: {concept.get('one_liner') or concept['plain']}
ANCHOR DOMAIN: "{anchor_label}"
CARD:
  bridge: {card.get('bridge', '')}
  where_it_breaks: {card.get('where_it_breaks', '')}
  check: {chk.get('q', '')} / {chk.get('a', '')}

The question: does the anchor's STRUCTURE give a learner an "aha" a plain definition wouldn't, or
does it just rename the concept's parts in anchor vocabulary?
{rubric}
Collapse smell: if every anchor for this concept would map the same generic way, it is renaming, not
teaching; cap at about 3.

Respond with STRICT JSON: {{"score": <1-5 integer>, "reason": "<=15 words"}}"""


def parse_judgement(text: str) -> dict[str, Any]:
    """Pull {"score", "reason"} out of a model reply; clamp the score to 1..5."""
    m = re.search(r"\{.*\}", text, re.S)
    d = json.loads(m.group(0) if m else text)
    score = int(d.get("score", 0))
    return {"score": max(1, min(5, score)), "reason": str(d.get("reason", "")).strip()}


def judge(cards: dict[str, dict[str, dict[str, Any]]], concepts: list[dict[str, Any]],
          anchors: dict[str, str], judge_fn: Callable[[str], dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Score every concept x anchor card that exists -> {concept_id: {anchor_id: score}}."""
    scores: dict[str, dict[str, int]] = {}
    for c in concepts:
        slot = cards.get(c["id"]) or {}
        scores[c["id"]] = {aid: int(judge_fn(judge_brief(c, label, slot[aid]))["score"])
                           for aid, label in anchors.items() if slot.get(aid)}
    return scores


def illumination_gate(scores: dict[str, dict[str, int]], floor: int = ILLUMINATION_FLOOR,
                      cut: int = ILLUMINATION_CUT) -> dict[str, Any]:
    """PURE hard gate. Passes iff EVERY concept has at least one anchor scoring >= floor.

    Returns {passed, failing: [concept_id], concepts: {cid: {best_anchor, best_score, passes, lead, cut}}}.
    `lead` is the concept's default anchor (its top scorer) when it passes, else None.
    """
    report, failing = {}, []
    for cid, by_anchor in scores.items():
        by_anchor = by_anchor or {}
        if by_anchor:
            best_anchor = max(by_anchor, key=lambda a: by_anchor[a])
            best_score = by_anchor[best_anchor]
        else:
            best_anchor, best_score = None, 0
        passes = best_score >= floor
        report[cid] = {"best_anchor": best_anchor, "best_score": best_score, "passes": passes,
                       "lead": best_anchor if passes else None,
                       "cut": sorted(a for a, s in by_anchor.items() if s <= cut)}
        if not passes:
            failing.append(cid)
    return {"passed": not failing, "failing": failing, "concepts": report}


def shown_analogies(scored: dict[str, int | None], floor: int = ILLUMINATION_FLOOR,
                    cut: int = ILLUMINATION_CUT) -> dict[str, Any]:
    """Per-concept display rule for a live app (the conservative one).

    scored: {anchor_id: score or None (not yet judged)}.
    - unscored analogies are never shown to readers (reviewers still see them)
    - analogies at or below `cut` are hidden and flagged
    - if no analogy reaches `floor`, readers get the plain explanation, no analogy at all
    """
    judged = {a: s for a, s in scored.items() if s is not None}
    best = max(judged.values(), default=0)
    show = sorted((a for a, s in judged.items() if s >= floor), key=lambda a: -judged[a]) if best >= floor else []
    return {
        "show": show,
        "lead": show[0] if show else None,
        "cut": sorted(a for a, s in judged.items() if s <= cut),
        "needs_scoring": sorted(a for a, s in scored.items() if s is None),
        "plain_only": not show,
    }
