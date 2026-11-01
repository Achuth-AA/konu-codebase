"""Quikr Homes – quikr.com/homes (buy and rent).

Quikr's search pages only render for a JavaScript-capable browser (plain
HTTP clients get 403), so this crawler loads them in headless Chromium via
Playwright. Each page publishes schema.org JSON-LD (Apartment / House /
Residence with BHK, area, lat/long, address) and ``div.listview`` cards with
the displayed price, transaction type and poster, which we merge by URL.
Pagination: ``?page=N``.

The category URLs include Quikr's city id (Hyderabad = 28); pass ``--cid``
for cities not in :data:`CITY_IDS`.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..base import register
from ..extract import clean_text
from ..http import absolute
from ..models import Record
from ._common import price_from_text, records_from_ld
from ._portal import PortalCrawler

BASE = "https://www.quikr.com"
CITY_IDS = {"hyderabad": "28", "bangalore": "23", "chennai": "31", "mumbai": "30", "pune": "29", "delhi": "32", "kolkata": "33"}


def parse_page(html: str, city: str, transaction: str) -> list[Record]:
    recs = records_from_ld("quikr", html, transaction, city)
    soup = BeautifulSoup(html, "lxml")
    cards: dict[str, dict] = {}
    for card in soup.select("div.listview"):
        a = card.find("a", href=lambda h: h and "/p/" in h)
        if not a:
            continue
        tokens = [t.strip() for t in card.get_text("|", strip=True).split("|") if t.strip()]
        url = absolute(BASE, a["href"])
        price = next((t for t in tokens if any(u in t for u in ("Lakh", "Lac", "Crore", "Cr", "₹", ",000"))), None)

        def after(label: str) -> str | None:
            return tokens[tokens.index(label) + 1] if label in tokens and tokens.index(label) + 1 < len(tokens) else None

        cards[url.split("?")[0]] = {"price": price, "transaction_type": after("Transaction"), "availability": after("Availability"),
                                    "posted": after("Posted"), "poster": next((tokens[i - 1] for i, t in enumerate(tokens) if t in ("Individual", "Agent", "Builder") and i), None),
                                    "poster_type": next((t for t in tokens if t in ("Individual", "Agent", "Builder")), None), "tokens": tokens}
    for r in recs:
        m = re.search(r"/p/(\d+)", r.url or "")
        if m:  # /p/<listing-id>/<project-id>
            r.source_id = m.group(1)
        c = cards.get((r.url or "").split("?")[0])
        if not c:
            continue
        p, pmax, ptxt = price_from_text("₹ " + (c["price"] or ""))
        r.price, r.price_max, r.price_text = r.price or p, r.price_max or pmax, ptxt
        r.status = c["availability"] or c["transaction_type"]
        r.posted_at = c["posted"] or r.posted_at
        r.posted_by = c["poster_type"]
        r.contact_name = clean_text(c["poster"])
        r.raw = {**r.raw, "card": c}
    return recs


@register
class Quikr(PortalCrawler):
    name = "quikr"
    title = "Quikr Homes"
    homepage = BASE + "/homes"
    needs_browser = True
    urls = {
        "sale": BASE + "/homes/property/residential-for-sale-in-{city}-cid_{cid}",
        "rent": BASE + "/homes/property/residential-for-rent-in-{city}-cid_{cid}",
    }

    def start_url(self, city: str, transaction: str, options: dict) -> str:
        cid = options.get("cid") or CITY_IDS.get(city.strip().lower())
        if not cid:
            raise ValueError(f"quikr: unknown Quikr city id for {city!r}; pass --cid")
        return super().start_url(city, transaction, {**options, "cid": cid})

    def parse(self, html: str, url: str, city: str, transaction: str) -> list[Record]:
        return parse_page(html, city, transaction) or super().parse(html, url, city, transaction)
