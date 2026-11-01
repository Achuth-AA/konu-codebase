"""Parsers against real pages captured from each site on 2026-10-24."""

import pytest

from konu_crawlers.http import BlockedError, Fetcher, Response
from konu_crawlers.sources import hmda, magicbricks, nobroker, proptiger, quikr, roofandfloor, squareyards, sulekha

from .conftest import load


def test_nobroker_sale():
    recs = nobroker.parse_sale_page(load("nobroker_sale.html"), "hyderabad")
    assert len(recs) >= 20
    r = recs[0]
    assert r.transaction == "sale" and r.price > 1_000_000 and r.area_sqft and r.bedrooms
    assert r.url.startswith("https://www.nobroker.in/property/") and r.latitude and r.longitude
    assert len({x.source_id for x in recs}) == len(recs)


def test_nobroker_rent_cards():
    recs = nobroker.parse_rent_page(load("nobroker_rent.html"), "hyderabad")
    assert len(recs) >= 20
    r = recs[0]
    assert r.transaction == "rent" and 1000 < r.price < 1_000_000
    assert r.area_sqft and r.bedrooms and r.furnishing
    assert r.raw["deposit"] and r.url.endswith("/detail")


def test_magicbricks_rent():
    recs = magicbricks.parse_page(load("magicbricks_rent.html"), "hyderabad", "rent")
    assert len(recs) == 30
    r = recs[0]
    assert r.transaction == "rent" and r.price and r.area_sqft and r.locality and r.latitude
    assert r.url.startswith("https://www.magicbricks.com/propertyDetails/")
    assert r.posted_by in ("Owner", "Agent", "Builder")


def test_magicbricks_maintenance_wall_is_reported_as_blocked():
    with pytest.raises(BlockedError, match="maintenance"):
        Fetcher.check_blocked(Response("https://www.magicbricks.com/x", 200, load("magicbricks_maintenance.html")))


@pytest.mark.parametrize("fixture,trans", [("squareyards_sale.html", "sale"), ("squareyards_rent.html", "rent")])
def test_squareyards_listings(fixture, trans):
    recs = squareyards.parse_page(load(fixture), "hyderabad", trans)
    listings = [r for r in recs if r.kind == "listing"]
    assert len(listings) == 25
    assert all(r.transaction == trans and r.price for r in listings)
    assert sum(1 for r in listings if r.latitude) >= 15  # merged from JSON-LD
    assert all(r.url.startswith("https://www.squareyards.com/") for r in listings)


def test_squareyards_projects():
    recs = squareyards.parse_page(load("squareyards_projects.html"), "hyderabad", "projects")
    assert len(recs) >= 30
    assert all(r.kind == "project" for r in recs)
    assert sum(1 for r in recs if r.price and r.price_max) >= 20


def test_proptiger_city_and_project():
    city = proptiger.parse_page(load("proptiger_city.html"), "hyderabad")
    assert len(city) >= 30 and all(r.kind == "project" and r.url for r in city)
    assert sum(1 for r in city if r.price) >= 25
    proj = proptiger.parse_page(load("proptiger_project.html"), "hyderabad")
    main = next(r for r in proj if r.source_id == "3528410")
    assert main.latitude and main.builder and main.price_max > main.price


def test_roofandfloor_cards_and_fragment():
    for name in ("roofandfloor_sale.html", "roofandfloor_fragment.html"):
        recs = roofandfloor.parse_listing_cards(load(name), "sale", "hyderabad")
        assert len(recs) == 10
        assert all(r.price and r.area_sqft for r in recs)
        projects = [r for r in recs if r.kind == "project"]
        assert all(r.price > 1_000_000 and r.area_sqft > 300 for r in projects)  # not ₹/sqft or a BHK count
        for r in recs:  # "load more" batches mix in builder projects
            assert r.url.startswith("https://www.roofandfloor.com/" + ("propertydetail/" if r.kind == "listing" else "hyderabad/project/"))


def test_roofandfloor_projects():
    recs = roofandfloor.parse_project_cards(load("roofandfloor_projects.html"), "hyderabad")
    assert len(recs) >= 10
    assert all(r.kind == "project" and r.price for r in recs)


def test_quikr_ld_merged_with_cards():
    recs = quikr.parse_page(load("quikr_sale.html"), "hyderabad", "sale")
    assert len(recs) >= 15
    priced = [r for r in recs if r.price]
    assert len(priced) >= 15
    assert all(r.latitude and r.source_id and r.source_id.isdigit() for r in recs)


def test_sulekha_city_page_ads():
    recs = sulekha.parse_ad_links(load("sulekha_city.html"), sulekha.BASE, "hyderabad", None)
    assert len(recs) >= 10
    assert sum(1 for r in recs if r.price) >= 10
    assert all(r.url.endswith("-ad") for r in recs)


def test_hmda_layouts():
    recs = hmda.parse_report(load("hmda_layouts.html"), "LO.aspx", "core", "layout")
    assert len(recs) == 25
    r = recs[0]
    assert r.kind == "layout" and r.source_id and r.contact_name and r.posted_at.startswith("20")
    assert r.area_sqft and r.area_sqft > 100_000  # 4 acres 36 guntas


def test_hmda_permissions():
    recs = hmda.parse_report(load("hmda_permissions.html"), "Bpermission.aspx", "core", "permission")
    assert len(recs) == 25
    assert all(r.kind == "permission" and r.source_id for r in recs)
    assert recs[0].property_type and recs[0].posted_at


def test_realestateindia_listings():
    from konu_crawlers.sources import realestateindia as rei

    for name, trans in (("realestateindia_sale.html", "sale"), ("realestateindia_rent.html", "rent")):
        recs = rei.parse_listings(load(name), "hyderabad", trans)
        assert len(recs) >= 15 and all(r.transaction == trans and r.source_id.isdigit() for r in recs)
        assert sum(1 for r in recs if r.price and r.area_sqft and r.locality) >= 10
    frag = rei.parse_listings(load("realestateindia_frag_sale.html"), "hyderabad", "sale")
    assert len(frag) == 20


def test_realestateindia_projects_agents_and_load_more():
    from konu_crawlers.sources import realestateindia as rei

    projects = rei.parse_projects(load("realestateindia_proj.html"), "hyderabad")
    assert len(projects) >= 15 and all(r.kind == "project" and r.url for r in projects)
    assert sum(1 for r in projects if r.price) >= 10
    agents = rei.parse_agents(load("realestateindia_agents.html"), "hyderabad")
    assert len(agents) >= 15 and all(r.kind == "agent" and "/profile/" in r.url for r in agents)
    endpoint, params = rei.load_more_request(load("realestateindia_sale.html"))
    assert endpoint.endswith("/Functions/property_fetch_results.php") and params["city"] and params["propertyfor"] == "1"
