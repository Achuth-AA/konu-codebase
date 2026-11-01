"""Bhu Bharati (formerly Dharani) – bhubharati.telangana.gov.in (land records).

Land-record lookups (pahani / ROR, land status, prohibited-property list)
are district → mandal → village → survey-number forms protected by a
captcha. This crawler discovers the public lookup pages by link text, and
for a lookup you pass the form fields as options, e.g. ``--opt submit=1
--opt form_district=… --opt form_mandal=… --opt form_village=…
--opt form_surveyNo=…``. The captcha is shown to you to type; it is never
solved automatically. Every result table row becomes a ``land_record``.

Note: bhubharati.telangana.gov.in resets connections from outside India.
Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ._portal import TableDiscoveryCrawler


@register
class BhuBharati(TableDiscoveryCrawler):
    name = "bhubharati"
    title = "Bhu Bharati (land records)"
    homepage = "https://bhubharati.telangana.gov.in/"
    transactions = ("land_records", "prohibited")
    interactive = True
    record_kind = {"land_records": "land_record", "prohibited": "land_record"}
    discover = {
        "land_records": [r"land\s*(details|status)", r"pahani", r"\bROR\b", r"record\s*of\s*rights", r"village\s*map"],
        "prohibited": [r"prohibited", r"22\s*\(?a\)?", r"restricted\s*land"],
    }
