"""TS-RERA – rera.telangana.gov.in (registered projects, promoters, agents).

The public search lives at ``rerait.telangana.gov.in/SearchList/Search``.
It is an ASP.NET MVC form protected by an anti-forgery token **and an image
captcha**. This crawler does not solve the captcha: it downloads the image
and asks the operator to type it (see :mod:`konu_crawlers.captcha`), then
submits the same form a browser would and parses the result grid. The
server keeps the search in session, so later pages are fetched by bumping
``pageTraverse`` (re-prompting only if the portal asks for a new captcha).

``--type projects`` searches promoters/projects, ``--type agents`` agents.
Options: ``--district NAME`` (default: the city, e.g. Hyderabad; ``all``
for no filter), ``--project``, ``--promoter``, ``--agent-name``, ``--cert-no``,
``--captcha TEXT``.

The host serves an incomplete TLS chain; the bundled emSign intermediate
fixes verification (see ``konu_crawlers/certs``).
"""

from __future__ import annotations

import re
from typing import Iterator

from bs4 import BeautifulSoup

from ..base import Crawler, register
from ..captcha import ask_human
from ..extract import clean_text, form_fields, largest_table, table_rows
from ..http import CaptchaRequired, FetchError, absolute
from ..models import Record

PORTAL = "https://rerait.telangana.gov.in"
SEARCH = PORTAL + "/SearchList/Search"
CAPTCHA = PORTAL + "/SearchList/SearchCaptcha"

_COLS = {
    "title": ("project name", "project", "agent name", "name"),
    "builder": ("promoter name", "promoter", "organisation", "organization"),
    "rera_id": ("certificate no", "registration no", "reg no", "certificate", "rera"),
    "address": ("address", "location", "district", "project address"),
    "status": ("status", "project status"),
    "posted_at": ("approved on", "registration date", "date", "approved date"),
    "completion": ("completion date", "proposed date of completion", "end date"),
}


def _pick(row: dict, names: tuple[str, ...]) -> str | None:
    low = {k.lower().strip(): v for k, v in row.items() if not k.endswith("__href")}
    for n in names:
        for k, v in low.items():
            if k == n or (len(n) > 4 and n in k):
                if v:
                    return v
    return None


def parse_results(html: str, kind: str) -> list[Record]:
    soup = BeautifulSoup(html, "lxml")
    table = largest_table(soup)
    if table is None:
        return []
    out = []
    for row in table_rows(table):
        title = _pick(row, _COLS["title"])
        if not title:
            continue
        href = next((v for k, v in row.items() if k.endswith("__href") and "javascript" not in v), None)
        out.append(
            Record(
                source="ts_rera",
                kind="agent" if kind == "agents" else "project",
                source_id=_pick(row, _COLS["rera_id"]),
                url=absolute(PORTAL + "/", href) if href else None,
                title=clean_text(title),
                builder=_pick(row, _COLS["builder"]),
                address=_pick(row, _COLS["address"]),
                status=_pick(row, _COLS["status"]),
                rera_id=_pick(row, _COLS["rera_id"]),
                posted_at=_pick(row, _COLS["posted_at"]),
                project_name=clean_text(title) if kind != "agents" else None,
                raw=row,
            )
        )
    return out


def _captcha_rejected(html: str) -> bool:
    return bool(re.search(r"(invalid|incorrect|wrong)\s+captcha|captcha\s+(is\s+)?(invalid|incorrect|wrong|required)", html, re.I))


@register
class TSRera(Crawler):
    name = "ts_rera"
    title = "TS-RERA (Telangana RERA)"
    homepage = "https://rera.telangana.gov.in"
    transactions = ("projects", "agents")
    interactive = True

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        form_html = self.fetcher.get(SEARCH).text
        soup = BeautifulSoup(form_html, "lxml")
        data = form_fields(soup, "frmSearchList")
        if "__RequestVerificationToken" not in data:
            raise FetchError(f"{SEARCH}: search form not found (portal layout changed?)")
        district = options.get("district", city)
        if district and district.lower() != "all":
            sel = soup.find("select", id="District")
            opts = {o.get_text(strip=True).lower(): o.get("value") for o in sel.find_all("option")} if sel else {}
            match = next((v for k, v in opts.items() if v and k.startswith(district.lower())), None)
            if not match:
                raise ValueError(f"ts_rera: unknown district {district!r}; choose one of {sorted(k for k in opts if k)}")
            data["District"] = match
        data.update(
            {
                "Type": "Agent" if t == "agents" else "Promoter",
                "Project": options.get("project", ""),
                "Promoter": options.get("promoter", ""),
                "AgentName": options.get("agent_name", ""),
                "CertiNo": options.get("cert_no", ""),
                "Command": "Search",
            }
        )
        provided = options.get("captcha")
        for page in range(max_pages):
            data["pageTraverse"] = str(page + 1) if page else data.get("pageTraverse", "0")
            for attempt in range(3):
                img = self.fetcher.get(CAPTCHA, check=False)
                data["Captcha"] = ask_human(img.content, "ts_rera", provided=provided if attempt == 0 else None)
                html = self.fetcher.post(SEARCH, data=data).text
                if not _captcha_rejected(html) and "ShowErrorPage" not in html[:20000]:
                    break
                provided = None
            else:
                raise CaptchaRequired("ts_rera: captcha rejected three times")
            recs = parse_results(html, t)
            if not recs:
                break
            yield from recs
            # Refresh the anti-forgery token for the next page.
            nxt = BeautifulSoup(html, "lxml")
            data.update({k: v for k, v in form_fields(nxt, "frmSearchList").items() if k == "__RequestVerificationToken"})
