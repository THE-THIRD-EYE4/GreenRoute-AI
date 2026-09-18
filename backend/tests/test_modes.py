import warnings
from datetime import datetime

import pytest

from dispatch.data_loader import load_dataset
from dispatch.eta import train_eta_model
from dispatch.modes import (
    air_vs_rail_co2_multiple,
    compute_physicals,
    quote_shipment,
    rank_options,
)

warnings.filterwarnings("ignore")

DS = load_dataset()
ETA_MODEL = train_eta_model(DS)


def test_chargeable_weight_uses_max_of_gross_and_volumetric():
    # A light, bulky item: volumetric weight should dominate.
    physicals = compute_physicals(DS, [("P013", 100)])  # Industrial Gateway, bulkiest finished good
    assert physicals.chargeable_kg_air >= physicals.gross_kg
    assert physicals.chargeable_kg_road >= physicals.gross_kg
    # road divisor (3000) < air divisor (6000) -> road chargeable weight is >= air's
    assert physicals.chargeable_kg_road >= physicals.chargeable_kg_air


def test_quote_finds_air_flight_when_eligible():
    departure = datetime(2026, 9, 18, 0, 0)
    deadline = datetime(2026, 9, 26, 0, 0)
    physicals, options = quote_shipment(
        DS, ETA_MODEL, [("P011", 50)], origin="S001", destination="W05", deadline=deadline, departure=departure
    )
    air = next(o for o in options if o.mode == "Air")
    assert air.feasible
    assert len(air.flights) >= 1
    assert air.flights[0].remaining_uld_kg >= physicals.chargeable_kg_air


def test_quote_respects_passed_departure_not_now():
    # Regression: `departure or X if C else Y` silently discarded the
    # caller's departure due to Python operator precedence (ternary binds
    # looser than `or`), always falling back to datetime.now().
    departure = datetime(2020, 1, 1, 0, 0)  # long before any scheduled flight
    deadline = datetime(2020, 1, 10, 0, 0)
    _, options = quote_shipment(
        DS, ETA_MODEL, [("P011", 50)], origin="S001", destination="W05", deadline=deadline, departure=departure
    )
    air = next(o for o in options if o.mode == "Air")
    assert not air.feasible  # no flight exists that far in the past


def test_electric_truck_infeasible_beyond_range():
    physicals, options = quote_shipment(
        DS, ETA_MODEL, [("P011", 10)], origin="S001", destination="W05",
        deadline=datetime(2026, 9, 30), departure=datetime(2026, 9, 18),
    )
    electric = next(o for o in options if o.mode == "Truck_Electric")
    assert not electric.feasible
    assert "range" in electric.infeasible_reason or "not eligible" in electric.infeasible_reason


def test_road_option_has_stop_sequence_and_utilisation():
    physicals, options = quote_shipment(
        DS, ETA_MODEL, [("P011", 20)], origin="F01", destination="C003",
        deadline=datetime(2026, 9, 30), departure=datetime(2026, 9, 18),
    )
    diesel = next(o for o in options if o.mode == "Truck_Diesel")
    assert diesel.feasible
    assert diesel.road_stop_sequence == ["F01", "C003"]
    assert diesel.road_vehicle_utilisation_pct is not None


def test_rank_options_returns_three_labelled_picks():
    _, options = quote_shipment(
        DS, ETA_MODEL, [("P011", 20)], origin="F01", destination="C003",
        deadline=datetime(2026, 9, 30), departure=datetime(2026, 9, 18),
    )
    picks = rank_options(options)
    assert set(picks) == {"cheapest", "greenest", "fastest"}


def test_air_vs_rail_co2_multiple_is_roughly_21x():
    assert 15 < air_vs_rail_co2_multiple() < 25
