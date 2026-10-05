"""The shared glossary: plain-English definitions behind every technical term, shown on first use.

Learning a field is mostly learning its vocabulary. Each term carries:
- `plain`: what it is, in one or two plain sentences (a claim, so it cites a public source);
- `say_it`: the term used in a natural sentence, so the reader can *use* the word, not just
  recognize it;
- optional `customer_line`: how you'd say it to a business stakeholder.

Both apps render terms the same way: the first mention per section gets an underline and a
tooltip. This module finds those mentions and validates entries; rendering beyond the simple
`annotate_html` helper is up to each app. No model calls here (`term_brief` builds a prompt;
each app brings its own model).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MAX_PLAIN_CHARS = 280
MAX_PLAIN_SENTENCES = 2
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Term:
    id: str
    term: str
    plain: str
    say_it: str
    aliases: list[str] = field(default_factory=list)
    customer_line: str = ""
    source_urls: list[str] = field(default_factory=list)
    general: bool = False          # generic vocabulary (e.g. "API") that needs no product citation
    domain: str | None = None
    related: list[str] = field(default_factory=list)

    @property
    def forms(self) -> list[str]:
        """Every surface form this term matches, longest first."""
        return sorted({f.strip() for f in [self.term, *self.aliases] if f.strip()}, key=len, reverse=True)

    def problems(self, live_urls: set[str] | None = None) -> list[str]:
        out = []
        if not _SLUG.match(self.id or ""):
            out.append(f"{self.id!r}: id must be a lowercase slug")
        if not self.term.strip():
            out.append(f"{self.id}: term is empty")
        if not self.plain.strip():
            out.append(f"{self.id}: plain definition is empty")
        else:
            if len(self.plain) > MAX_PLAIN_CHARS:
                out.append(f"{self.id}: plain definition is over {MAX_PLAIN_CHARS} characters")
            if _sentences(self.plain) > MAX_PLAIN_SENTENCES:
                out.append(f"{self.id}: plain definition is over {MAX_PLAIN_SENTENCES} sentences")
        if not self.say_it.strip():
            out.append(f"{self.id}: say_it is empty (show the term used in a sentence)")
        elif not any(re.search(rf"(?i)(?<![\w-]){re.escape(f)}(?![\w-])", self.say_it) for f in self.forms):
            out.append(f"{self.id}: say_it doesn't use the term")
        if not self.general and not self.source_urls:
            out.append(f"{self.id}: needs a public source (or mark it general)")
        if live_urls is not None:
            live = {_norm(x) for x in live_urls}
            for u in self.source_urls:
                if _norm(u) not in live:
                    out.append(f"{self.id}: source not in the current docs: {u}")
        return out


@dataclass
class Glossary:
    terms: list[Term]

    def __post_init__(self) -> None:
        self.by_id = {t.id: t for t in self.terms}
        self._pattern: re.Pattern[str] | None = None
        self._form_to_id: dict[str, str] = {}
        self._exact: dict[str, str] = {}  # short all-caps acronyms match only in that case ("API", not "api.")

    def get(self, term_id: str) -> Term | None:
        return self.by_id.get(term_id)

    def problems(self, live_urls: set[str] | None = None) -> list[str]:
        """Entry problems plus duplicates and dangling `related` links. Empty list = ok."""
        live = {_norm(u) for u in live_urls} if live_urls is not None else None
        out: list[str] = []
        seen_ids: set[str] = set()
        seen_forms: dict[str, str] = {}
        for t in self.terms:
            if t.id in seen_ids:
                out.append(f"{t.id}: duplicate id")
            seen_ids.add(t.id)
            out.extend(t.problems(live))
            for f in t.forms:
                k = f.lower()
                if k in seen_forms and seen_forms[k] != t.id:
                    out.append(f"{t.id}: form {f!r} also belongs to {seen_forms[k]}")
                seen_forms.setdefault(k, t.id)
            for r in t.related:
                if r not in self.by_id:
                    out.append(f"{t.id}: related term {r!r} is not in the glossary")
        return out

    # ---------------------------------------------------------------- matching
    def _compile(self) -> re.Pattern[str]:
        if self._pattern is None:
            forms: list[tuple[str, str]] = []
            for t in self.terms:
                for f in t.forms:
                    forms.append((f, t.id))
                    self._form_to_id[f.lower()] = t.id
                    if f.isupper() and len(f) <= 5:
                        self._exact[f.lower()] = f
            forms.sort(key=lambda x: len(x[0]), reverse=True)  # longest match wins
            alt = "|".join(re.escape(f) for f, _ in forms) or r"(?!x)x"
            self._pattern = re.compile(rf"(?<![\w-])(?:{alt})(?![\w-])", re.I)
        return self._pattern

    def mentions(self, text: str, first_only: bool = True, skip: set[str] | None = None) -> list[tuple[int, int, str]]:
        """(start, end, term_id) for term mentions in `text`, in order.

        Matching is case-insensitive on word boundaries, longest form first, and ignores markdown
        code (fenced blocks and `inline`). With first_only, each term is returned once (its first
        mention). `skip` = term ids already explained earlier on the page."""
        pat = self._compile()
        masked = _code_spans(text)
        done = set(skip or ())
        out = []
        for m in pat.finditer(text):
            if any(a <= m.start() < b for a, b in masked):
                continue
            low = m.group(0).lower()
            if low in self._exact and m.group(0) != self._exact[low]:
                continue
            tid = self._form_to_id[low]
            if first_only and tid in done:
                continue
            done.add(tid)
            out.append((m.start(), m.end(), tid))
        return out

    def annotate(self, text: str, wrap: Callable[[str, Term], str], escape: Callable[[str], str] = lambda s: s,
                 first_only: bool = True, skip: set[str] | None = None) -> str:
        """Rebuild `text` with each mention replaced by wrap(matched_text, term); other text goes
        through `escape`. Use this for markdown or any custom markup."""
        out, pos = [], 0
        for a, b, tid in self.mentions(text, first_only, skip):
            out.append(escape(text[pos:a]))
            out.append(wrap(text[a:b], self.by_id[tid]))
            pos = b
        out.append(escape(text[pos:]))
        return "".join(out)

    def annotate_html(self, text: str, first_only: bool = True, skip: set[str] | None = None) -> str:
        """Plain text -> escaped HTML with <dfn class="cg-term"> on first mentions. The title attribute
        gives a native tooltip with no JavaScript; apps can enhance it from data-term."""
        def wrap(s: str, t: Term) -> str:
            return (f'<dfn class="cg-term" data-term="{html.escape(t.id, True)}" '
                    f'title="{html.escape(t.plain, True)}">{html.escape(s)}</dfn>')
        return self.annotate(text, wrap, html.escape, first_only, skip)

    def used_in(self, text: str) -> list[Term]:
        """Distinct terms mentioned in `text`, in first-mention order (e.g. for a 'Terms' sidebar)."""
        return [self.by_id[tid] for _, _, tid in self.mentions(text)]

    def to_records(self) -> list[dict[str, Any]]:
        return [t.__dict__.copy() for t in self.terms]


# ------------------------------------------------------------------ loading
def term_from_dict(d: dict[str, Any]) -> Term:
    keys = Term.__dataclass_fields__.keys()
    unknown = set(d) - set(keys)
    if unknown:
        raise ValueError(f"{d.get('id')}: unknown glossary fields {sorted(unknown)}")
    return Term(**{k: d[k] for k in keys if k in d})


def load_glossary(path: str | Path) -> Glossary:
    """Load a glossary from YAML or JSON: a list of terms, or {"terms": [...]}."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml  # core dependency; imported lazily so this module stays stdlib-only at import
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    items = data.get("terms", []) if isinstance(data, dict) else data
    return Glossary([term_from_dict(d) for d in items or []])


def default_glossary() -> Glossary:
    """The shared, public Databricks glossary that ships with this package (data/glossary.yaml)."""
    return load_glossary(Path(__file__).with_name("data") / "glossary.yaml")


def merge(*glossaries: Glossary) -> Glossary:
    """Later glossaries override earlier ones by id (e.g. an app's additions over the shared base)."""
    by_id: dict[str, Term] = {}
    for g in glossaries:
        for t in g.terms:
            by_id[t.id] = t
    return Glossary(list(by_id.values()))


# ------------------------------------------------------------------ drafting
def term_brief(term: str, facts: str, audience: str = "learner") -> str:
    """Canonical instruction to draft ONE glossary entry from grounding facts."""
    who = ("a newcomer to the field" if audience == "learner"
           else "an account executive who will use the word with business stakeholders")
    extra = (',\n  "customer_line": "one sentence using the term the way you would with a business stakeholder"'
             if audience == "business" else "")
    return f"""Write a glossary entry for the term "{term}" for {who}.

GROUNDING FACTS (from the official documentation; say nothing these facts don't support):
{facts}

Rules: plain words, no jargon you don't also explain, at most {MAX_PLAIN_SENTENCES} sentences and
{MAX_PLAIN_CHARS} characters for the definition. The point is that the reader can USE the word
afterwards, so show it in a natural sentence.

Respond with STRICT JSON, exactly these keys:
{{
  "plain": "what it is, in 1-2 plain sentences",
  "say_it": "one natural sentence that uses the term correctly"{extra}
}}"""


def parse_term(text: str) -> dict[str, str]:
    """Pull the drafted fields out of a model reply."""
    m = re.search(r"\{.*\}", text, re.S)
    d = json.loads(m.group(0) if m else text)
    return {k: str(d.get(k, "")).strip() for k in ("plain", "say_it", "customer_line") if k in d}


# ------------------------------------------------------------------ helpers
def _sentences(s: str) -> int:
    # ignore dots in abbreviations and numbers (e.g., i.e., 3.5, catalog.schema.table)
    t = re.sub(r"\b(e\.g|i\.e|etc|vs)\.", "", s)
    t = re.sub(r"(?<=\w)\.(?=\w)", "", t)
    return max(1, len(re.findall(r"[.!?](?:\s|$)", t.strip())))


def _code_spans(text: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in re.finditer(r"```.*?(?:```|\Z)", text, re.S)]
    for m in re.finditer(r"`[^`\n]+`", text):
        if not any(a <= m.start() < b for a, b in spans):
            spans.append((m.start(), m.end()))
    return spans


def _norm(u: str) -> str:
    return u.rstrip("/")
