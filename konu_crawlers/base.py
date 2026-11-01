"""Crawler base class and registry."""

from __future__ import annotations

import logging
from typing import ClassVar, Iterator

from .http import Fetcher
from .models import Record

log = logging.getLogger(__name__)

REGISTRY: dict[str, type["Crawler"]] = {}


def register(cls: type["Crawler"]) -> type["Crawler"]:
    REGISTRY[cls.name] = cls
    return cls


class Crawler:
    """Subclasses implement :meth:`crawl` and yield :class:`Record` objects.

    ``transactions`` lists the values ``--type`` accepts for the source
    (``sale``, ``rent``, ``projects``, ``agents``, ``layouts`` ...). The first
    entry is the default.
    """

    name: ClassVar[str] = ""
    title: ClassVar[str] = ""
    homepage: ClassVar[str] = ""
    transactions: ClassVar[tuple[str, ...]] = ("sale", "rent")
    #: True when the source's search needs a human to type a captcha.
    interactive: ClassVar[bool] = False
    #: True when the source only serves listings to a JavaScript browser,
    #: so the crawler uses Playwright by default.
    needs_browser: ClassVar[bool] = False

    def __init__(self, fetcher: Fetcher | None = None, render: bool | None = None) -> None:
        self.fetcher = fetcher or Fetcher()
        self.render = self.needs_browser if render is None else render

    def crawl(
        self,
        city: str = "hyderabad",
        transaction: str | None = None,
        max_pages: int = 1,
        **options: str,
    ) -> Iterator[Record]:
        raise NotImplementedError

    # helpers --------------------------------------------------------------
    def page(self, url: str, **kw):
        """GET (or render, in browser mode) a page."""
        if self.render:
            return self.fetcher.render(url, **{k: v for k, v in kw.items() if k in ("wait_ms", "wait_for", "scrolls")})
        return self.fetcher.get(url, **{k: v for k, v in kw.items() if k not in ("wait_ms", "wait_for", "scrolls")})

    def resolve_transaction(self, transaction: str | None) -> str:
        t = transaction or self.transactions[0]
        if t not in self.transactions:
            raise ValueError(f"{self.name}: --type must be one of {', '.join(self.transactions)} (got {t!r})")
        return t


def slug(city: str) -> str:
    return city.strip().lower().replace(" ", "-")
