"""Crawl flows (pagination, delegation, captcha, forms) with a fake fetcher.

Pages for the geo-blocked portals are small synthetic documents shaped like
each site's embedded state (we could not capture them from outside India);
they pin down the extraction path, not the live markup.
"""

import json

import pytest

from konu_crawlers import REGISTRY, get_crawler
from konu_crawlers.cli import main
from konu_crawlers.extract import iter_json_blobs, parse_inr, scan_balanced
from konu_crawlers.http import BlockedError, CaptchaRequired, Fetcher, Response

from .conftest import FakeFetcher, load


def test_all_20_sources_registered():
    assert len(REGISTRY) == 20
    assert {"ts_rera", "hmda", "ghmc", "igrs", "bhubharati", "nobroker", "magicbricks", "99acres", "housing", "squareyards",
            "makaan", "proptiger", "roofandfloor", "realestateindia", "indiaproperty", "commonfloor", "olx", "quikr", "sulekha", "justdial"} == set(REGISTRY)


@pytest.mark.parametrize("s,v", [("₹1.25 Cr", 12_500_000), ("45 Lac", 4_500_000), ("25,000", 25_000), (9500000, 9_500_000), ("Rs. 2.5 Lakhs", 250_000)])
def test_parse_inr(s, v):
    assert parse_inr(s) == v


def test_scan_balanced_handles_strings():
    s = 'x = {"a": "}{", "b": [1, {"c": "\\"]"}]}; y'
    start = s.index("{")
    assert json.loads(s[start:scan_balanced(s, start)])["b"][1]["c"] == '"]'


def test_iter_json_blobs_window_assignment_and_next_data():
    html = """<script>window.__INITIAL_STATE__ = {"items": [{"id": 1, "price": 10, "pad": "%s"}]};</script>
    <script id="__NEXT_DATA__" type="application/json">{"props": {"x": 1}}</script>""" % ("p" * 300)
    names = {n for n, _ in iter_json_blobs(html)}
    assert "__INITIAL_STATE__" in names and "__NEXT_DATA__" in names


@pytest.mark.parametrize("body,status", [
    ("<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD></HTML>", 403),
    ("<html><head><title>Security Alert</title></head></html>", 406),
    ("The requested URL was rejected. Please consult with your administrator.", 403),
])
def test_block_detection(body, status):
    with pytest.raises(BlockedError):
        Fetcher.check_blocked(Response("https://x.example/", status, body))


def test_geo_redirect_detection():
    with pytest.raises(BlockedError, match="country=US"):
        Fetcher.check_blocked(Response("https://property.sulekha.com/403?ref=x&country=US", 200, ""))


# ----------------------------------------------------------------- listing sites
def test_nobroker_walks_locality_pages():
    city = load("nobroker_sale.html")
    f = FakeFetcher({"https://www.nobroker.in/flats-for-sale-in-hyderabad_hyderabad": city,
                     "https://www.nobroker.in/flats-for-sale-in-*": city})
    recs = list(get_crawler("nobroker", fetcher=f).crawl("hyderabad", "sale", max_pages=3))
    assert len(f.calls) == 3
    assert f.calls[1][1].startswith("https://www.nobroker.in/flats-for-sale-in-") and f.calls[1][1].endswith("_hyderabad")
    assert len(recs) == len({r.source_id for r in recs}) >= 20  # same page thrice → deduplicated


def test_roofandfloor_paginates_through_fragments():
    f = FakeFetcher({
        "https://www.roofandfloor.com/hyderabad/flats-for-sale-in-hyderabad": load("roofandfloor_sale.html"),
        "https://www.roofandfloor.com/sale/flats/fetch_pages.php?loc=hyderabad&city=hyderabad&page=2&per_page=10": load("roofandfloor_fragment.html"),
    })
    recs = list(get_crawler("roofandfloor", fetcher=f).crawl("hyderabad", "sale", max_pages=3))
    assert len(recs) == 20  # page 3 is empty → stop
    assert len(f.calls) == 3


def test_magicbricks_stops_on_empty_page():
    f = FakeFetcher({"https://www.magicbricks.com/property-for-rent-in-hyderabad-pppfr": load("magicbricks_rent.html")})
    recs = list(get_crawler("magicbricks", fetcher=f).crawl("hyderabad", "rent", max_pages=5))
    assert len(recs) == 30 and len(f.calls) == 2


def test_99acres_initial_data():
    props = [{"PROP_ID": f"X{i}", "PROP_HEADING": f"{i} BHK Apartment for Sale in Kondapur", "PRICE": "1.2 Cr", "SUPERBUILTUP_SQFT": 1500 + i,
              "BEDROOM_NUM": str(i), "LOCALITY": "Kondapur", "CITY": "Hyderabad", "CLASS_LABEL": "Owner",
              "PROP_DETAILS_URL": f"/{i}-bhk-flat-spid-X{i}", "MAP_DETAILS": {"LATITUDE": "17.46", "LONGITUDE": "78.36"}} for i in (2, 3)]
    html = "<script>window.__initialData__ = %s;</script>" % json.dumps({"srp": {"pageData": {"properties": props}}})
    f = FakeFetcher({"https://www.99acres.com/property-for-sale-in-hyderabad-ffid": html})
    recs = list(get_crawler("99acres", fetcher=f).crawl("hyderabad", "sale", max_pages=2))
    assert [r.source_id for r in recs] == ["X2", "X3"]
    assert recs[0].price == 12_000_000 and recs[0].latitude == 17.46 and recs[0].posted_by == "Owner"
    assert recs[0].url == "https://www.99acres.com/2-bhk-flat-spid-X2"
    assert f.calls[1][1] == "https://www.99acres.com/property-for-sale-in-hyderabad-ffid-page-2"


def test_housing_initial_state():
    items = [{"listingId": 11, "title": "2 BHK Flat", "price": 4500000, "url": "/in/buy/resale/page/11", "coords": [17.4, 78.4],
              "propertyType": "Apartment", "address": {"address": "Gachibowli, Hyderabad"}, "pad": "x" * 200}]
    html = "<script>window.__INITIAL_STATE__ = %s</script>" % json.dumps({"searchResults": {"data": items}})
    f = FakeFetcher({"https://housing.com/in/buy/hyderabad/hyderabad": html})
    (r,) = list(get_crawler("housing", fetcher=f).crawl("hyderabad", "sale"))
    assert r.source_id == "11" and r.price == 4_500_000 and r.latitude == 17.4 and r.url == "https://housing.com/in/buy/resale/page/11"


def test_makaan_hands_over_to_housing_on_redirect():
    redirect = Response("https://www.makaan.com/x", 301, "", {"Location": "https://origin.makaan.com/housing-buyer-redirect"})
    items = [{"listingId": 7, "title": "3 BHK", "price": 9000000, "url": "/in/buy/resale/page/7", "pad": "x" * 200}]
    f = FakeFetcher({
        "https://www.makaan.com/hyderabad-residential-property/buy-property-in-hyderabad-city": redirect,
        "https://housing.com/in/buy/hyderabad/hyderabad": "<script>window.__INITIAL_STATE__ = %s</script>" % json.dumps({"d": items}),
    })
    (r,) = list(get_crawler("makaan", fetcher=f).crawl("hyderabad", "sale"))
    assert r.source == "makaan" and r.raw["served_by"] == "housing.com" and r.price == 9_000_000


def test_olx_app_state():
    ad = {"id": "1800", "ad_id": "1800", "title": "3 BHK flat for sale", "price": {"value": {"raw": 7500000, "display": "₹ 75,00,000"}},
          "locations_resolved": {"SUBLOCALITY_LEVEL_1_name": "Madhapur", "ADMIN_LEVEL_3_name": "Hyderabad"},
          "parameters": [{"key": "type", "formatted_value": "Apartments"}, {"key": "rooms", "formatted_value": "3"},
                         {"key": "sqft", "formatted_value": "1650"}, {"key": "furnishing", "formatted_value": "Semi-Furnished"}],
          "created_at": "2026-10-20T10:00:00+05:30"}
    html = "<script>window.__APP = %s;</script>" % json.dumps({"states": {"items": {"elements": {"1800": ad}}}})
    f = FakeFetcher({"https://www.olx.in/hyderabad_g4058526/for-sale-houses-apartments_c1725": html})
    (r,) = list(get_crawler("olx", fetcher=f).crawl("hyderabad", "sale"))
    assert (r.price, r.bedrooms, r.area_sqft, r.locality, r.property_type) == (7_500_000, 3, 1650, "Madhapur", "Apartments")


def test_olx_needs_location_id_for_unknown_city():
    with pytest.raises(ValueError, match="location-id"):
        list(get_crawler("olx", fetcher=FakeFetcher({})).crawl("warangal", "sale"))


def test_justdial_ld_agents():
    ld = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": {"@type": "RealEstateAgent", "name": "Sri Sai Realtors", "url": "/Hyderabad/Sri-Sai-Realtors/040P1",
                                       "address": {"@type": "PostalAddress", "addressLocality": "Kukatpally"},
                                       "aggregateRating": {"ratingValue": "4.3"}}}]}
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    f = FakeFetcher({"https://www.justdial.com/Hyderabad/Real-Estate-Agents/nct-10401947": html})
    (r,) = list(get_crawler("justdial", fetcher=f).crawl("hyderabad", "agents"))
    assert r.kind == "agent" and r.title == "Sri Sai Realtors" and r.status == "4.3"


# ----------------------------------------------------------------- government
def test_hmda_year_filter_posts_form():
    page = load("hmda_layouts.html")
    f = FakeFetcher({"https://masterplan.hmda.gov.in/planning/LO.aspx": page})
    recs = list(get_crawler("hmda", fetcher=f).crawl("hyderabad", "layouts", year="2023", area="core"))
    assert len(recs) == 25
    method, url, kw = f.calls[-1]
    assert method == "POST" and kw["data"]["DDL1_Year"] == "2023" and kw["data"]["__EVENTTARGET"] == "DDL1_Year"


RERA_FORM = """<form action="/SearchList/Search" id="frmSearchList" method="post">
<input name="__RequestVerificationToken" type="hidden" value="tok" />
<input id="pageTraverse" name="pageTraverse" type="hidden" value="0" />
<select id="District" name="District"><option value="">Select District</option><option value="25">Hyderabad</option></select>
<input id="Captcha" name="Captcha" type="text" value="" /></form>"""
RERA_RESULTS = """<table><tr><th>S.No</th><th>Project Name</th><th>Promoter Name</th><th>Certificate No</th><th>Status</th></tr>
<tr><td>1</td><td>Green Meadows</td><td>ABC Developers</td><td>P02400001234</td><td>Approved</td></tr>
<tr><td>2</td><td>Lake View</td><td>XYZ Infra</td><td>P02400005678</td><td>Approved</td></tr></table>"""


def test_ts_rera_uses_operator_captcha():
    f = FakeFetcher({
        "https://rerait.telangana.gov.in/SearchList/Search": lambda m, u, kw: RERA_FORM if m == "GET" else RERA_RESULTS,
        "https://rerait.telangana.gov.in/SearchList/SearchCaptcha": b"\x89PNG",
    })
    recs = list(get_crawler("ts_rera", fetcher=f).crawl("hyderabad", "projects", captcha="AB12C"))
    assert [r.rera_id for r in recs] == ["P02400001234", "P02400005678"]
    assert recs[0].builder == "ABC Developers" and recs[0].kind == "project"
    post = next(kw for m, u, kw in f.calls if m == "POST")
    assert post["data"]["Captcha"] == "AB12C" and post["data"]["District"] == "25" and post["data"]["Type"] == "Promoter"


def test_ts_rera_without_captcha_in_batch_mode(monkeypatch):
    monkeypatch.setenv("KONU_NONINTERACTIVE", "1")
    f = FakeFetcher({"https://rerait.telangana.gov.in/SearchList/Search": RERA_FORM,
                     "https://rerait.telangana.gov.in/SearchList/SearchCaptcha": b"\x89PNG"})
    with pytest.raises(CaptchaRequired):
        list(get_crawler("ts_rera", fetcher=f).crawl("hyderabad", "projects"))


def test_ghmc_discovers_register_pages():
    home = '<a href="/BuildingPermissions/List">Building Permissions</a><a href="/about">About</a>'
    table = "<table><tr><th>File No</th><th>Applicant</th><th>Location</th></tr>" + "".join(
        f"<tr><td>{i}/BP/2026</td><td>Owner {i}</td><td>Madhapur</td></tr>" for i in range(5)) + "</table>"
    f = FakeFetcher({"https://www.ghmc.gov.in/": home, "https://www.ghmc.gov.in/BuildingPermissions/List": table})
    recs = list(get_crawler("ghmc", fetcher=f).crawl("hyderabad", "permissions"))
    assert len(recs) == 5 and recs[0].kind == "permission" and recs[0].raw["File No"] == "0/BP/2026"


# ----------------------------------------------------------------- CLI
def test_cli_list(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "ts_rera" in out and "hmda" in out and "justdial" in out


def test_cli_crawl_writes_csv(tmp_path, monkeypatch):
    f = FakeFetcher({"https://www.magicbricks.com/property-for-rent-in-hyderabad-pppfr": load("magicbricks_rent.html")})
    monkeypatch.setattr("konu_crawlers.cli.Fetcher", lambda **kw: f)
    out = tmp_path / "{source}.csv"
    assert main(["crawl", "magicbricks", "--type", "rent", "--out", str(out), "--no-raw"]) == 0
    lines = (tmp_path / "magicbricks.csv").read_text().splitlines()
    assert lines[0].startswith("source,kind,source_id") and len(lines) == 31


def test_doctor_saves_pages_and_report(tmp_path, monkeypatch):
    from konu_crawlers import doctor

    page = load("magicbricks_rent.html")

    def fake_request(self, method, url, *, check=True, **kw):
        resp = Response(url, 200, page if "pppfr" in url and "page=" not in url else "<html></html>", {"Content-Type": "text/html"}, b"")
        self._save(method, url, resp, None)
        if check:
            self.check_blocked(resp)
        return resp

    monkeypatch.setattr(doctor.RecordingFetcher, "request", fake_request)
    monkeypatch.setattr(doctor, "_network_info", lambda f: {"country": "IN"})
    out = tmp_path / "doc"
    assert doctor.run(["magicbricks"], str(out), None, 0, False, None) == 0
    report = json.loads((out / "report.json").read_text())
    rent = next(r for r in report["runs"] if r["type"] == "rent")
    assert rent["outcome"] == "ok" and rent["records"] == 30 and rent["requests"][0]["file"]
    assert (out / "magicbricks__rent" / rent["requests"][0]["file"]).exists()
    assert (tmp_path / "doc.zip").exists()


def test_realestateindia_posts_load_more_batches():
    first = "https://www.realestateindia.com/hyderabad-property/flats-apartments-for-sale.htm"
    endpoint = "https://www.realestateindia.com/Functions/property_fetch_results.php"
    frag = load("realestateindia_frag_sale.html")
    f = FakeFetcher({first: load("realestateindia_sale.html"),
                     endpoint: lambda m, u, kw: frag if kw["data"]["pageno"] == "1" else "<div></div>"})
    recs = list(get_crawler("realestateindia", fetcher=f).crawl("hyderabad", "sale", max_pages=3))
    posts = [kw["data"]["pageno"] for m, u, kw in f.calls if m == "POST"]
    assert posts == ["1", "2"]  # batch 3 empty → stop
    assert len(recs) == len({r.source_id for r in recs}) >= 35
