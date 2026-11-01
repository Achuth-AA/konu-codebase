"""Detail-page facts and the analysis-table columns (pages captured 2026-10-24)."""

import json

import pytest

from konu_crawlers.details import parse_details
from konu_crawlers.models import Record
from konu_crawlers.schema import COLUMNS, CSV_COLUMNS, age_group, age_years, car_parking, floor_number, to_row, total_floors, to_csv_row

from .conftest import FIXTURES, load

RECS = json.loads((FIXTURES / "detail_records.json").read_text())


def rec(name):
    return Record(**RECS[name])


@pytest.mark.parametrize("name,expect", [
    ("nobroker__rent", {"Floor_Number": 3, "Total_Floors": 5, "Balconies": 2, "Car_Parking": 1, "Facing": "East", "Pincode": "500032", "Transaction": "Rent"}),
    ("squareyards__sale", {"Floor_Number": 5, "Total_Floors": 15, "Facing": "West", "Overlooking": "Garden View", "Transaction": "Resale"}),
    ("roofandfloor__sale", {"Floor_Number": 5, "Balconies": 2, "Car_Parking": 1, "Age_Group": "New / Under Construction", "Transaction": "New Property"}),
    ("realestateindia__sale", {"Floor_Number": 8, "Total_Floors": 35, "Balconies": 1, "Ownership": "Freehold", "Construction_Status": "Under Construction"}),
    ("sulekha__latest", {"Floor_Number": 0, "Total_Floors": 4, "Car_Parking": 2, "Facing": "North", "Property_Age_Years": 15, "Age_Group": "11-20 Years"}),
    ("quikr__sale", {"Floor_Number": 5, "Total_Floors": 5, "Balconies": 2}),
])
def test_detail_pages_fill_columns(name, expect):
    r = rec(name)
    row = to_row(r, parse_details(load(f"detail_{name}.html"), r))
    assert list(row) == COLUMNS
    for k, v in expect.items():
        assert row[k] == v, (k, row[k])
    assert row["Price_INR"] and row["Price_Cr"] == round(row["Price_INR"] / 1e7, 4)
    assert row["YearMonth"] == f"{row['Year']}-{row['Month']:02d}"


def test_material_prices_by_month_or_flat():
    r = rec("roofandfloor__sale")
    assert to_row(r, {}, {"*": {"cement": 380, "steel": 58000}})["Cement_Price_Rs_per_bag_50kg"] == 380
    ym = to_row(r)["YearMonth"]
    assert to_row(r, {}, {ym: {"cement": 400, "steel": 60000}})["Steel_TMT_Price_Rs_per_tonne"] == 60000
    assert to_row(r)["Cement_Price_Rs_per_bag_50kg"] is None


@pytest.mark.parametrize("fn,arg,out", [
    (floor_number, "15th of 28 Floors", 15), (floor_number, "Ground Floor", 0), (floor_number, "8th", 8),
    (total_floors, None, None), (age_years, "5-10 Years", 7.5), (age_years, "New Construction", 0),
    (car_parking, "Four Wheeler", 1), (car_parking, "no", 0), (age_group, 15, "11-20 Years"),
])
def test_converters(fn, arg, out):
    assert fn(arg) == out


def test_total_floors_from_floor_text():
    assert total_floors(None, "4 out of 4") == 4 and total_floors(None, "15th of 28 Floors") == 28


def test_every_record_kind_gets_a_full_csv_row():
    agent = Record(source="ts_rera", kind="agent", source_id="A02500001", title="Sri Homes", rera_id="A02500001",
                   raw={"Agent Name": "Sri Homes", "District": "Hyderabad", "View__href": "javascript:void(0)"})
    row = to_csv_row(agent)
    assert list(row) == CSV_COLUMNS
    assert (row["Record_Type"], row["RERA_ID"], row["Title"]) == ("agent", "A02500001", "Sri Homes")
    assert row["Other_Details"] == "Agent Name: Sri Homes | District: Hyderabad"
    portal = to_csv_row(rec("squareyards__sale"))
    assert portal["Record_Type"] == "listing" and portal["Source"] == "squareyards"
    assert portal["Other_Details"] is None or "{" not in portal["Other_Details"]
