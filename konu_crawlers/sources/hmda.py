"""HMDA – hmda.gov.in (approved layouts and building permissions).

HMDA's Planning Department publishes its permission registers as public
ASP.NET reports on ``masterplan.hmda.gov.in``:

* ``planning/LO.aspx``          – layout permissions (core HMDA area)
* ``planning/ExtLO.aspx``       – layout permissions (extended HUDA area)
* ``planning/Bpermission.aspx`` – building permissions (core area)
* ``planning/ExtBP.aspx``       – building permissions (extended area)

Each report returns its full register in one ``GridView1`` table (thousands
of rows). ``--year YYYY`` posts the page's own year filter instead.

The host serves an incomplete TLS chain (missing the emSign intermediate);
:func:`konu_crawlers.http.ca_bundle_path` supplies it.
"""

from __future__ import annotations

import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Crawler, register
from ..extract import form_fields, largest_table, parse_number, table_rows
from ..http import FetchError
from ..models import Record

BASE = "https://masterplan.hmda.gov.in/planning/"

REPORTS = {
    "layouts": [("LO.aspx", "core"), ("ExtLO.aspx", "extended")],
    "permissions": [("Bpermission.aspx", "core"), ("ExtBP.aspx", "extended")],
}


def _get(row: dict, *names: str) -> str | None:
    """Look a column up by any of several header spellings (case-insensitive)."""
    low = {k.lower().replace(" ", "_"): v for k, v in row.items()}
    for n in names:
        v = low.get(n.lower().replace(" ", "_"))
        if v:
            return v
    return None


def _date(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", s)
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else s


_VILLAGE_RE = re.compile(r"([A-Z][A-Za-z.]+(?:\s[A-Z][A-Za-z.]+)?)\s*(?:\(V\)|Village)")


def _village(text: str | None) -> str | None:
    m = _VILLAGE_RE.search(text or "")
    return m.group(1) if m else None


def _extent_sqft(extent: str | None) -> float | None:
    """'5766.33 Sq.Mts.' / 'Ac.4-36.22 Gts or 19852.50 Sq.mtrs' → sq ft."""
    if not extent:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*Sq\.?\s*m", extent, re.I)
    if m:
        return round(float(m.group(1).replace(",", "")) * 10.7639, 1)
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*Sq\.?\s*(?:yd|yards)", extent, re.I)
    if m:
        return round(float(m.group(1).replace(",", "")) * 9, 1)
    # "Ac.4-36.22 Gts" = 4 acres 36.22 guntas (1 gunta = 1089 sq ft)
    m = re.search(r"Ac\.?\s*(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?", extent, re.I)
    if m:
        try:
            return round(float(m.group(1)) * 43560 + float(m.group(2) or 0) * 1089, 1)
        except ValueError:
            return None
    return None


def parse_report(html: str, report: str, area: str, kind: str) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="GridView1") or largest_table(soup)
    if table is None:
        return []
    out = []
    for row in table_rows(table):
        file_no = _get(row, "File_no", "File No")
        if not file_no:
            continue
        if kind == "layout":
            out.append(
                Record(
                    source="hmda",
                    kind="layout",
                    source_id=file_no,
                    url=BASE + report,
                    title=_get(row, "Village Synos", "Subject"),
                    property_type="layout",
                    area_sqft=_extent_sqft(_get(row, "Extent")),
                    area_text=_get(row, "Extent"),
                    locality=_village(_get(row, "Village Synos", "Subject")),
                    city="hyderabad",
                    address=_get(row, "Village Synos"),
                    contact_name=_get(row, "name", "Name_Address"),
                    status="final layout issued" if _get(row, "layout_issued") else "draft layout issued" if _get(row, "Draft_layout_issued") else None,
                    posted_at=_date(_get(row, "layout_issued") or _get(row, "Draft_layout_issued")),
                    description=_get(row, "Remarks"),
                    raw={**row, "report": report, "area": area, "serial": parse_number(_get(row, "S.No"))},
                )
            )
        else:
            out.append(
                Record(
                    source="hmda",
                    kind="permission",
                    source_id=file_no,
                    url=BASE + report,
                    title=_get(row, "Subject"),
                    property_type=_get(row, "Type Of Permission"),
                    locality=_village(_get(row, "Subject")),
                    city="hyderabad",
                    address=_get(row, "Subject"),
                    contact_name=_get(row, "Name_Address"),
                    status="released",
                    posted_at=_date(_get(row, "Released on")),
                    raw={**row, "report": report, "area": area},
                )
            )
    return out


@register
class HMDA(Crawler):
    name = "hmda"
    title = "HMDA (layouts & building permissions)"
    homepage = "https://www.hmda.gov.in"
    transactions = ("layouts", "permissions")

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        kind = "layout" if t == "layouts" else "permission"
        year = options.get("year")
        only_area = options.get("area")  # core | extended
        for report, area in REPORTS[t]:
            if only_area and area != only_area:
                continue
            url = BASE + report
            resp = self.fetcher.get(url)
            html = resp.text
            if year:
                soup = BeautifulSoup(html, "lxml")
                data = form_fields(soup)
                if "DDL1_Year" not in data:
                    raise FetchError(f"{url}: year filter not found on page")
                data["DDL1_Year"] = str(year)
                data["__EVENTTARGET"] = "DDL1_Year"
                data["__EVENTARGUMENT"] = ""
                html = self.fetcher.post(url, data=data).text
            yield from parse_report(html, report, area, kind)
