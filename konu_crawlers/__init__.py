"""Crawlers for Hyderabad / Telangana real-estate sources.

>>> from konu_crawlers import get_crawler
>>> for rec in get_crawler("magicbricks").crawl(city="hyderabad", transaction="rent", max_pages=2):
...     print(rec.title, rec.price)
"""

from __future__ import annotations

from .base import REGISTRY, Crawler
from .http import BlockedError, CaptchaRequired, Fetcher, FetchError
from .models import Record
from . import sources  # noqa: F401  (registers every crawler)

__all__ = ["Crawler", "Fetcher", "Record", "BlockedError", "CaptchaRequired", "FetchError", "get_crawler", "REGISTRY"]


def get_crawler(name: str, fetcher: Fetcher | None = None, render: bool | None = None) -> Crawler:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown source {name!r}; available: {', '.join(sorted(REGISTRY))}") from None
    return cls(fetcher=fetcher, render=render)
