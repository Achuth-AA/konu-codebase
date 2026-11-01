"""Square Yards – squareyards.com (resale, rental, new projects).

Listing cards carry all their data as ``data-*`` attributes (price, area,
BHK, locality, project, broker...). JSON-LD on the same page adds lat/long,
bathrooms and floor; new-project pages publish ``ApartmentComplex`` JSON-LD
with price ranges and unit configurations. Pagination: ``?page=N``.
"""

from __future__ import annotations

from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Crawler, register, slug
from ..extract import clean_text, parse_bhk, parse_inr
from ..models import Record
from ._common import area_to_sqft, records_from_ld

BASE = "https://www.squareyards.com"


def parse_cards(html: str, city: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Record] = {}
    for el in soup.find_all(attrs={"data-propertyid": True, "data-price": True}):
        a = el.attrs
        pid = a["data-propertyid"]
        if pid in out:
            continue
        kind = (a.get("data-type") or "").lower()
        out[pid] = Record(
            source="squareyards",
            kind="listing",
            source_id=pid,
            url=a.get("data-url") or a.get("data-propertylink"),
            title=clean_text(a.get("data-propname") or a.get("data-name")),
            transaction="rent" if kind.startswith("rent") else "sale",
            property_type=a.get("data-propertytype"),
            price=parse_inr(a.get("data-price")),
            price_text=clean_text(" ".join(x for x in (a.get("data-totalprice"), a.get("data-pricedurationname")) if x)),
            area_sqft=area_to_sqft(a.get("data-area")),
            area_text=a.get("data-area"),
            bedrooms=parse_bhk(a.get("data-unittype")),
            locality=a.get("data-sublocalityname"),
            city=a.get("data-city") or city,
            address=a.get("data-locality"),
            project_name=clean_text(a.get("data-projectname")),
            posted_by={"CP": "Agent", "EMPLOYEE": "Square Yards", "OWNER": "Owner"}.get(a.get("data-user-type", ""), a.get("data-user-type")),
            contact_name=clean_text(a.get("data-username")),
            # data-propstatus is possession for sale cards but furnishing for rentals
            status=a.get("data-propstatus") if not kind.startswith("rent") else None,
            furnishing=a.get("data-propstatus") if kind.startswith("rent") else None,
            image_url=a.get("data-image"),
            raw={k[5:]: v for k, v in a.items() if k.startswith("data-")},
        )
    return list(out.values())


def parse_page(html: str, city: str | None = None, transaction: str | None = None) -> list[Record]:
    ld = records_from_ld("squareyards", html, None if transaction == "projects" else transaction, city)
    if transaction == "projects":
        return [r for r in ld if r.kind == "project"]
    cards = parse_cards(html, city)
    by_url = {r.url: r for r in ld}
    for r in cards:
        extra = by_url.pop(r.url, None)
        if extra:
            r.latitude, r.longitude = extra.latitude, extra.longitude
            r.bathrooms = r.bathrooms or extra.bathrooms
            r.description = extra.description
            r.posted_at = extra.posted_at
    # Featured new projects appear on resale pages too (Product JSON-LD).
    projects = [r for r in by_url.values() if r.kind == "project"]
    return cards + projects if cards else ld


@register
class SquareYards(Crawler):
    name = "squareyards"
    title = "Square Yards"
    homepage = BASE
    transactions = ("sale", "rent", "projects")

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        c = slug(city)
        start = {
            "sale": f"{BASE}/sale/property-for-sale-in-{c}",
            "rent": f"{BASE}/rent/property-for-rent-in-{c}",
            "projects": f"{BASE}/new-projects-in-{c}",
        }[t]
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            url = start if page == 1 else f"{start}?page={page}"
            recs = [r for r in parse_page(self.page(url).text, c, t) if (r.url or r.source_id) not in seen]
            if not recs:
                break
            for r in recs:
                seen.add(r.url or r.source_id or "")
                yield r
