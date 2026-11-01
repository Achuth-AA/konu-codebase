"""RoofandFloor – roofandfloor.com (The Hindu group; strong in South India).

Search pages server-render the first 10 ``article.sr-listing-card`` cards;
the page's own "load more" requests further batches as HTML fragments from
``sale/flats/fetch_pages.php`` / ``rent/fetch_pages.php`` with
``page`` + ``per_page`` params, which we call the same way. New-project
pages list ``div.property-click`` cards.
"""

from __future__ import annotations

import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Crawler, register, slug
from ..extract import clean_text, parse_bhk
from ..http import absolute
from ..models import Record
from ._common import area_to_sqft, price_from_text, text_of

BASE = "https://www.roofandfloor.com/"
PAGE_SIZE = 10


def parse_listing_cards(html: str, transaction: str, city: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("article.sr-listing-card"):
        rows = card.select(".sr-config-row")
        cells = [c.get_text(" ", strip=True) for c in rows[0].find_all("span")] if rows else []
        # Listing rows: area | price | post date. Project rows: config label | area | price,
        # one row per BHK type, so take the range across all rows.
        areas = [text_of(r, ".sr-config-area") for r in rows]
        prices = [text_of(r, ".sr-config-price") for r in rows]
        area_t = " / ".join(a for a in areas if a) or None
        lo = [price_from_text("₹ " + p) for p in prices if p]
        lo = [x for x in lo if x[0]]
        price = min(x[0] for x in lo) if lo else None
        price_max = max(x[1] or x[0] for x in lo) if lo else None
        price_max = price_max if price_max and price and price_max > price else None
        price_text = " / ".join(x[2] for x in lo if x[2]) or None
        labels = [text_of(r, ".sr-config-label") for r in rows]
        posted = cells[2] if len(cells) > 2 and not any(labels) else None
        cta = card.select_one("[data-listing-id][data-listing-button]")
        title = text_of(card, ".sr-card-title")
        loc = text_of(card, ".sr-card-location")
        tags = [t.get_text(" ", strip=True) for t in card.select(".sr-tag")]
        desc = card.select_one(".sr-card-desc")
        img = card.select_one("img.sr-card-img")
        loc_m = re.search(r"\bin ([A-Za-z .-]+)$", title or "")
        is_project = "/project/" in (card.get("id") or "")
        out.append(
            Record(
                source="roofandfloor",
                kind="project" if is_project else "listing",
                source_id=(cta.get("data-listing-id") if cta else None) or card.get("data-listing-id") or card.get("id"),
                url=absolute(BASE, card.get("id")) if card.get("id") else None,
                title=title,
                transaction=transaction,
                property_type=(cta.get("data-leadfrom") if cta else None),
                price=price,
                price_max=price_max,
                price_text=price_text,
                area_sqft=area_to_sqft((areas[0] or "").split("-")[0] + " sqft") if areas and areas[0] else None,
                area_text=area_t,
                bedrooms=parse_bhk(labels[0] if labels and labels[0] else title),
                locality=clean_text(loc_m.group(1)) if loc_m else None,
                city=city,
                address=loc,
                posted_by=text_of(card, ".sr-agent-meta"),
                contact_name=text_of(card, ".sr-agent-name"),
                status=next((t for t in tags if re.search(r"ready|construction|furnish", t, re.I)), None),
                posted_at=posted,
                image_url=absolute(BASE, img.get("src")) if img else None,
                description=clean_text(desc.get("data-full")) if desc else None,
                raw={"tags": tags, "config": [dict(label=l, area=a, price=p) for l, a, p in zip(labels, areas, prices)]},
            )
        )
    return out


def parse_project_cards(html: str, city: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("div.property-click"):
        link = card.find("a", href=re.compile(r"/project/"))
        if not link:
            continue
        tokens = [t.strip() for t in card.get_text("|", strip=True).split("|") if t.strip()]

        def after(label: str) -> str | None:
            for i, t in enumerate(tokens):
                if t.rstrip(" :").lower() == label.lower() and i + 1 < len(tokens):
                    return tokens[i + 1]
            return None

        price, price_max, price_text = price_from_text("₹ " + (after("Price") or ""))
        name = link.get_text(" ", strip=True) or tokens[1] if len(tokens) > 1 else None
        loc = after("Location")
        m = re.search(r"/project/[^/]+/(\d+)", link["href"])
        out.append(
            Record(
                source="roofandfloor",
                kind="project",
                source_id=m.group(1) if m else None,
                url=absolute(BASE, link["href"]),
                title=clean_text(name),
                transaction="sale",
                property_type=tokens[tokens.index("Get this deal") + 1] if "Get this deal" in tokens and tokens.index("Get this deal") + 1 < len(tokens) else None,
                price=price,
                price_max=price_max,
                price_text=price_text,
                area_text=after("Area"),
                area_sqft=area_to_sqft((after("Area") or "").split("-")[0] + " sqft") if after("Area") else None,
                bedrooms=parse_bhk((after("Beds") or "").split(",")[0] + " bhk") if after("Beds") else None,
                locality=clean_text(loc.split(",")[-3]) if loc and loc.count(",") >= 2 else loc,
                city=city,
                address=loc,
                project_name=clean_text(name),
                raw={"tokens": tokens, "rate_per_sqft": after("Rate per Sqft")},
            )
        )
    return out


@register
class RoofAndFloor(Crawler):
    name = "roofandfloor"
    title = "RoofandFloor"
    homepage = BASE
    transactions = ("sale", "rent", "projects")

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        c = slug(city)
        if t == "projects":
            yield from parse_project_cards(self.page(f"{BASE}new-projects-in-{c}").text, c)
            return
        first_url = f"{BASE}{c}/flats-for-{t}-in-{c}"
        frag = "sale/flats/fetch_pages.php" if t == "sale" else "rent/fetch_pages.php"
        # No ``ptype`` for rent: the flats-only rent feed is often empty, the
        # unfiltered one also covers houses / PGs / commercial rentals.
        params = {"loc": c, "city": c}
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            if page == 1 and t == "sale":
                html = self.page(first_url).text
            else:
                html = self.fetcher.get(BASE + frag, params={**params, "page": page, "per_page": PAGE_SIZE}).text
            recs = [r for r in parse_listing_cards(html, t, c) if r.source_id not in seen]
            if not recs:
                break
            for r in recs:
                seen.add(r.source_id or "")
                yield r
