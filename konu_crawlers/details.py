"""Listing detail pages → the extra facts search pages don't show.

Search results rarely carry floor, balconies, parking, age, facing,
ownership, view or pincode; each portal's listing page does, as a block of
label/value pairs. :func:`fetch_details` loads a listing's page and returns
those facts under one set of normalised keys, whatever the site calls them.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from bs4 import BeautifulSoup

from .extract import clean_text, iter_json_blobs, label_values, ld_items, walk
from .http import Fetcher
from .models import Record

# normalised key -> labels used across the portals (matched case-insensitively)
SYNONYMS: dict[str, tuple[str, ...]] = {
    "floor": ("Floor Number", "Property on Floor", "On Floor", "Floor No", "Property Floor No.", "Floor"),
    "total_floors": ("Total Floor Count", "Total Floor(s)", "No Of Floors", "Total No. of Floors", "Total Floors", "Floors"),
    "balconies": ("Balcony", "Balconie(s)", "Balconies", "No of Balconies"),
    "car_parking": ("No of Car Parking", "Car Parking", "Covered Parking", "4 Wheeler Parking", "Four Wheeler Parking", "Parking"),
    "age": ("Age of the property", "Age of Property", "Age of Construction", "Property Age", "Age of Building"),
    "facing": ("Facing", "Property Facing"),
    "ownership": ("Ownership", "Ownership Type"),
    "overlooking": ("Overlooking", "View"),
    "transaction_type": ("Transaction", "Transaction Type", "Sale Type"),
    "status": ("Possession Status", "Construction Status", "Availability", "Status"),
    "furnishing": ("Furnishing", "Furnishing Status"),
    "society": ("Project & Society", "Society", "Project", "Project Name"),
    "bathrooms": ("Number of Bathroom", "Bathroom(s)", "Bathrooms", "Bathroom"),
    "pincode": ("Pincode", "Pin Code", "PIN"),
    "address": ("Address", "Location"),
}
_ALL_LABELS = [l for ls in SYNONYMS.values() for l in ls]


def _pairs(soup: BeautifulSoup, item: str, lbl: str, val: str) -> dict[str, str]:
    out = {}
    for it in soup.select(item):
        l, v = it.select_one(lbl), it.select_one(val)
        if l and v:
            out[clean_text(l.get_text(" ", strip=True)) or ""] = clean_text(v.get_text(" ", strip=True)) or ""
    return out


def _sulekha(soup: BeautifulSoup) -> dict[str, str]:
    """``<span>Label</span> value`` list items and ``<span>Label <b>value</b></span>`` chips."""
    out = _pairs(soup, ".add-info", "span", "b")
    for li in soup.select("li, li > div"):
        sp = li.find("span", recursive=False)
        if sp:
            lbl = clean_text(sp.get_text(" ", strip=True))
            val = clean_text(li.get_text(" ", strip=True)[len(sp.get_text(" ", strip=True)):] if li.get_text(" ", strip=True).startswith(sp.get_text(" ", strip=True)) else "")
            if lbl and val and len(val) < 60:
                out.setdefault(lbl, val)
    for sp in soup.select(".prop-features span"):
        b = sp.find("b")
        if b:
            out.setdefault(clean_text("".join(sp.find_all(string=True, recursive=False))) or "", clean_text(b.get_text(" ", strip=True)) or "")
    return out


def _squareyards(soup: BeautifulSoup) -> dict[str, str]:
    out = {}
    for td in soup.select("table.specifications-table td"):
        st = td.find("strong")
        if st:
            lbl = clean_text("".join(td.find_all(string=True, recursive=False)))
            if lbl:
                out[lbl] = clean_text(st.get_text(" ", strip=True)) or ""
    return out


SITE_PAIRS: dict[str, Callable[[BeautifulSoup], dict[str, str]]] = {
    "squareyards": _squareyards,
    "roofandfloor": lambda s: _pairs(s, ".pd-spec-item", ".pd-spec-lbl", ".pd-spec-val"),
    "realestateindia": lambda s: _pairs(s, ".pd_property_information li", ".pd_information_lbl", ".pd_information_val"),
    "sulekha": _sulekha,
    "quikr": lambda s: _pairs(s, "#Features li", "label", "span"),
}

_FACING = {"E": "East", "W": "West", "N": "North", "S": "South", "NE": "North-East", "NW": "North-West", "SE": "South-East", "SW": "South-West"}


def _nobroker(html: str, rec: Record) -> dict[str, Any]:
    """NoBroker detail pages embed the full property object in ``nb.appState``."""
    for _, blob in iter_json_blobs(html):
        for d in walk(blob):
            if isinstance(d, dict) and d.get("id") == rec.source_id and ("floor" in d or "totalFloor" in d):
                parking = str(d.get("parking") or "").upper()
                return {
                    "floor": d.get("floor"),
                    "total_floors": d.get("totalFloor"),
                    "balconies": d.get("balconies"),
                    "car_parking": 1 if parking in ("BOTH", "FOUR_WHEELER") else 0 if parking in ("NONE", "TWO_WHEELER") else None,
                    "age": d.get("propertyAge"),
                    "facing": d.get("facingDesc") or _FACING.get(str(d.get("facing") or "")),
                    "ownership": str(d.get("ownershipType") or "").replace("_", " ").title() or None,
                    "furnishing": str(d.get("furnishingDesc") or d.get("furnishing") or "").replace("_", " ").title() or None,
                    "society": d.get("society") or d.get("buildingName"),
                    "bathrooms": d.get("bathroom"),
                    "address": d.get("completeStreetName") or d.get("address"),
                    "latitude": d.get("latitude"),
                    "longitude": d.get("longitude"),
                    "status": {"READY": "Ready to Move", "UNDER_CONSTRUCTION": "Under Construction"}.get(str(d.get("state") or "").upper(), d.get("state")),
                    "pincode": d.get("pinCode") or d.get("pincode"),
                }
    return {}


def _normalise(pairs: dict[str, str]) -> dict[str, Any]:
    low = {re.sub(r"\s+", " ", k).lower().strip(" :"): v for k, v in pairs.items() if v}
    out: dict[str, Any] = {}
    for key, names in SYNONYMS.items():
        for n in names:
            v = low.get(n.lower())
            if v:
                out[key] = v
                break
    return out


_PIN_RE = re.compile(r"\b(5\d{5})\b")


def _pincode(html: str, details: dict, rec: Record) -> str | None:
    for node in ld_items(html, "PostalAddress", "Place", "Apartment", "House", "Residence"):
        addr = node.get("address")
        pc = node.get("postalCode") or (addr.get("postalCode") if isinstance(addr, dict) else None)
        if pc and _PIN_RE.fullmatch(str(pc)):
            return str(pc)
    for text in (details.get("address"), rec.address):
        m = _PIN_RE.search(str(text or ""))
        if m:
            return m.group(1)
    # "Hyderabad, Telangana 500032" style address lines in the page text
    m = re.search(r"(?:Hyderabad|Telangana|Secunderabad)[^<>\d]{0,40}?\b(5\d{5})\b", html)
    return m.group(1) if m else None


def fetch_details(rec: Record, fetcher: Fetcher, render: bool = False) -> dict[str, Any]:
    """Load ``rec.url`` and return normalised detail facts (``{}`` if none)."""
    if not rec.url:
        return {}
    resp = fetcher.render(rec.url) if render else fetcher.get(rec.url)
    return parse_details(resp.text, rec)


def parse_details(html: str, rec: Record) -> dict[str, Any]:
    if rec.source == "nobroker":
        d = _nobroker(html, rec)
    else:
        soup = BeautifulSoup(html, "lxml")
        fn = SITE_PAIRS.get(rec.source)
        pairs = fn(soup) if fn else {}
        if not pairs:  # unknown or changed layout: generic label scan
            pairs = label_values(soup, _ALL_LABELS)
        d = _normalise(pairs)
    d = {k: v for k, v in d.items() if v not in (None, "")}
    pc = d.get("pincode") or _pincode(html, d, rec)
    if pc:
        d["pincode"] = str(pc)
    return d
