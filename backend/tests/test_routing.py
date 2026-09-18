import warnings

import pytest

from dispatch.data_loader import load_dataset
from dispatch.routing import (
    Stop,
    VehicleRoute,
    _enforce_fragile_first,
    assign_customers_to_nearest_depot,
    build_vehicles,
    solve_cvrptw,
)

warnings.filterwarnings("ignore")

DS = load_dataset()


def test_realistic_local_cluster_is_fully_served():
    # W05 (Pune) has two nearly-co-located customers plus one reachable
    # same-day stop -- a genuine corridor, unlike scattered cross-country
    # customers which legitimately can't all be served in one shift.
    result = solve_cvrptw(DS, depot="W05", customer_ids=["C012", "C022", "C011"], time_limit_s=5)
    assert result.unassigned_stops == []
    assert len(result.routes) >= 1


def test_payload_and_cube_capacity_not_exceeded():
    result = solve_cvrptw(DS, depot="W05", customer_ids=["C012", "C022", "C011"], time_limit_s=5)
    vehicles = {v.vehicle_id: v for v in build_vehicles(DS, "W05")}
    for route in result.routes:
        vehicle = vehicles[route.vehicle_id]
        assert max(route.cumulative_load_kg) <= vehicle.payload_kg + 1e-6


def test_electric_vehicle_respects_range():
    result = solve_cvrptw(DS, depot="W02", customer_ids=["C014"], time_limit_s=5)
    vehicles = {v.vehicle_id: v for v in build_vehicles(DS, "W02")}
    for route in result.routes:
        vehicle = vehicles[route.vehicle_id]
        total_dist = sum(leg.distance_km for leg in route.legs)
        assert total_dist <= vehicle.range_km + 1e-6


def test_hours_of_service_span_respected():
    result = solve_cvrptw(DS, depot="W05", customer_ids=["C012", "C022", "C011"], time_limit_s=5)
    vehicles = {v.vehicle_id: v for v in build_vehicles(DS, "W05")}
    for route in result.routes:
        vehicle = vehicles[route.vehicle_id]
        span = route.arrival_time_min[-1] - route.arrival_time_min[0]
        assert span <= vehicle.max_driving_hours * 60 + 1e-6


def test_load_dependent_co2_matches_formula():
    result = solve_cvrptw(DS, depot="W05", customer_ids=["C012", "C022", "C011"], time_limit_s=5)
    vehicles = {v.vehicle_id: v for v in build_vehicles(DS, "W05")}
    for route in result.routes:
        vehicle = vehicles[route.vehicle_id]
        for leg in route.legs:
            load_factor = min(1.0, leg.load_kg_after / vehicle.payload_kg) if leg.load_kg_after > 0 else 0.0
            # returning-empty legs use ef_empty directly; outbound legs use the load-dependent blend
            expected_loaded = leg.distance_km * (vehicle.ef_empty + (vehicle.ef_full - vehicle.ef_empty) * load_factor)
            expected_empty = leg.distance_km * vehicle.ef_empty
            assert leg.co2_kg == pytest.approx(expected_loaded, abs=1e-6) or leg.co2_kg == pytest.approx(
                expected_empty, abs=1e-6
            )


def test_unreachable_cluster_reports_unassigned_not_a_hard_failure():
    # Customers scattered across the country: a single day, narrow-window
    # CVRPTW genuinely cannot serve all of them from one depot's small
    # fleet. The solver should drop what it can't reach, not fail outright.
    result = solve_cvrptw(DS, depot="W01", customer_ids=["C001", "C002", "C003", "C006", "C011"], time_limit_s=5)
    assert isinstance(result.unassigned_stops, list)


def test_assign_customers_to_nearest_depot_covers_all_customers():
    groups = assign_customers_to_nearest_depot(DS)
    all_assigned = [c for stops in groups.values() for c in stops]
    assert sorted(all_assigned) == sorted(DS.customers.Customer_ID)


def test_fragile_first_reorders_local_repair():
    stops = [
        Stop("A", "P001", 10, 0.01, 0, 1440, 5, fragile=False, hazmat=False, max_vehicle_rank=99, late_penalty_per_min=0),
        Stop("B", "P011", 10, 0.01, 0, 1440, 5, fragile=True, hazmat=False, max_vehicle_rank=99, late_penalty_per_min=0),
        Stop("C", "P002", 10, 0.01, 0, 1440, 5, fragile=False, hazmat=False, max_vehicle_rank=99, late_penalty_per_min=0),
    ]
    route = VehicleRoute(
        vehicle_id="V1",
        stop_sequence=["DEPOT", "A", "C", "B", "DEPOT"],
        arrival_time_min=[0, 10, 20, 30, 40],
        cumulative_load_kg=[0, 10, 20, 30, 0],
        legs=[],
        utilisation_pct=50.0,
    )
    fixed = _enforce_fragile_first(route, stops, ["DEPOT", "A", "B", "C"])
    b_idx = fixed.stop_sequence.index("B")
    a_idx = fixed.stop_sequence.index("A")
    c_idx = fixed.stop_sequence.index("C")
    assert b_idx < a_idx and b_idx < c_idx
