"""99acres – 99acres.com (buy, rent, new projects, agents).

Search pages (``property-for-sale-in-<city>-ffid``) server-render their
results into ``window.__initialData__``; each property dict carries
``PROP_ID``, ``PRICE``, ``SUPERBUILTUP_SQFT``, ``BEDROOM_NUM``,
``LOCALITY``, ``MAP_DETAILS`` ... Pagination: ``-ffid-page-N``.

Note: 99acres sits behind Akamai Bot Manager and returns 403 to non-Indian
datacenter IPs. Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

import re

from ..base import register
from ..models import Record
from ._common import map_generic
from ._portal import PortalCrawler

BASE = "https://www.99acres.com"


@register
class Acres99(PortalCrawler):
    name = "99acres"
    title = "99acres"
    homepage = BASE
    transactions = ("sale", "rent", "projects", "agents")
    urls = {
        "sale": BASE + "/property-for-sale-in-{city}-ffid",
        "rent": BASE + "/property-for-rent-in-{city}-ffid",
        "projects": BASE + "/new-projects-in-{city}-ffid",
        "agents": BASE + "/real-estate-agents-in-{city}-ffid",
    }
    page_url = staticmethod(lambda first, n: re.sub(r"-ffid(?:-page-\d+)?", f"-ffid-page-{n}", first, count=1))
    json_predicate = staticmethod(
        lambda d: ("PROP_ID" in d or "SPID" in d or "PROJECT_ID" in d or "BROKER_ID" in d)
        and any(k in d for k in ("PRICE", "MIN_PRICE", "PROP_HEADING", "PROP_NAME", "PROJECT_NAME", "BROKER_NAME", "COMPANY_NAME"))
    )

    def json_key(self, d: dict) -> object:
        return d.get("PROP_ID") or d.get("SPID") or d.get("PROJECT_ID") or d.get("BROKER_ID")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        kind = {"projects": "project", "agents": "agent"}.get(transaction, "listing")
        rec = map_generic(self.name, d, BASE + "/", transaction if kind == "listing" else None, city, kind=kind)
        if kind == "agent":
            rec.title = d.get("BROKER_NAME") or d.get("COMPANY_NAME") or rec.title
            rec.contact_name = d.get("CONTACT_NAME") or rec.contact_name
        if kind == "project":
            rec.title = d.get("PROJECT_NAME") or d.get("PROP_NAME") or rec.title
            rec.project_name = rec.title
        return rec
