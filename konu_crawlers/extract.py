"""Generic helpers for pulling structured data out of HTML pages.

Most Indian property portals server-render their search pages and embed the
full result set as JSON (``window.__INITIAL_STATE__ = {...}``, Next.js
``__NEXT_DATA__``, JSON-LD, ...). Rather than hard-coding one variable name per
site (which breaks on every redeploy), we harvest every JSON blob on the page
and walk it looking for dicts that look like property listings.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Iterator

from bs4 import BeautifulSoup

_ASSIGN_RE = re.compile(r"(?:window\.|var\s+|let\s+|const\s+|self\.)?([A-Za-z_$][\w$.]*)\s*=\s*(?=[{\[])")
_JSON_PARSE_RE = re.compile(r"JSON\.parse\(\s*(['\"])(.*?)(?<!\\)\1\s*\)", re.S)


def scan_balanced(text: str, start: int) -> int:
    """Return the index just past the JSON value starting at ``text[start]``.

    Handles nested braces/brackets and quoted strings. Returns -1 if the value
    is not terminated.
    """
    opener = text[start]
    if opener not in "{[":
        return -1
    depth = 0
    i = start
    n = len(text)
    in_str: str | None = None
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'":
            in_str = c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _loads_lenient(blob: str) -> Any:
    try:
        return json.loads(blob, strict=False)
    except ValueError:
        pass
    # JS object literals frequently use ``undefined`` / ``!0`` / ``!1``.
    fixed = re.sub(r"(?<=[:\[,])\s*undefined\b", "null", blob)
    fixed = re.sub(r"(?<=[:\[,])\s*!0\b", "true", fixed)
    fixed = re.sub(r"(?<=[:\[,])\s*!1\b", "false", fixed)
    try:
        return json.loads(fixed, strict=False)
    except ValueError:
        return None


def iter_json_blobs(html: str, min_len: int = 200) -> Iterator[tuple[str, Any]]:
    """Yield ``(name, parsed_json)`` for every JSON payload embedded in ``html``."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script"):
        text = tag.string or tag.get_text() or ""
        stype = (tag.get("type") or "").lower()
        if len(text) < min_len and stype not in ("application/json", "application/ld+json", "text/x-config") and tag.get("id") != "__NEXT_DATA__":
            continue
        stripped = text.strip()
        if (
            stype in ("application/json", "application/ld+json", "text/x-config")
            or tag.get("id") == "__NEXT_DATA__"
            or (stripped[:1] in "{[" and stripped[-1:] in "}]" and stype not in ("", "text/javascript", "module"))
        ):
            data = _loads_lenient(text.strip())
            if data is not None:
                yield tag.get("id") or stype, data
            continue
        for m in _JSON_PARSE_RE.finditer(text):
            raw = m.group(2)
            try:
                raw = json.loads(m.group(1) + raw + m.group(1)) if m.group(1) == '"' else raw.encode().decode("unicode_escape")
            except ValueError:
                continue
            data = _loads_lenient(raw)
            if data is not None:
                yield "JSON.parse", data
        for m in _ASSIGN_RE.finditer(text):
            start = m.end()
            end = scan_balanced(text, start)
            if end == -1 or end - start < min_len:
                continue
            data = _loads_lenient(text[start:end])
            if data is not None:
                yield m.group(1), data


def walk(obj: Any) -> Iterator[Any]:
    """Depth-first walk over every dict and list inside ``obj``."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(reversed(list(cur.values())))  # keep document order
        elif isinstance(cur, list):
            yield cur
            stack.extend(reversed(cur))


def find_dicts(obj: Any, predicate: Callable[[dict], bool]) -> list[dict]:
    """Every dict inside ``obj`` (at any depth) that satisfies ``predicate``."""
    return [d for d in walk(obj) if isinstance(d, dict) and predicate(d)]


def has_keys(*groups: Iterable[str]) -> Callable[[dict], bool]:
    """Predicate: the dict has at least one key from *each* group."""
    groups_l = [tuple(g) for g in groups]

    def pred(d: dict) -> bool:
        return all(any(k in d for k in g) for g in groups_l)

    return pred


def dedupe(items: Iterable[dict], key: Callable[[dict], Any]) -> list[dict]:
    seen: set = set()
    out = []
    for it in items:
        k = key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def ld_json(html: str) -> list[Any]:
    """All JSON-LD payloads on the page, with ``@graph`` arrays flattened."""
    out: list[Any] = []
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        data = _loads_lenient((tag.string or tag.get_text() or "").strip())
        if data is None:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if isinstance(it, dict) and "@graph" in it:
                out.extend(it["@graph"])
            else:
                out.append(it)
    return out


def ld_items(html: str, *types: str) -> list[dict]:
    """JSON-LD nodes (at any depth) whose ``@type`` matches one of ``types``."""
    wanted = {t.lower() for t in types}
    out = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                visit(x)
            return
        if not isinstance(node, dict):
            return
        t = node.get("@type")
        ts = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.lower() in wanted for x in ts):
            out.append(node)  # don't descend: nested nodes describe this one
            return
        for v in node.values():
            visit(v)

    for blob in ld_json(html):
        visit(blob)
    return out


def first(d: dict, *keys: str, default: Any = None) -> Any:
    """First non-empty value among ``keys`` (dotted paths allowed)."""
    for k in keys:
        cur: Any = d
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, "", [], {}):
            return cur
    return default


_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
_UNITS = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5, "k": 1e3, "thousand": 1e3}


def parse_inr(value: Any) -> float | None:
    """Parse '₹1.25 Cr', '45 Lac', '25,000', 1500000 → rupees as float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("₹", " ").replace("Rs.", " ").replace("INR", " ").strip().lower()
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        num = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    rest = s[m.end():].strip().split(" ")[0].strip(".") if s[m.end():].strip() else ""
    return round(num * _UNITS.get(rest, 1), 2)


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_bhk(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    m = re.search(r"(\d+(?:\.\d)?)\s*(?:bhk|rk|bed)", str(value), re.I)
    if m:
        return float(m.group(1))
    if re.search(r"\b1\s*rk\b", str(value), re.I):
        return 0.5
    return None


def clean_text(s: Any) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


_PRICE_HINT = re.compile(r"(\u20b9|Rs\.?\s?\d|\d\s*(?:Lac|Lakh|Cr|Crore)s?\b)", re.I)


def find_cards(soup_or_html: Any, min_count: int = 3, max_text: int = 3000) -> list:
    """Heuristically find repeated listing "cards" on a search-results page.

    A card is an element that (a) mentions a price, (b) contains a link and
    (c) shares its tag+class signature with at least ``min_count - 1``
    siblings-in-spirit elsewhere on the page. Returns the elements of the
    most frequent qualifying signature, outermost first.
    """
    soup = BeautifulSoup(soup_or_html, "lxml") if isinstance(soup_or_html, str) else soup_or_html
    groups: dict[tuple, list] = {}
    for el in soup.find_all(["div", "li", "article", "section", "a", "tr"]):
        cls = el.get("class")
        if not cls:
            continue
        text = el.get_text(" ", strip=True)
        if not text or len(text) > max_text or not _PRICE_HINT.search(text):
            continue
        if el.name != "a" and not el.find("a", href=True):
            continue
        groups.setdefault((el.name, tuple(cls)), []).append(el)
    best: list = []
    best_score = 0.0
    for els in groups.values():
        if len(els) < min_count:
            continue
        # Prefer the outermost repeated element: more text per card, but
        # penalise wrappers that hold several cards (very long text).
        avg = sum(len(e.get_text(" ", strip=True)) for e in els) / len(els)
        score = len(els) * min(avg, 600)
        if score > best_score:
            best, best_score = els, score
    return best


def table_rows(table: Any, header_row: int | None = None) -> list[dict[str, str]]:
    """Turn an HTML ``<table>`` into a list of ``{header: cell_text}`` dicts.

    The header is the first row that contains ``<th>`` cells (or row
    ``header_row`` when given). Cell links are kept under ``"<header>__href"``.
    """
    rows = table.find_all("tr")
    if not rows:
        return []
    if header_row is None:
        header_row = next((i for i, r in enumerate(rows) if r.find("th")), 0)
    headers = [clean_text(c.get_text(" ", strip=True)) or f"col{i}" for i, c in enumerate(rows[header_row].find_all(["th", "td"]))]
    out = []
    for r in rows[header_row + 1:]:
        cells = r.find_all(["td", "th"], recursive=False) or r.find_all(["td", "th"])
        if len(cells) < max(2, len(headers) // 2):
            continue  # pager / spacer rows
        row: dict[str, str] = {}
        for i, c in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col{i}"
            row[key] = clean_text(c.get_text(" ", strip=True)) or ""
            a = c.find("a", href=True)
            if a:
                row[key + "__href"] = a["href"]
        out.append(row)
    return out


def largest_table(soup: Any) -> Any:
    """The innermost table with the most rows (the data grid on ASP.NET pages)."""
    leaf = [t for t in soup.find_all("table") if not t.find("table")]
    return max(leaf, key=lambda t: len(t.find_all("tr")), default=None)


def form_fields(soup: Any, form_id: str | None = None) -> dict[str, str]:
    """All successful-control values of a form (hidden fields, selects, inputs)."""
    form = soup.find("form", id=form_id) if form_id else soup.find("form")
    if form is None:
        return {}
    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        typ = (inp.get("type") or "text").lower()
        if not name or typ in ("submit", "button", "image", "reset", "file"):
            continue
        if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        data[name] = inp.get("value", "")
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        data[name] = opt.get("value", opt.get_text(strip=True)) if opt else ""
    return data


def label_values(html_or_soup: Any, labels: Iterable[str], max_len: int = 80) -> dict[str, str]:
    """Pull ``Label: value`` facts out of a detail page, whatever the markup.

    For each wanted label, finds the smallest element whose own text is the
    label (optionally followed by ':'), then takes the value from its next
    sibling element, or else from the rest of its parent's text. Labels are
    matched case-insensitively; the first plausible value wins.
    """
    soup = BeautifulSoup(html_or_soup, "lxml") if isinstance(html_or_soup, str) else html_or_soup
    wanted = {re.sub(r"\W+", " ", l).strip().lower(): l for l in labels}
    out: dict[str, str] = {}
    for el in soup.find_all(string=True):
        if el.parent is None or el.parent.name in ("script", "style", "title", "option"):
            continue
        key = re.sub(r"\W+", " ", str(el)).strip().lower()
        if key not in wanted or wanted[key] in out:
            continue
        node = el.parent
        value = None
        for _ in range(3):  # climb a little if the label sits in a <b>/<span> wrapper
            sib = node.find_next_sibling()
            if sib is not None:
                v = clean_text(sib.get_text(" ", strip=True))
                if v and len(v) <= max_len and v.lower() != key:
                    value = v
                    break
            parent_text = clean_text(node.parent.get_text(" ", strip=True)) if node.parent else None
            if parent_text and parent_text.lower().startswith(str(el).strip().lower()):
                rest = parent_text[len(str(el).strip()):].lstrip(" :-|")
                if rest and len(rest) <= max_len:
                    value = rest
                    break
            node = node.parent
            if node is None:
                break
        if value:
            out[wanted[key]] = value
    return out
