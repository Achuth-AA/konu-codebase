"""GHMC – ghmc.gov.in (building permissions within GHMC limits).

GHMC publishes building-permission registers / approved-plan lists as HTML
tables linked from its site (new applications are processed on TG-bPASS).
This crawler discovers those pages from the GHMC home page by link text
("building permission", "approved plans", "occupancy", "TG-bPASS") and emits
every data table row as a ``permission`` record (all columns kept in
``raw``). ``--opt url=<page>`` crawls one specific register page.

Note: GHMC's F5/Volterra WAF rejects non-Indian IPs ("The requested URL was
rejected"). Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ._portal import TableDiscoveryCrawler


@register
class GHMC(TableDiscoveryCrawler):
    name = "ghmc"
    title = "GHMC (building permissions)"
    homepage = "https://www.ghmc.gov.in/"
    transactions = ("permissions",)
    record_kind = {"permissions": "permission"}
    discover = {"permissions": [r"building\s*permission", r"approved\s*(building\s*)?plans?", r"occupancy", r"b\s*-?\s*pass", r"\bBPS\b"]}
