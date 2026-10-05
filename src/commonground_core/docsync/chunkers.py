"""Built-in chunkers over page markdown. Pluggable: `register_chunker("name", fn)`.

Use these when you want to own chunking. On Databricks you can skip them entirely and let
`ai_prep_search` chunk the markdown (see databricks/03_chunk_with_ai_prep_search.sql).

Token counts are estimated as ceil(chars / 4), which is close enough for sizing chunks
against embedding-model limits without pulling in a tokenizer.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

CHUNKERS: dict[str, Callable[..., list["Chunk"]]] = {}


def register_chunker(name: str, fn: Callable[..., list["Chunk"]]) -> None:
    CHUNKERS[name] = fn


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


@dataclass
class Chunk:
    text: str
    heading_path: list[str] = field(default_factory=list)
    position: int = 0
    role: str = "chunk"           # chunk | parent | child
    parent_position: int | None = None

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    def to_record(self, doc: dict, title_prefix: bool = True) -> dict:
        """Self-contained record: embed text carries the page title + heading path (context)."""
        ctx = " > ".join([doc.get("title") or ""] + self.heading_path).strip(" >")
        cid = hashlib.sha1(f"{doc['doc_id']}|{self.role}|{self.position}".encode()).hexdigest()[:16]
        parent_id = None
        if self.parent_position is not None:
            parent_id = hashlib.sha1(f"{doc['doc_id']}|parent|{self.parent_position}".encode()).hexdigest()[:16]
        return {
            "chunk_id": cid, "doc_id": doc["doc_id"], "url": doc["url"], "title": doc.get("title"),
            "section": doc.get("section"), "breadcrumbs": doc.get("breadcrumbs") or [],
            "heading_path": self.heading_path, "position": self.position, "role": self.role,
            "parent_id": parent_id, "text": self.text,
            "text_to_embed": f"{ctx}\n\n{self.text}" if title_prefix and ctx else self.text,
            "tokens_est": self.tokens, "version": doc.get("version"),
            "content_hash": doc.get("content_hash"), "page_last_updated": doc.get("page_last_updated"),
        }


# ------------------------------------------------------------------ markdown blocks
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _blocks(md: str) -> list[tuple[str, str | int]]:
    """Split markdown into ('heading', level, text) and ('para', text) units; code fences stay whole."""
    out = []
    buf: list[str] = []
    in_code = False
    for line in md.splitlines():
        if line.startswith("```"):
            buf.append(line)
            in_code = not in_code
            if not in_code:
                out.append(("para", "\n".join(buf).strip()))
                buf = []
            continue
        if in_code:
            buf.append(line)
            continue
        m = _HEADING.match(line)
        if m:
            if buf and "\n".join(buf).strip():
                out.append(("para", "\n".join(buf).strip()))
            buf = []
            out.append(("heading", len(m.group(1)), m.group(2).strip()))
            continue
        if not line.strip():
            if buf and "\n".join(buf).strip():
                out.append(("para", "\n".join(buf).strip()))
            buf = []
            continue
        buf.append(line)
    if buf and "\n".join(buf).strip():
        out.append(("para", "\n".join(buf).strip()))
    return out


def _sections(md: str, max_level: int = 3) -> list[tuple[list[str], list[str]]]:
    """[(heading_path, [paragraphs])] split at headings of level <= max_level."""
    path: list[tuple[int, str]] = []
    sections: list[tuple[list[str], list[str]]] = [([], [])]
    for b in _blocks(md):
        if b[0] == "heading" and b[1] <= max_level:
            level, text = b[1], b[2]
            path = [p for p in path if p[0] < level] + [(level, text)]
            sections.append(([t for _, t in path], []))
        elif b[0] == "heading":
            sections[-1][1].append("#" * b[1] + " " + b[2])
        else:
            sections[-1][1].append(b[1])
    return [(hp, paras) for hp, paras in sections if paras]


def _pack(paras: list[str], max_tokens: int, overlap_tokens: int) -> list[str]:
    """Greedy-pack paragraphs up to max_tokens; carry trailing paragraphs as overlap."""
    chunks, cur = [], []
    for p in paras:
        if cur and estimate_tokens("\n\n".join(cur + [p])) > max_tokens:
            chunks.append("\n\n".join(cur))
            carry, size = [], 0
            for q in reversed(cur):
                if size + estimate_tokens(q) > overlap_tokens:
                    break
                carry.insert(0, q)
                size += estimate_tokens(q)
            cur = carry
        cur.append(p)
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# ------------------------------------------------------------------ chunkers
def chunk_none(md: str, **_) -> list[Chunk]:
    return [Chunk(md.strip(), [], 0)] if md.strip() else []


def chunk_heading(md: str, max_tokens: int = 512, overlap_tokens: int = 64, max_level: int = 3, **_) -> list[Chunk]:
    out: list[Chunk] = []
    for hp, paras in _sections(md, max_level):
        for text in _pack(paras, max_tokens, overlap_tokens):
            out.append(Chunk(text, hp, len(out)))
    return out


def chunk_fixed(md: str, max_tokens: int = 512, overlap_tokens: int = 64, **_) -> list[Chunk]:
    paras = [b[1] for b in _blocks(md) if b[0] == "para"]
    return [Chunk(t, [], i) for i, t in enumerate(_pack(paras, max_tokens, overlap_tokens))]


def chunk_parent_child(md: str, child_tokens: int = 512, parent_tokens: int = 2048, **_) -> list[Chunk]:
    out: list[Chunk] = []
    for p_i, parent in enumerate(chunk_heading(md, max_tokens=parent_tokens, overlap_tokens=0)):
        out.append(Chunk(parent.text, parent.heading_path, p_i, role="parent"))
        paras = parent.text.split("\n\n")
        for text in _pack(paras, child_tokens, 0):
            out.append(Chunk(text, parent.heading_path, len(out), role="child", parent_position=p_i))
    return out


register_chunker("none", chunk_none)
register_chunker("heading", chunk_heading)
register_chunker("fixed", chunk_fixed)
register_chunker("parent_child", chunk_parent_child)

__all__ = ["Chunk", "CHUNKERS", "register_chunker", "estimate_tokens", "asdict"]
