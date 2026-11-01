"""RealEstateIndia – realestateindia.com (buy, rent, new projects, agents).

Search pages server-render 20 ``div.pcList`` listing cards (price, built-up
area, bathrooms, furnishing, poster) or 20 ``div.spc-item`` project cards.
"View More Results" posts the page's own search parameters (embedded in its
script) to ``/Functions/property_fetch_results.php`` /
``fetch_results_projects.php`` with ``pageno`` = 1, 2, ... and appends the
returned HTML fragment; we make the same requests for pages 2+.

Agents: the first page of ``agents-brokers-in-<city>.htm`` only; its
load-more endpoint is disallowed by robots.txt.
"""

from __future__ import annotations

import re
from typing import Iterator

from bs4 import BeautifulSoup, Tag

from ..base import Crawler, register, slug
from ..extract import clean_text, parse_bhk, parse_number
from ..http import absolute
from ..models import Record
from ._common import area_to_sqft, price_from_text, text_of

BASE = "https://www.realestateindia.com"
_POST_RE = re.compile(r"\$\.post\('([^']+/Functions/(?:property_fetch_results|fetch_results_projects)\.php)',\s*(\{[^}]*\})")
_PARAM_RE = re.compile(r"'(\w+)'\s*:\s*'([^']*)'")
_TITLE_RE = re.compile(r"for (Sale|Rent|[\w ]*?)\s*in ([^,|]+)", re.I)  # "PG for Boys in X" too
_TYPE_RE = re.compile(r"^(?:[\d.]+\s*(?:BHK|RK)\s+)?(.+?)\s+for\b", re.I)


def load_more_request(html: str) -> tuple[str, dict[str, str]] | None:
    """The endpoint and form fields the page's "View More Results" posts."""
    m = _POST_RE.search(html)
    if not m:
        return None
    return m.group(1), dict(_PARAM_RE.findall(m.group(2)))


def _facts(card: Tag) -> dict[str, str]:
    """``<span class=area-top>Label</span><span class=area-btm>Value</span>`` pairs."""
    out = {}
    for li in card.select("li"):
        k, v = li.select_one(".area-top"), li.select_one(".area-btm")
        if k and v:
            out[clean_text(k.get_text(" ", strip=True)) or ""] = clean_text(v.get_text(" ", strip=True)) or ""
    return out


def parse_listings(html: str, city: str | None = None, transaction: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("div.pcList"):
        inner = card.select_one("[data-url]")
        url = inner.get("data-url") if inner else None
        link = card.select_one("h2 a")
        url = url or (link.get("href") if link else None)
        if not url:
            continue
        m = re.search(r"-(\d+)\.htm", url)
        title = clean_text(link.get_text(" ", strip=True)) if link else None
        heading = (title or "").split("|")[0].strip() or None
        f = _facts(card)
        price, price_max, price_text = price_from_text(f.get("Price"))
        tm = _TITLE_RE.search(heading or "")
        trans = tm.group(1).lower() if tm and tm.group(1).lower() in ("sale", "rent") else transaction
        text = card.get_text(" | ", strip=True)
        poster = next((t for t in ("Owner", "Agent", "Builder") if f"| {t} |" in text), None)
        prof = card.find("a", href=re.compile(r"/profile/"))
        proj = card.select_one(".head-title-left p a")
        posted = re.search(r"Posted on\s*:\s*([^|]+)", text)
        img = card.find("img")
        desc = card.select_one("._data_show")
        out.append(
            Record(
                source="realestateindia",
                kind="listing",
                source_id=m.group(1) if m else None,
                url=url,
                title=heading,
                transaction=trans,
                property_type=(_TYPE_RE.match(heading).group(1) if heading and _TYPE_RE.match(heading) else None),
                price=price,
                price_max=price_max,
                price_text=f.get("Price"),
                area_sqft=area_to_sqft(f.get("Built Up Area") or f.get("Carpet Area") or f.get("Plot Area") or f.get("Super Area")),
                area_text=f.get("Built Up Area") or f.get("Carpet Area") or f.get("Plot Area") or f.get("Super Area"),
                bedrooms=parse_bhk(heading),
                bathrooms=parse_number(f.get("Bathroom")),
                furnishing=f.get("Furnishing"),
                locality=clean_text(tm.group(2)) if tm else None,
                city=city,
                project_name=clean_text(proj.get_text(" ", strip=True)) if proj else None,
                posted_by=poster,
                contact_name=clean_text(prof.get_text(" ", strip=True)) if prof and prof.get_text(strip=True) else None,
                posted_at=clean_text(posted.group(1)) if posted else None,
                image_url=img.get("src") if img and "under-screening" not in (img.get("src") or "") else None,
                description=clean_text(desc.get_text(" ", strip=True).replace("View all details", "")) if desc else None,
                raw={"facts": f, "tags": [li.get_text(strip=True) for li in card.select(".chip-list li")]},
            )
        )
    return out


def parse_projects(html: str, city: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for item in soup.select("div.spc-item"):
        box = item.select_one("[data-url]")
        link = item.select_one(".spc-title a")
        url = (box.get("data-url") if box else None) or (link.get("href") if link else None)
        if not url:
            continue
        m = re.search(r"pjid-(\d+)", url)
        feats = {text_of(li, ".spcf-lbl"): text_of(li, ".spcf-val") for li in item.select(".spc-feature li")}
        units = []
        for li in item.select(".plb-outer li"):
            units.append({"config": text_of(li, ".spc-uc-title"), "price": text_of(li, ".spc-uc-price .price")})
        prices = [p for p in (price_from_text("₹ " + (u["price"] or ""))[0] for u in units) if p]
        main_price, main_max, _ = price_from_text("₹ " + (text_of(item, ".spc-price") or ""))
        lo = min(prices) if prices else main_price
        hi = max(prices) if prices else main_max
        loc = text_of(item, ".spc-location [itemprop=addressLocality]") or text_of(item, ".spc-location a")
        builder = text_of(item, ".spc-location strong")
        img = item.find("img")
        name = text_of(link) if link else None
        areas = [area_to_sqft(re.sub(r"^.*-\s*", "", u["config"] or "")) for u in units if u["config"] and "Sq" in u["config"]]
        out.append(
            Record(
                source="realestateindia",
                kind="project",
                source_id=m.group(1) if m else None,
                url=url,
                title=name,
                transaction="sale",
                property_type=feats.get("Configs"),
                price=lo,
                price_max=hi if hi and lo and hi > lo else None,
                price_text=text_of(item, ".spc-price"),
                area_sqft=min(a for a in areas if a) if any(areas) else None,
                bedrooms=parse_bhk(feats.get("Configs")),
                locality=clean_text(loc.split(",")[0]) if loc else None,
                city=city,
                address=loc,
                project_name=name,
                builder=re.sub(r"^By\s+", "", builder) if builder else None,
                posted_by="Builder",
                status=feats.get("Possession Status"),
                image_url=img.get("src") if img else None,
                description=text_of(item, ".spc-sd"),
                raw={"features": feats, "units": units, "total_units": parse_number(feats.get("Total Units"))},
            )
        )
    return out


def parse_agents(html: str, city: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for item in soup.select("div.agent_item"):
        prof = item.find("a", href=re.compile(r"/profile/"))
        if not prof:
            continue
        m = re.search(r"-(\d+)/?$", prof["href"])
        text = item.get_text(" | ", strip=True)
        tokens = [t.strip() for t in text.split("|") if t.strip()]
        name = text_of(item, ".ag_name") or clean_text(prof.get_text(" ", strip=True)) or (tokens[0] if tokens else None)
        sale = re.search(r"(\d[\d,]*)\s*\|\s*Property for Sale", text)
        rent = re.search(r"(\d[\d,]*)\s*\|\s*Property for Rent", text)
        exp = re.search(r"(\d+)\s*Years? Experience", text)
        rating = re.search(r"\|\s*(\d(?:\.\d)?)\s*\|\s*\d+\s*reviews?", text)
        locality = tokens[1] if len(tokens) > 1 and not re.match(r"^\d", tokens[1]) else None
        out.append(
            Record(
                source="realestateindia",
                kind="agent",
                source_id=m.group(1) if m else None,
                url=absolute(BASE, prof["href"]),
                title=clean_text(name),
                locality=clean_text(re.sub(rf"\s*{re.escape((city or '').title())}$", "", locality or "")) or None,
                city=city,
                status="RERA registered" if "RERA registered" in text else None,
                raw={
                    "listings_for_sale": parse_number(sale.group(1)) if sale else None,
                    "listings_for_rent": parse_number(rent.group(1)) if rent else None,
                    "years_experience": parse_number(exp.group(1)) if exp else None,
                    "rating": parse_number(rating.group(1)) if rating else None,
                    "text": text[:1200],
                },
            )
        )
    return out


@register
class RealEstateIndia(Crawler):
    name = "realestateindia"
    title = "RealEstateIndia"
    homepage = BASE
    transactions = ("sale", "rent", "projects", "agents")

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        c = slug(city)
        first = {
            "sale": f"{BASE}/{c}-property/flats-apartments-for-sale.htm",
            "rent": f"{BASE}/{c}-property/flats-apartments-for-rent.htm",
            "projects": f"{BASE}/{c}-property/new-projects.htm",
            "agents": f"{BASE}/agents-brokers-in-{c}.htm",
        }[t]
        parse = {
            "sale": lambda h: parse_listings(h, c, "sale"),
            "rent": lambda h: parse_listings(h, c, "rent"),
            "projects": lambda h: parse_projects(h, c),
            "agents": lambda h: parse_agents(h, c),
        }[t]
        html = self.page(first).text
        seen: set[str] = set()

        def fresh(recs: list[Record]) -> list[Record]:
            new = [r for r in recs if (r.source_id or r.url) not in seen]
            seen.update(r.source_id or r.url or "" for r in new)
            return new

        yield from fresh(parse(html))
        more = load_more_request(html) if t != "agents" else None
        if not more:
            return
        endpoint, params = more
        for page in range(1, max_pages):  # the page's own counter starts at 1 for batch 2
            frag = self.fetcher.post(endpoint, data={**params, "pageno": str(page)},
                                     headers={"X-Requested-With": "XMLHttpRequest", "Referer": first}).text
            recs = fresh(parse(frag))
            if not recs:
                break
            yield from recs
