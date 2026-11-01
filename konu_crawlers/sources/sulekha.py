"""Sulekha Property – property.sulekha.com (buy, rent, agents).

Category pages (``/apartments-flats-for-sale/<city>``) list ad cards whose
detail links end in ``-<id>-ad``; JSON-LD is read first, cards second.
Pagination: ``?page=N``.

Note: Sulekha redirects non-Indian visitors of its search pages to
``/403?country=XX``. The city landing page (``/<city>-real-estate``) is not
geo-blocked and shows the newest ads, so ``--type latest`` works anywhere.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..base import register
from ..extract import clean_text, parse_bhk
from ..http import absolute
from ..models import Record
from ._common import area_to_sqft, price_from_text
from ._portal import PortalCrawler

BASE = "https://property.sulekha.com"
_AD = re.compile(r"-(\d{6,})-ad\b")


def parse_ad_links(html: str, base: str, city: str, transaction: str | None) -> list[Record]:
    """Ads from any Sulekha page: every ``...-<id>-ad`` link plus nearby text."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Record] = {}
    for a in soup.find_all("a", href=_AD):
        ad_id = _AD.search(a["href"]).group(1)
        if ad_id in out:
            continue
        box = a
        for _ in range(4):  # climb to the smallest container that has the price
            if box.parent is None or re.search(r"(Lakh|Crore|₹|/month)", box.get_text(" ")):
                break
            box = box.parent
        text = box.get_text(" | ", strip=True)
        url = absolute(base, a["href"])
        p, pmax, ptxt = price_from_text(text.replace("|", " "))
        trans = transaction or ("rent" if "-for-rent/" in a["href"] else "sale")
        loc = re.search(r"in-([a-z0-9-]+?)-" + re.escape(city), a["href"])
        title = clean_text(a.get_text(" ", strip=True)) or clean_text(a.get("title"))
        out[ad_id] = Record(
            source="sulekha",
            kind="listing",
            source_id=ad_id,
            url=url,
            title=title,
            transaction=trans,
            property_type=a["href"].split("/")[3] if a["href"].startswith("http") and len(a["href"].split("/")) > 3 else None,
            price=p,
            price_max=pmax,
            price_text=ptxt,
            area_sqft=area_to_sqft(re.search(r"(\d+)-sqft", a["href"]).group(1) + " sqft") if re.search(r"(\d+)-sqft", a["href"]) else None,
            bedrooms=parse_bhk(title) or parse_bhk(a["href"].replace("-", " ")),
            locality=loc.group(1).replace("-", " ").title() if loc else None,
            city=city,
            raw={"text": text[:800]},
        )
    return list(out.values())


@register
class Sulekha(PortalCrawler):
    name = "sulekha"
    title = "Sulekha Property"
    homepage = BASE
    transactions = ("sale", "rent", "latest", "agents")
    urls = {
        "sale": BASE + "/apartments-flats-for-sale/{city}",
        "rent": BASE + "/apartments-flats-for-rent/{city}",
        "latest": BASE + "/{city}-real-estate",
        "agents": BASE + "/real-estate-agents/{city}",
    }

    def parse(self, html: str, url: str, city: str, transaction: str) -> list[Record]:
        if transaction == "agents":
            recs = super().parse(html, url, city, transaction)
            for r in recs:
                r.kind = "agent"
                r.transaction = None
            return recs
        recs = super().parse(html, url, city, None if transaction == "latest" else transaction)
        ads = parse_ad_links(html, BASE, city, None if transaction == "latest" else transaction)
        return ads if len(ads) >= len(recs) else recs

    def next_url(self, first_url: str, page: int) -> str:
        return first_url if page == 1 else f"{first_url}?page={page}"
