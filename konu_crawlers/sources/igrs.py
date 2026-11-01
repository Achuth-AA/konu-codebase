"""IGRS Telangana – registration.telangana.gov.in (market values, registration data).

The Registration & Stamps department publishes guideline market values and
registration statistics as HTML tables and district/mandal/village forms.
This crawler discovers those pages from the portal home page by link text
("market value", "unit rates", "statistics") and emits every data table row
(``market_value`` records, all columns in ``raw``).

For the form-driven market-value search pass the form fields as options,
e.g. ``--opt submit=1 --opt form_district=… --opt form_mandal=…
--opt form_village=…``; if the form shows a captcha you will be asked to
type it (it is never solved automatically). ``--opt url=<page>`` targets a
specific page.

Note: registration.telangana.gov.in resets connections from outside India.
Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ._portal import TableDiscoveryCrawler


@register
class IGRS(TableDiscoveryCrawler):
    name = "igrs"
    title = "IGRS Telangana (market values)"
    homepage = "https://registration.telangana.gov.in/"
    transactions = ("market_values", "statistics")
    interactive = True
    record_kind = {"market_values": "market_value", "statistics": "registration_stat"}
    discover = {
        "market_values": [r"market\s*value", r"unit\s*rates?", r"\bMV\b", r"basic\s*value"],
        "statistics": [r"statistic", r"revenue", r"documents?\s*registered", r"registrations?\s*report"],
    }
