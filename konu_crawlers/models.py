"""Normalised record shape shared by every crawler."""

from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Any


@dataclasses.dataclass
class Record:
    """One crawled entity.

    ``kind`` distinguishes what the row describes:

    * ``listing``     – a single unit for sale/rent (portals, classifieds)
    * ``project``     – a development / new project (RERA, PropTiger, ...)
    * ``agent``       – a broker / real-estate agent / promoter
    * ``layout``      – an approved layout (HMDA)
    * ``permission``  – a building permission (HMDA, GHMC)
    * ``market_value``– a government guideline value row (IGRS)
    * ``land_record`` – a land-record (pahani / ROR) row (Bhu Bharati)

    Fields that do not apply are left as ``None``; everything the source
    returned is preserved verbatim in ``raw`` so nothing is lost by the
    normalisation.
    """

    source: str
    kind: str
    source_id: str | None = None
    url: str | None = None
    title: str | None = None
    transaction: str | None = None  # sale | rent | None
    property_type: str | None = None
    price: float | None = None  # INR (monthly rent for rentals)
    price_max: float | None = None  # upper bound when the source gives a range
    price_text: str | None = None
    area_sqft: float | None = None
    area_text: str | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    furnishing: str | None = None
    locality: str | None = None
    city: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    project_name: str | None = None
    builder: str | None = None
    posted_by: str | None = None  # Owner / Agent / Builder
    contact_name: str | None = None
    status: str | None = None  # ready / under construction / approved ...
    rera_id: str | None = None
    posted_at: str | None = None
    image_url: str | None = None
    description: str | None = None
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)
    scraped_at: str = dataclasses.field(
        default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self, include_raw: bool = True) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d


FIELDS = [f.name for f in dataclasses.fields(Record)]
