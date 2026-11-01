"""NoBroker – nobroker.in (owner listings, buy and rent).

* Sale pages (``/flats-for-sale-in-<locality>_<city>``) are server-rendered
  with the full result set in ``window.nb.appState``: 25 listings per page
  with id, price, size, BHK, coordinates, builder, etc.
* Rent pages (``/flats-for-rent-in-<locality>_<city>``) are server-rendered
  HTML cards (``<div id="<property-id>">``).
* NoBroker's JSON APIs (``/api/v1..v5``) are disallowed in robots.txt, so we
  do not use them; we widen coverage by walking the locality pages that the
  city page links to instead. ``--max-pages`` = number of result pages.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Crawler, register, slug
from ..extract import clean_text, parse_bhk, parse_inr, parse_number
from ..http import absolute
from ..models import Record
from ._common import records_from_json

BASE = "https://www.nobroker.in"


def _ms_to_iso(ms: object) -> str | None:
    if isinstance(ms, (int, float)) and ms > 10**11:
        return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()
    return None


def _ptype(title: str | None) -> str | None:
    m = re.search(r"\b(?:BHK|RK)\s+(\w+)", title or "", re.I)
    return m.group(1).lower() if m else None


def map_sale(d: dict, city: str | None = None) -> Record:
    return Record(
        source="nobroker",
        kind="listing",
        source_id=d.get("id"),
        url=absolute(BASE, d.get("detailUrl")),
        title=clean_text(d.get("propertyTitle")),
        transaction="sale",
        property_type=_ptype(d.get("propertyTitle")),
        price=parse_inr(d.get("price")),
        price_text=d.get("formattedPrice"),
        area_sqft=parse_number(d.get("propertySize")),
        bedrooms=parse_bhk(d.get("typeDesc")) or parse_bhk(d.get("propertyTitle")),
        bathrooms=parse_number(d.get("bathroom")),
        furnishing=d.get("furnishingDesc") or d.get("furnishing"),
        locality=d.get("locality"),
        city=d.get("city") or city,
        address=clean_text(d.get("address")),
        latitude=d.get("latitude"),
        longitude=d.get("longitude"),
        project_name=d.get("buildingName") or d.get("society"),
        builder=d.get("builderName"),
        posted_by="Owner",
        status=d.get("state"),
        posted_at=_ms_to_iso(d.get("activationDate")) or _ms_to_iso(d.get("creationDate")),
        image_url=("https:" + d["originalImageUrl"]) if str(d.get("originalImageUrl", "")).startswith("//") else d.get("originalImageUrl"),
        description=clean_text(d.get("description")),
        raw=d,
    )


def parse_sale_page(html: str, city: str | None = None) -> list[Record]:
    return records_from_json(
        "nobroker",
        html,
        predicate=lambda d: "propertyCode" in d and "id" in d and ("price" in d or "rent" in d),
        mapper=lambda d: map_sale(d, city),
        key=lambda d: d.get("id"),
    )


_DETAIL_RE = re.compile(r"/property/[^\"']*?/([0-9a-f]{32})/detail")


def _before(tokens: list[str], label: str, n: int = 1) -> str | None:
    """Value printed just before a label in NoBroker's '<value> <label>' tiles."""
    for i, t in enumerate(tokens):
        if t.lower() == label.lower() and i >= n:
            return " ".join(tokens[i - n:i])
    return None


def parse_rent_page(html: str, city: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Record] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=_DETAIL_RE):
        pid = _DETAIL_RE.search(a["href"]).group(1)
        if pid in seen:
            continue
        card = soup.find(id=pid)
        if card is None:
            continue
        seen.add(pid)
        tokens = [t.strip() for t in card.get_text("|", strip=True).split("|") if t.strip()]
        text = " ".join(tokens)
        h = card.find(["h2", "h3"])
        title = clean_text(h.get_text(" ", strip=True)) if h else clean_text(a.get_text(" ", strip=True))
        rent_m = re.search(r"₹\s*([\d,]+)", text)
        dep_m = re.search(r"₹\s*([\d,]+)\s*Deposit", text)
        maint_m = re.search(r"₹\s*([\d,]+)\s*Maintenance", text)
        area_m = re.search(r"([\d,]+)\s*sqft\s*Builtup", text, re.I)
        addr = None
        for t in tokens[1:4]:
            if "," in t and len(t) > 15:
                addr = t
                break
        slug_m = re.search(r"for-rent-in-([a-z0-9-]+?)-" + re.escape((city or "").lower()) + r"-for-rs", a["href"]) if city else None
        out.append(
            Record(
                source="nobroker",
                kind="listing",
                source_id=pid,
                url=absolute(BASE, a["href"].split("#")[0]),
                title=title,
                transaction="rent",
                property_type=_ptype(title),
                price=parse_inr(rent_m.group(1)) if rent_m else None,
                price_text=rent_m.group(0) if rent_m else None,
                area_sqft=parse_number(area_m.group(1)) if area_m else None,
                bedrooms=parse_bhk(_before(tokens, "Apartment Type")) or parse_bhk(title),
                furnishing=_before(tokens, "Furnishing"),
                locality=slug_m.group(1).replace("-", " ").title() if slug_m else None,
                city=city,
                address=addr,
                posted_by="Owner",
                status=_before(tokens, "Available From"),
                raw={
                    "deposit": parse_inr(dep_m.group(1)) if dep_m else None,
                    "maintenance": parse_inr(maint_m.group(1)) if maint_m else None,
                    "preferred_tenants": _before(tokens, "Preferred Tenants"),
                    "card_text": text[:1500],
                },
            )
        )
    return out


@register
class NoBroker(Crawler):
    name = "nobroker"
    title = "NoBroker"
    homepage = BASE

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        c = slug(city)
        word = "sale" if t == "sale" else "rent"
        parse = parse_sale_page if t == "sale" else parse_rent_page
        localities = [x.strip().lower().replace(" ", "-") for x in options.get("localities", "").split(",") if x.strip()]
        queue = [f"{BASE}/flats-for-{word}-in-{loc}_{c}" for loc in localities] or [f"{BASE}/flats-for-{word}-in-{c}_{c}"]
        seen_pages: set[str] = set()
        seen_ids: set[str] = set()
        while queue and len(seen_pages) < max_pages:
            url = queue.pop(0)
            if url in seen_pages:
                continue
            seen_pages.add(url)
            resp = self.page(url)
            for rec in parse(resp.text, c):
                if rec.source_id not in seen_ids:
                    seen_ids.add(rec.source_id or "")
                    yield rec
            if not localities:
                # Walk locality pages linked from the city page for more coverage.
                for m in re.finditer(rf'href="(?:{re.escape(BASE)})?(/flats-for-{word}-in-[a-z0-9-]+_{re.escape(c)})"', resp.text):
                    nxt = BASE + m.group(1)
                    if nxt not in seen_pages and nxt not in queue:
                        queue.append(nxt)
