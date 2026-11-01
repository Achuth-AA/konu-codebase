"""Mappers shared by the listing-portal crawlers."""

from __future__ import annotations

import html as _html
import re
from typing import Any, Callable, Iterable

from bs4 import BeautifulSoup, Tag

from ..extract import (
    clean_text,
    dedupe,
    find_cards,
    find_dicts,
    first,
    iter_json_blobs,
    ld_items,
    parse_bhk,
    parse_inr,
    parse_number,
)
from ..http import absolute
from ..models import FIELDS, Record

LISTING_LD_TYPES = (
    "Apartment",
    "House",
    "SingleFamilyResidence",
    "Residence",
    "Accommodation",
    "RealEstateListing",
    "ApartmentComplex",
    "Place",
    "Product",
    "Offer",
    "RentAction",
    "SellAction",
)
AGENT_LD_TYPES = ("RealEstateAgent", "LocalBusiness", "Organization", "ProfessionalService")

_AREA_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(sq\.?\s*ft|sqft|sq\.?\s*feet|square feet|sft|sq\.?\s*yd|sq\.?\s*yards?|sq\.?\s*m|acres?)", re.I)
_PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?\s*(?:cr|crore|crores|l|lac|lacs|lakh|lakhs|k)?)\b|([\d.]+\s*(?:cr|crore|crores|lac|lacs|lakh|lakhs))\b", re.I)
_UNIT_TO_SQFT = {"sqyd": 9.0, "sqyard": 9.0, "sqyards": 9.0, "sqm": 10.7639, "acre": 43560.0, "acres": 43560.0}


def area_to_sqft(text: Any) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _AREA_RE.search(str(text))
    if not m:
        return parse_number(text)
    val = float(m.group(1).replace(",", ""))
    unit = re.sub(r"[\s.]", "", m.group(2).lower()).replace("feet", "ft").replace("square", "sq")
    return round(val * _UNIT_TO_SQFT.get(unit, 1.0), 2)


def price_from_text(text: str | None) -> tuple[float | None, float | None, str | None]:
    """'₹ 1.35 Cr - 1.86 Cr' → (13500000, 18600000, '₹ 1.35 Cr - 1.86 Cr')."""
    if not text:
        return None, None, None
    found = [m.group(1) or m.group(2) for m in _PRICE_RE.finditer(text)]
    vals = [v for v in (parse_inr(f) for f in found) if v]
    if not vals:
        return None, None, None
    raw = text if len(text) < 80 else found[0]
    return vals[0], (vals[1] if len(vals) > 1 and vals[1] > vals[0] else None), clean_text(raw)


def _geo(node: dict) -> tuple[float | None, float | None]:
    g = node.get("geo") or first(node, "location.geo", "object.geo", "itemOffered.geo") or {}
    if isinstance(g, dict):
        return parse_number(g.get("latitude")), parse_number(g.get("longitude"))
    return None, None


def _addr(node: dict) -> dict:
    a = node.get("address") or first(node, "location.address", "object.address", "itemOffered.address") or {}
    return a if isinstance(a, dict) else {"streetAddress": a}


def record_from_ld(source: str, node: dict, transaction: str | None = None, city: str | None = None) -> Record | None:
    """Map a schema.org JSON-LD node (Apartment, Product, RentAction, ...) to a Record."""
    t = node.get("@type")
    types = [x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]
    obj = node.get("object") if isinstance(node.get("object"), dict) else {}
    item = node.get("itemOffered") if isinstance(node.get("itemOffered"), dict) else {}
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    offers = offers if isinstance(offers, dict) else {}
    pspec = node.get("priceSpecification") if isinstance(node.get("priceSpecification"), dict) else {}
    url = first(node, "url", "URL", "@id", "mainEntityOfPage") or first(offers, "url")
    title = clean_text(first(node, "name") or first(obj, "name") or first(item, "name"))
    if not (url or title):
        return None
    price = parse_inr(first(node, "price") or first(offers, "price", "lowPrice") or first(pspec, "price", "minPrice"))
    price_max = parse_inr(first(offers, "highPrice") or first(pspec, "maxPrice"))
    floor = node.get("floorSize") or first(item, "floorSize")
    if isinstance(floor, dict):
        floor = floor.get("value")
    addr = _addr(node)
    lat, lon = _geo(node)
    if "rentaction" in types:
        transaction = "rent"
    elif "sellaction" in types:
        transaction = "sale"
    elif title and transaction is None:
        low = title.lower()
        transaction = "rent" if "for rent" in low else "sale" if ("for sale" in low or "resale" in low) else None
    agent = node.get("agent") or node.get("landlord") or node.get("seller") or offers.get("seller") or {}
    rec = Record(
        source=source,
        kind="project" if any(x in types for x in ("apartmentcomplex", "product")) and "apartment" not in types else "listing",
        source_id=str(first(node, "identifier.value", "mpn", "sku", "productID") or "") or _id_from_url(url),
        url=url if isinstance(url, str) else None,
        title=title,
        transaction=transaction,
        property_type=next((x for x in types if x not in ("product", "offer", "rentaction", "sellaction")), None),
        price=price,
        price_max=price_max,
        area_sqft=area_to_sqft(floor),
        bedrooms=parse_number(first(node, "numberOfRooms", "numberOfBedrooms") or first(item, "numberOfRooms")) or parse_bhk(title),
        bathrooms=parse_number(first(node, "numberOfBathroomsTotal", "numberOfBathrooms")),
        locality=_locality(addr, city),
        city=city or clean_text(first(addr, "addressLocality")),
        address=clean_text(", ".join(str(v) for k, v in addr.items() if not k.startswith("@") and isinstance(v, str))),
        latitude=lat,
        longitude=lon,
        project_name=clean_text(first(addr, "name")),
        contact_name=clean_text(agent.get("name")) if isinstance(agent, dict) else None,
        image_url=_first_str(first(node, "image", "photo")),
        description=clean_text(_html.unescape(str(node.get("description") or "")))[:1000] if node.get("description") else None,
        posted_at=first(node, "startTime", "datePosted"),
        raw=node,
    )
    return rec


def _locality(addr: dict, city: str | None) -> str | None:
    """Sites disagree on which PostalAddress field holds the locality."""
    c = (city or "").strip().lower().replace("-", " ")
    for k in ("addressLocality", "streetAddress"):
        v = clean_text(addr.get(k))
        if not v or not isinstance(v, str):
            continue
        if c:
            v = re.sub(rf",\s*{re.escape(c)}\s*$", "", v, flags=re.I).strip()
            if v.lower() == c:
                continue
        if v.count(",") >= 2:  # a full street address, not a locality
            continue
        return v
    return None


def _first_str(v: Any) -> str | None:
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get("url") or v.get("contentUrl")
    return v if isinstance(v, str) else None


def _id_from_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    m = re.search(r"(\d{5,})(?:/?(?:\?|#|$|/detail|-ad\b))", url) or re.search(r"(\d{5,})", url)
    return m.group(1) if m else None


def records_from_ld(source: str, html: str, transaction: str | None = None, city: str | None = None,
                    types: Iterable[str] = LISTING_LD_TYPES) -> list[Record]:
    nodes = ld_items(html, *types)
    # Sites often describe one listing with several nodes sharing a URL
    # (Apartment + RentAction, Product + Offer); merge them field by field.
    merged: dict[str, Record] = {}
    for n in nodes:
        rec = record_from_ld(source, n, transaction, city)
        if not rec:
            continue
        key = rec.url or rec.title or ""
        cur = merged.get(key)
        if cur is None:
            merged[key] = rec
            continue
        for f in FIELDS:
            if f in ("raw", "scraped_at"):
                continue
            if getattr(cur, f) in (None, "") and getattr(rec, f) not in (None, ""):
                setattr(cur, f, getattr(rec, f))
        if rec.transaction in ("rent", "sale"):
            cur.transaction = rec.transaction
        cur.raw = {**cur.raw, **{"@merged": cur.raw.get("@merged", []) + [n]}}
    return [r for r in merged.values() if r.price or r.area_sqft or r.bedrooms or r.latitude]


def record_from_card(source: str, el: Tag, base_url: str, transaction: str | None = None, city: str | None = None) -> Record | None:
    """Last-resort mapping from a rendered listing card's text."""
    text = el.get_text(" | ", strip=True)
    link = el if el.name == "a" and el.get("href") else el.find("a", href=True)
    url = absolute(base_url, link.get("href")) if link else None
    heading = el.find(["h1", "h2", "h3", "h4"])
    title = clean_text(heading.get_text(" ", strip=True)) if heading else clean_text(link.get_text(" ", strip=True)) if link else None
    price, price_max, price_text = price_from_text(text.replace("|", " "))
    am = _AREA_RE.search(text.replace("|", " "))
    if not (title or url):
        return None
    img = el.find("img")
    return Record(
        source=source,
        kind="listing",
        source_id=el.get("id") or el.get("data-id") or el.get("data-listing-id") or _id_from_url(url),
        url=url,
        title=title,
        transaction=transaction,
        price=price,
        price_max=price_max,
        price_text=price_text,
        area_sqft=area_to_sqft(am.group(0)) if am else None,
        area_text=am.group(0) if am else None,
        bedrooms=parse_bhk(title) or parse_bhk(text),
        city=city,
        image_url=(img.get("data-src") or img.get("src")) if img else None,
        raw={"card_text": text[:2000]},
    )


def records_from_cards(source: str, html: str, base_url: str, transaction: str | None = None,
                       city: str | None = None, selector: str | None = None) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(selector) if selector else find_cards(soup)
    out = [r for r in (record_from_card(source, c, base_url, transaction, city) for c in cards) if r]
    return dedupe(out, key=lambda r: r.url or r.title)  # type: ignore[arg-type]


def records_from_json(source: str, html: str, predicate: Callable[[dict], bool],
                      mapper: Callable[[dict], Record | None], key: Callable[[dict], Any]) -> list[Record]:
    dicts: list[dict] = []
    for _name, blob in iter_json_blobs(html):
        dicts.extend(find_dicts(blob, predicate))
    dicts = dedupe(dicts, key=key)
    return [r for r in (mapper(d) for d in dicts) if r]


def best_effort(*strategies: Callable[[], list[Record]]) -> list[Record]:
    """Return the first strategy that yields records."""
    for s in strategies:
        recs = s()
        if recs:
            return recs
    return []


def text_of(el: Tag | None, sel: str | None = None) -> str | None:
    if el is None:
        return None
    t = el.select_one(sel) if sel else el
    return clean_text(t.get_text(" ", strip=True)) if t else None


def map_generic(source: str, d: dict, base_url: str, transaction: str | None = None, city: str | None = None,
                kind: str = "listing") -> Record:
    """Map a listing dict whose exact schema we only partly know.

    Tries the field spellings used across Indian portals (camelCase,
    UPPER_SNAKE, nested ``price.value`` ...). Everything is kept in ``raw``.
    """
    def g(*keys: str) -> Any:
        return first(d, *keys)

    price_raw = g("price.value.raw", "price.value", "price.raw", "price.amount", "displayPrice.value", "PRICE", "MIN_PRICE",
                  "minPrice", "price", "rent", "expectedPrice", "amount", "salePrice")
    if isinstance(price_raw, dict):
        price_raw = first(price_raw, "raw", "value", "amount", "min")
    price_text = g("price.value.display", "price.display", "displayPrice.displayValue", "formattedPrice", "PRICE_DISPLAY",
                   "priceText", "displayPrice", "PRICE")
    url = g("url", "URL", "PROP_DETAILS_URL", "detailUrl", "listingUrl", "propertyUrl", "inventoryUrl", "seoUrl", "link", "href")
    lat = g("latitude", "lat", "MAP_DETAILS.LATITUDE", "coords.0", "location.lat", "geo.latitude", "locations.0.lat")
    lon = g("longitude", "lng", "lon", "MAP_DETAILS.LONGITUDE", "coords.1", "location.lng", "location.lon", "geo.longitude", "locations.0.lon")
    coords = d.get("coords")
    if isinstance(coords, list) and len(coords) == 2:
        lat, lon = coords
    area = g("SUPERBUILTUP_SQFT", "BUILTUP_SQFT", "CARPET_SQFT", "AREA", "superBuiltUpArea", "builtUpArea", "carpetArea", "size",
             "area", "propertySize", "floorArea", "covered_area")
    title = g("PROP_HEADING", "title", "propertyTitle", "name", "heading", "PROP_NAME", "projectName", "subtitle")
    locality = g("LOCALITY", "localityName", "locality.name", "locality", "locations_resolved.SUBLOCALITY_LEVEL_1_name",
                 "locations_resolved.ADMIN_LEVEL_3_name", "address.locality", "region.name", "subLocality", "location")
    if isinstance(locality, dict):
        locality = first(locality, "name", "label")
    return Record(
        source=source,
        kind=kind,
        source_id=str(g("PROP_ID", "SPID", "listingId", "id", "ad_id", "propertyId", "projectId", "docid", "PROJECT_ID") or "") or None,
        url=absolute(base_url, url) if isinstance(url, str) else None,
        title=clean_text(title) if isinstance(title, str) else None,
        transaction=transaction,
        property_type=clean_text(g("PROPERTY_TYPE", "propertyType", "property_type", "unitType", "type")) if isinstance(g("PROPERTY_TYPE", "propertyType", "property_type", "unitType", "type"), str) else None,
        price=parse_inr(price_raw),
        price_max=parse_inr(g("MAX_PRICE", "maxPrice", "price.max")),
        price_text=clean_text(price_text) if isinstance(price_text, (str, int, float)) else None,
        area_sqft=area_to_sqft(area) if not isinstance(area, dict) else area_to_sqft(first(area, "value", "sqft")),
        bedrooms=parse_number(g("BEDROOM_NUM", "bedrooms", "bedroom", "bhk", "noOfBedrooms", "rooms")) or parse_bhk(title),
        bathrooms=parse_number(g("BATHROOM_NUM", "bathrooms", "bathroom", "noOfBathrooms")),
        furnishing=g("FURNISH", "furnishing", "furnishingStatus", "furnished"),
        locality=clean_text(locality) if isinstance(locality, str) else None,
        city=clean_text(g("CITY", "cityName", "city.name", "city")) if isinstance(g("CITY", "cityName", "city.name", "city"), str) else city,
        address=clean_text(g("address.address", "ADDRESS", "address", "fullAddress", "displayAddress")) if isinstance(g("address.address", "ADDRESS", "address", "fullAddress", "displayAddress"), str) else None,
        latitude=parse_number(lat),
        longitude=parse_number(lon),
        project_name=g("PROP_NAME", "projectName", "project.name", "societyName", "buildingName"),
        builder=g("BUILDER_NAME", "builderName", "builder.name", "developerName", "companyName"),
        posted_by=g("CLASS_LABEL", "postedBy", "sellerType", "userType", "listedBy", "user_type"),
        contact_name=g("CONTACT_NAME", "sellers.0.name", "seller.name", "agentName", "ownerName", "user.name"),
        status=g("AVAILABILITY", "possessionStatus", "constructionStatus", "status", "availability"),
        rera_id=g("RERA_ID", "reraId", "rera.id", "reraNumber"),
        posted_at=g("POSTING_DATE", "postedDate", "postedOn", "created_at", "createdAt", "display_date", "updatedAt"),
        image_url=_first_str(g("PHOTO_URL", "coverImage", "thumbImage", "image", "images", "imageUrl", "thumbnail", "photos")),
        description=clean_text(g("DESCRIPTION", "description", "desc"))[:1000] if isinstance(g("DESCRIPTION", "description", "desc"), str) else None,
        raw=d,
    )
