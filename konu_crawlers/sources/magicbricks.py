"""MagicBricks – magicbricks.com (buy, rent, new projects).

Search pages embed ``window.SERVER_PRELOADED_STATE_`` whose ``searchResult``
holds 30 listings per page (id, price, area, BHK, locality, lat/long,
owner/agent, ...). Pagination: ``?page=N``. New-project rows (``psmid`` /
``pdpUrl``) are emitted as ``kind="project"``.
"""

from __future__ import annotations

from typing import Iterator

from ..base import Crawler, register, slug
from ..extract import clean_text, parse_bhk, parse_inr, parse_number
from ..http import absolute
from ..models import Record
from ._common import records_from_cards, records_from_json, records_from_ld

BASE = "https://www.magicbricks.com"


def map_listing(d: dict, city: str | None = None) -> Record:
    url = d.get("url")
    if url and not url.startswith("http"):
        url = f"{BASE}/propertyDetails/{url}"
    lat, lon = d.get("pmtLat"), d.get("pmtLong")
    if (lat is None or lon is None) and d.get("ltcoordGeo"):
        try:
            lat, lon = (float(x) for x in str(d["ltcoordGeo"]).split(","))
        except ValueError:
            pass
    trans = (d.get("transType") or d.get("transactionTypeD") or "").lower()
    return Record(
        source="magicbricks",
        kind="listing",
        source_id=str(d.get("id") or d.get("encId")),
        url=url,
        title=clean_text(d.get("propertyTitle") or d.get("auto_desc")),
        transaction="rent" if "rent" in trans else "sale" if trans else None,
        property_type=d.get("propTypeD"),
        price=parse_inr(d.get("price")),
        price_text=d.get("priceD"),
        area_sqft=parse_number(d.get("caSqFt") or d.get("coveredArea") or d.get("ca")),
        area_text=" ".join(str(x) for x in (d.get("ca"), d.get("coverAreaUnitD")) if x) or None,
        bedrooms=parse_number(d.get("bedroomD")) or parse_bhk(d.get("propertyTitle")),
        bathrooms=parse_number(d.get("bathD")),
        furnishing=d.get("furnishedD"),
        locality=d.get("lmtDName") or d.get("locSeoName"),
        city=d.get("ctName") or city,
        latitude=lat,
        longitude=lon,
        project_name=d.get("prjname"),
        posted_by=d.get("userType"),
        contact_name=d.get("oname") or d.get("caCompNameD"),
        status=d.get("possStatusD"),
        rera_id=d.get("reraId") or d.get("rera"),
        posted_at=d.get("postDateT"),
        image_url=d.get("wapImgUrl") or d.get("image"),
        description=clean_text(d.get("seoDesc")),
        raw=d,
    )


def map_project(d: dict, city: str | None = None) -> Record:
    url = d.get("pdpUrl") or d.get("oldPdpUrl")
    return Record(
        source="magicbricks",
        kind="project",
        source_id=str(d.get("psmid") or d.get("prjidC")),
        url=absolute(BASE + "/", url),
        title=clean_text(d.get("psmName")),
        transaction="sale",
        property_type=d.get("propertyTypeDesc"),
        price=parse_inr(d.get("minPrice")),
        price_max=parse_inr(d.get("maxPrice")),
        price_text=" - ".join(x for x in (d.get("minPriceDesc"), d.get("maxPriceDesc")) if x) or None,
        locality=d.get("lmtDName"),
        city=d.get("ctname") or city,
        project_name=d.get("psmName"),
        builder=d.get("devName"),
        status=d.get("psmStatus") or d.get("prjPossDate"),
        rera_id="yes" if d.get("isRera") in ("Y", True) else None,
        image_url=d.get("imageUrl") or d.get("image"),
        raw=d,
    )


def parse_page(html: str, city: str | None = None, transaction: str | None = None) -> list[Record]:
    listings = records_from_json(
        "magicbricks", html,
        predicate=lambda d: "encId" in d and "price" in d,
        mapper=lambda d: map_listing(d, city),
        key=lambda d: d.get("id") or d.get("encId"),
    )
    projects = records_from_json(
        "magicbricks", html,
        predicate=lambda d: "psmid" in d and ("pdpUrl" in d or "psmName" in d),
        mapper=lambda d: map_project(d, city),
        key=lambda d: d.get("psmid"),
    )
    if transaction == "projects":
        return projects
    if listings:
        return listings
    return records_from_ld("magicbricks", html, transaction, city) or records_from_cards(
        "magicbricks", html, BASE, transaction, city, selector="div.mb-srp__list"
    )


@register
class MagicBricks(Crawler):
    name = "magicbricks"
    title = "MagicBricks"
    homepage = BASE
    transactions = ("sale", "rent", "projects")

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        c = slug(city)
        start = {
            "sale": f"{BASE}/property-for-sale-in-{c}-pppfs",
            "rent": f"{BASE}/property-for-rent-in-{c}-pppfr",
            "projects": f"{BASE}/new-projects-{c}",
        }[t]
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            url = start if page == 1 else f"{start}?page={page}"
            recs = [r for r in parse_page(self.page(url).text, c, t) if r.source_id not in seen]
            if not recs:
                break
            for r in recs:
                seen.add(r.source_id or "")
                yield r
