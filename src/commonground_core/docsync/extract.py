"""HTML -> structured page: metadata, main-content markdown, and a stable content hash.

Dependency-free (stdlib html.parser) so it runs anywhere: a laptop, a Databricks job,
a Lambda. Tuned for Docusaurus (docs.databricks.com) but driven by configurable selectors.

The content hash covers only the normalized main-content text, so nav, footer, script
bundles and the "Last updated" stamp never trigger a false "changed".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ZW = re.compile("[​‌‍﻿]")


# --------------------------------------------------------------------------- tiny DOM
@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node | str"] = field(default_factory=list)
    parent: "Node | None" = None

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return "".join(out)


# HTML lets authors omit many end tags (</p>, </li>, </td>, </tr>, ...); browsers infer them.
# Docusaurus emits tables like <tbody><tr><td><p>a<td><p>b<tr>... with every end tag omitted.
# Without inference each cell nests inside the previous one (966 levels on one real page).
# starting tag -> (open tags it implicitly closes, tags that bound the search)
IMPLIED_END = {
    "li": ({"li"}, {"ul", "ol", "menu"}),
    "dt": ({"dt", "dd"}, {"dl"}),
    "dd": ({"dt", "dd"}, {"dl"}),
    "tr": ({"tr"}, {"table", "thead", "tbody", "tfoot"}),
    "td": ({"td", "th"}, {"tr", "table"}),
    "th": ({"td", "th"}, {"tr", "table"}),
    "thead": ({"thead", "tbody", "tfoot"}, {"table"}),
    "tbody": ({"thead", "tbody", "tfoot"}, {"table"}),
    "tfoot": ({"thead", "tbody", "tfoot"}, {"table"}),
    "option": ({"option"}, {"select", "datalist", "optgroup"}),
}
# Block-level start tags close an open <p> (within "button scope", per the HTML spec).
P_CLOSERS = {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
             "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
             "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section", "table", "ul"}
P_SCOPE = {"td", "th", "table", "caption", "button", "html", "template"}
MAX_DEPTH = 120  # real docs pages peak near 30 (287-page sample); last-resort guard: deeper elements are attached flat instead of nested


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]

    def _close_implied(self, closes: set[str], bounds: set[str]) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            t = self.stack[i].tag
            if t in closes:
                del self.stack[i:]
                return
            if t in bounds:
                return

    def handle_starttag(self, tag, attrs):
        if tag in P_CLOSERS:
            self._close_implied({"p"}, P_SCOPE)
        if tag in IMPLIED_END:
            self._close_implied(*IMPLIED_END[tag])
        node = Node(tag, {k: (v or "") for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID and len(self.stack) < MAX_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):  # tolerate unclosed children
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root


_SEL = re.compile(r"^(?P<tag>[a-zA-Z][a-zA-Z0-9-]*)?(?P<rest>(?:[.#][A-Za-z0-9_-]+)*)$")


def matches(node: Node, selector: str) -> bool:
    """Simple selectors only: tag, .cls, #id, tag.cls.cls2, tag#id, [attr=value]."""
    selector = selector.strip()
    m_attr = re.fullmatch(r"(?P<tag>[a-zA-Z0-9-]*)\[(?P<k>[\w:-]+)=\"?(?P<v>[^\"\]]*)\"?\]", selector)
    if m_attr:
        return (not m_attr["tag"] or node.tag == m_attr["tag"]) and node.attrs.get(m_attr["k"]) == m_attr["v"]
    m = _SEL.match(selector)
    if not m:
        raise ValueError(f"unsupported selector: {selector!r}")
    if m["tag"] and node.tag != m["tag"].lower():
        return False
    for part in re.findall(r"[.#][A-Za-z0-9_-]+", m["rest"] or ""):
        if part[0] == "." and part[1:] not in node.classes:
            return False
        if part[0] == "#" and node.attrs.get("id") != part[1:]:
            return False
    return True


def select_first(root: Node, selector: str) -> Node | None:
    return next((n for n in root.iter() if n is not root and matches(n, selector)), None)


def select_all(root: Node, selector: str) -> list[Node]:
    return [n for n in root.iter() if n is not root and matches(n, selector)]


def strip(root: Node, selectors: list[str]) -> None:
    for n in list(root.iter()):
        if n is not root and n.parent is not None and any(matches(n, s) for s in selectors):
            n.parent.children = [c for c in n.parent.children if c is not n]


# --------------------------------------------------------------------------- markdown
class _Md:
    def __init__(self, base_url: str):
        self.base = base_url

    def convert(self, node: Node) -> str:
        md = self.block(node, depth=0)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip() + "\n"

    # inline content: collapse whitespace, render emphasis/code/links
    def inline(self, node: Node | str) -> str:
        if isinstance(node, str):
            return re.sub(r"\s+", " ", ZW.sub("", node))
        t = node.tag
        inner = "".join(self.inline(c) for c in node.children)
        if t in ("strong", "b"):
            return f"**{inner.strip()}**" if inner.strip() else ""
        if t in ("em", "i"):
            return f"*{inner.strip()}*" if inner.strip() else ""
        if t == "code":
            txt = ZW.sub("", node.text())
            return f"`{txt}`" if txt else ""
        if t == "br":
            return "  \n"
        if t == "a":
            href = node.attrs.get("href", "")
            label = inner.strip()
            if not label:
                return ""
            if not href or href.startswith("#") or href.startswith("javascript:"):
                return label
            return f"[{label}]({urljoin(self.base, href)})"
        if t == "img":
            return ""
        return inner

    def block(self, node: Node, depth: int) -> str:
        out: list[str] = []
        buf: list[str] = []

        def flush():
            text = "".join(buf).strip()
            if text:
                out.append(text + "\n\n")
            buf.clear()

        for c in node.children:
            if isinstance(c, str) or c.tag not in BLOCK_TAGS:
                buf.append(self.inline(c))
                continue
            flush()
            out.append(self.render_block(c, depth))
        flush()
        return "".join(out)

    def render_block(self, n: Node, depth: int) -> str:
        t = n.tag
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = "".join(self.inline(c) for c in n.children).strip()
            return f"{'#' * int(t[1])} {text}\n\n" if text else ""
        if t == "p":
            text = "".join(self.inline(c) for c in n.children).strip()
            return text + "\n\n" if text else ""
        if t == "pre":
            lang = ""
            for cand in [n] + [x for x in n.iter() if x.tag == "code"]:
                m = re.search(r"language-([\w+-]+)", cand.attrs.get("class", ""))
                if m:
                    lang = m.group(1)
                    break
            code = _pre_text(n).strip("\n")
            return f"```{lang}\n{code}\n```\n\n"
        if t in ("ul", "ol"):
            return self.render_list(n, depth) + "\n"
        if t == "table":
            return self.render_table(n)
        if t == "blockquote":
            inner = self.block(n, depth).strip()
            return "\n".join("> " + line if line else ">" for line in inner.splitlines()) + "\n\n"
        if t == "hr":
            return "---\n\n"
        if t in ("div", "section", "aside") and "theme-admonition" in n.classes:
            kind = next((c.split("theme-admonition-")[1] for c in n.classes
                         if c.startswith("theme-admonition-")), "note")
            content = _class_prefixed(n, "admonitionContent") or _last_div(n) or n
            inner = self.block(content, depth).strip()
            lines = inner.splitlines() or [""]
            lines[0] = f"**{kind.capitalize()}:** {lines[0]}"
            return "\n".join("> " + ln if ln else ">" for ln in lines) + "\n\n"
        return self.block(n, depth)  # generic container

    def render_list(self, n: Node, depth: int) -> str:
        lines = []
        for i, li in enumerate([c for c in n.children if isinstance(c, Node) and c.tag == "li"], 1):
            bullet = f"{i}." if n.tag == "ol" else "-"
            body = self.block(li, depth + 1).strip()
            pad = "  " * depth
            first, *rest = body.splitlines() or [""]
            lines.append(f"{pad}{bullet} {first}")
            for r in rest:
                lines.append(f"{pad}  {r}" if r.strip() else "")
        return "\n".join(lines) + "\n"

    def render_table(self, n: Node) -> str:
        rows = []
        for tr in [x for x in n.iter() if x.tag == "tr"]:
            cells = [x for x in tr.children if isinstance(x, Node) and x.tag in ("th", "td")]
            rows.append([" ".join(self.inline(c) for c in cell.children).strip().replace("|", "\\|")
                         for cell in cells])
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(out) + "\n\n"


BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "ul", "ol", "table", "blockquote",
              "hr", "div", "section", "aside", "header", "footer", "details", "figure", "li", "dl"}


def _pre_text(n: Node) -> str:
    out = []
    for c in n.children:
        if isinstance(c, str):
            s = ZW.sub("", c)
            # Prism emits one span per line; a newline text node after a line span is redundant.
            if s.strip() == "" and "\n" in s and out and out[-1].endswith("\n"):
                continue
            out.append(s)
        elif c.tag == "br":
            out.append("\n")
        else:
            inner = _pre_text(c)
            out.append(inner)
            if "token-line" in c.classes and not inner.endswith("\n"):
                out.append("\n")
    return "".join(out)


def _class_prefixed(n: Node, prefix: str) -> Node | None:
    """Docusaurus CSS-module classes carry build hashes (admonitionContent_zlPx); match the prefix."""
    return next((x for x in n.iter() if x is not n and any(c.startswith(prefix) for c in x.classes)), None)


def _last_div(n: Node) -> Node | None:
    divs = [c for c in n.children if isinstance(c, Node) and c.tag == "div"]
    return divs[-1] if divs else None


# --------------------------------------------------------------------------- page
@dataclass
class Page:
    url: str
    title: str | None
    h1: str | None
    canonical: str | None
    description: str | None
    last_updated: str | None  # ISO date when the page states one
    breadcrumbs: list[str]
    headings: list[str]
    markdown: str
    text: str
    content_hash: str
    selector: str | None
    word_count: int


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", ZW.sub("", s)).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def extract(html: str, url: str, content_selectors: list[str], strip_selectors: list[str]) -> Page:
    root = parse_html(html)
    head_title = select_first(root, "title")
    og = select_first(root, 'meta[property="og:title"]')
    canonical = select_first(root, 'link[rel="canonical"]')
    desc = select_first(root, 'meta[name="description"]')

    breadcrumbs = []
    bc = select_first(root, "nav.theme-doc-breadcrumbs")
    if bc:
        breadcrumbs = [normalize_text(li.text()) for li in bc.iter() if li.tag == "li"]
        breadcrumbs = [b for b in breadcrumbs if b]

    last_updated = None
    lu = select_first(root, ".theme-last-updated")
    t = next((x for x in (lu.iter() if lu else root.iter()) if x.tag == "time" and x.attrs.get("datetime")), None)
    if t is None:
        t = next((x for x in root.iter() if x.tag == "time" and x.attrs.get("itemprop") == "dateModified"), None)
    if t is not None:
        last_updated = t.attrs.get("datetime", "")[:10] or None
    if not last_updated:
        meta_mod = select_first(root, 'meta[property="article:modified_time"]')
        if meta_mod:
            last_updated = meta_mod.attrs.get("content", "")[:10] or None

    main, used = None, None
    for sel in content_selectors:
        main = select_first(root, sel)
        if main is not None:
            used = sel
            break
    if main is None:
        main = select_first(root, "body") or root
    strip(main, list(strip_selectors) + [".theme-last-updated", "nav.theme-doc-breadcrumbs"])

    h1n = next((x for x in main.iter() if x.tag == "h1"), None)
    headings = [normalize_text(x.text()) for x in main.iter() if x.tag in ("h2", "h3")]
    markdown = _Md(url).convert(main)
    text = normalize_text(main.text())

    title = (og.attrs.get("content") if og else None) or (normalize_text(head_title.text()) if head_title else None)
    return Page(
        url=url,
        title=title,
        h1=normalize_text(h1n.text()) if h1n else None,
        canonical=canonical.attrs.get("href") if canonical else None,
        description=desc.attrs.get("content") if desc else None,
        last_updated=last_updated,
        breadcrumbs=breadcrumbs,
        headings=[h for h in headings if h],
        markdown=markdown,
        text=text,
        content_hash=content_hash(text),
        selector=used,
        word_count=len(text.split()),
    )


def safe_extract(html: str, url: str, content_selectors: list[str], strip_selectors: list[str]
                 ) -> tuple[Page, str | None]:
    """Never raises. On failure returns a minimal Page (crude text, empty markdown) and the error,
    so one odd page can't stop a sync and its raw HTML is still stored for a later re-prepare."""
    try:
        return extract(html, url, content_selectors, strip_selectors), None
    except Exception as e:  # noqa: BLE001 - deliberately broad; recorded on the doc
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
        text = normalize_text(re.sub(r"(?s)<[^>]+>", " ", body))
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
        page = Page(url=url, title=normalize_text(m.group(1)) if m else None, h1=None, canonical=None,
                    description=None, last_updated=None, breadcrumbs=[], headings=[], markdown="",
                    text=text, content_hash=content_hash(text), selector=None, word_count=len(text.split()))
        return page, f"{type(e).__name__}: {e}"[:500]
