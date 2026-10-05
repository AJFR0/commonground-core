"""The concept spine shared by both apps, and the analogy-card shape.

A concept's spine is public-safe: id, name, plain definition, a one-liner, core claims each
with a public citation, and a structural "shape" tag that guides which analogies can work.
Each app layers its own view on top (How it works / Why it matters); see the work app's
docs/two-products.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The structural shape of a concept's core idea. Anchors that natively contain the same shape
# teach; anchors that don't just rename (docs/anchor-playbook.md).
SHAPES = ("hierarchy", "trade_off", "pipeline", "matching", "permission", "record", "lifecycle",
          "single_relationship")


@dataclass
class Claim:
    text: str
    source_urls: list[str]


@dataclass
class Concept:
    id: str
    name: str
    plain: str
    one_liner: str = ""
    claims: list[Claim] = field(default_factory=list)
    shape: str | None = None
    domain: str | None = None
    citations: list[str] = field(default_factory=list)  # source ids (pack-local) or URLs

    def problems(self) -> list[str]:
        out = []
        if not self.id or not self.name:
            out.append("id and name are required")
        if not self.plain.strip():
            out.append(f"{self.id}: plain definition is empty")
        if self.shape and self.shape not in SHAPES:
            out.append(f"{self.id}: unknown shape {self.shape!r}")
        for i, c in enumerate(self.claims):
            if not c.source_urls:
                out.append(f"{self.id}: claim {i} has no source")
        return out


@dataclass
class AnalogyCard:
    """One concept seen through one anchor domain."""

    anchor_id: str
    anchor_label: str
    bridge: str
    where_it_breaks: str
    check_q: str = ""
    check_a: str = ""
    mapping: list[dict[str, str]] = field(default_factory=list)  # [{concept, maps_to}], optional
    teaching_score: int | None = None
    teaching_reason: str | None = None

    def as_card(self) -> dict[str, Any]:
        return {"bridge": self.bridge, "where_it_breaks": self.where_it_breaks,
                "check": {"q": self.check_q, "a": self.check_a}}


def validate_cards(cards: dict[str, dict[str, dict[str, Any]]], concept_ids: list[str],
                   anchor_ids: list[str]) -> list[str]:
    """Completeness + shape over {concept_id: {anchor_id: {bridge, where_it_breaks, check}}}.
    Empty list = ok. (The correctness gate; the teaching gate is commonground_core.teaching.)"""
    problems = []
    for cid in concept_ids:
        for aid in anchor_ids:
            blk = (cards.get(cid) or {}).get(aid)
            if not blk:
                problems.append(f"missing {cid} x {aid}")
                continue
            for k in ("bridge", "where_it_breaks"):
                if not str(blk.get(k, "")).strip():
                    problems.append(f"empty {k} in {cid} x {aid}")
            chk = blk.get("check") or {}
            if not str(chk.get("q", "")).strip() or not str(chk.get("a", "")).strip():
                problems.append(f"incomplete check in {cid} x {aid}")
    return problems
