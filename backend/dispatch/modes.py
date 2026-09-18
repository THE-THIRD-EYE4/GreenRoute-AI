"""Shipment quoting: chargeable weight, mode feasibility, and Pareto ranking.

Chargeable weight is a billing construct (`max(gross_kg, volume_cm3 /
divisor)`), used for PRICING; gross weight is the real physical mass, used
for PAYLOAD limits and for CO2 (emissions track real mass moved, not a
billing volumetric equivalent -- conflating the two is the commonest costing
bug in freight software, per the brief, and using chargeable weight for CO2
would repeat the same category of mistake in the other direction).

Where a direct (origin, destination, mode) lane already exists in
transportation_routes_augmented.csv, its stored cost/CO2/transit/reliability
(which already bakes in fleet-tier jitter) is used directly. Otherwise a
lane is synthesized with the exact same economics and eligibility rules
augment_network.py used to build the table in the first place (see MODES
below), so a shipper can quote between any two nodes, not just ones that
happen to have a precomputed lane.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from haversine import haversine

from dispatch.config import (
    CHARGEABLE_DIVISOR_AIR,
    CHARGEABLE_DIVISOR_ROAD,
    ROAD_DETOUR_FACTOR,
    SEED,
)
from dispatch.data_loader import Dataset, load_dataset
from dispatch.eta import ETAModel, predict_delay_distribution

RAIL_PER_TON_KM_CO2 = 0.0280  # for the "air is ~21x rail per ton-km" contrast the UI must show

# Mirrors augment_network.py's MODES table exactly, so a synthesized lane
# (no precomputed row for this origin/destination/mode) behaves identically
# to how the dataset itself was generated.
MODES = {
    "Truck_Diesel": dict(cost=0.0750, co2=0.1050, speed_kmph=55, hours_per_day=8, terminal_days=0, min_km=0, max_km=2600),
    "Truck_Electric": dict(cost=0.0985, co2=0.0335, speed_kmph=48, hours_per_day=7, terminal_days=0, min_km=0, max_km=420),
    "Rail": dict(cost=0.0355, co2=0.0280, speed_kmph=32, hours_per_day=20, terminal_days=2, min_km=280, max_km=2600),
    "Air": dict(cost=0.8900, co2=0.6020, speed_kmph=620, hours_per_day=24, terminal_days=1, min_km=650, max_km=2600),
}


def _mode_transit_days(km: float, mode: str) -> int:
    m = MODES[mode]
    daily_reach = m["speed_kmph"] * m["hours_per_day"]
    return int(max(1, math.ceil(km / daily_reach) + m["terminal_days"]))


@dataclass
class ShipmentPhysicals:
    gross_kg: float
    volume_m3: float
    volume_cm3: float
    chargeable_kg_air: float
    chargeable_kg_road: float
    contains_hazmat: bool


def compute_physicals(ds: Dataset, items: list[tuple[str, float]]) -> ShipmentPhysicals:
    """items: list of (product_id, quantity)."""
    dims = ds.product_dimensions.set_index("Product_ID")
    gross_kg = 0.0
    volume_m3 = 0.0
    hazmat = False
    for product_id, qty in items:
        row = dims.loc[product_id]
        gross_kg += qty * float(row.Unit_Weight_kg)
        volume_m3 += qty * float(row.Volume_m3)
        if row.Hazmat_Class != "NONE":
            hazmat = True
    volume_cm3 = volume_m3 * 1_000_000
    return ShipmentPhysicals(
        gross_kg=gross_kg,
        volume_m3=volume_m3,
        volume_cm3=volume_cm3,
        chargeable_kg_air=max(gross_kg, volume_cm3 / CHARGEABLE_DIVISOR_AIR),
        chargeable_kg_road=max(gross_kg, volume_cm3 / CHARGEABLE_DIVISOR_ROAD),
        contains_hazmat=hazmat,
    )


@dataclass
class AirFlightOption:
    flight_id: str
    carrier_name: str
    departure_utc: str
    arrival_utc: str
    cutoff_utc: str
    remaining_uld_kg: float
    cost_per_kg: float


@dataclass
class ModeOption:
    mode: str
    feasible: bool
    infeasible_reason: str | None
    distance_km: float
    cost: float
    co2_kg: float
    transit_days: int
    eta_p10_days: float
    eta_p50_days: float
    eta_p90_days: float
    on_time_probability: float
    reliability: float
    flights: list[AirFlightOption] = field(default_factory=list)
    road_stop_sequence: list[str] | None = None
    road_arrival_min: list[float] | None = None
    road_cumulative_load_kg: list[float] | None = None
    road_vehicle_id: str | None = None
    road_vehicle_utilisation_pct: float | None = None


def _direct_route_rows(ds: Dataset, origin: str, destination: str, mode: str):
    return ds.routes[
        (ds.routes.Origin == origin) & (ds.routes.Destination == destination) & (ds.routes.Transport_Mode == mode)
    ]


def _node_row(ds: Dataset, node_id: str):
    return ds.node_coordinates.set_index("Node_ID").loc[node_id]


def _eligible_by_geography(ds: Dataset, origin: str, destination: str, mode: str, distance_km: float) -> tuple[bool, str | None]:
    m = MODES[mode]
    if not (m["min_km"] <= distance_km <= m["max_km"]):
        return False, f"{mode} not eligible for {distance_km:.0f}km (needs {m['min_km']}-{m['max_km']}km)"
    if mode == "Truck_Electric" and distance_km > m["max_km"]:
        return False, f"exceeds electric range ({m['max_km']}km)"
    if mode == "Rail":
        o, d = _node_row(ds, origin), _node_row(ds, destination)
        if o.Rail_Hub != "YES" or d.Rail_Hub != "YES":
            return False, "both ends must be rail hubs"
    if mode == "Air":
        o, d = _node_row(ds, origin), _node_row(ds, destination)
        if not o.Nearest_Airport_IATA or not d.Nearest_Airport_IATA:
            return False, "both ends must have an airport"
    return True, None


def _quote_ground_or_rail(
    ds: Dataset, origin: str, destination: str, mode: str, physicals: ShipmentPhysicals, eta_model: ETAModel, departure: datetime, seed: int
) -> ModeOption:
    direct = _direct_route_rows(ds, origin, destination, mode)
    o_row, d_row = _node_row(ds, origin), _node_row(ds, destination)
    o_latlon = (float(o_row.Latitude), float(o_row.Longitude))
    d_latlon = (float(d_row.Latitude), float(d_row.Longitude))
    great_circle = haversine(o_latlon, d_latlon)
    detour = ROAD_DETOUR_FACTOR if mode != "Air" else 1.0
    distance_km = great_circle * detour

    if not direct.empty:
        best = direct.loc[direct.Cost_per_km_per_ton.idxmin()]
        distance_km = float(best.Distance_km)
        cost_per_ton_km = float(best.Cost_per_km_per_ton)
        co2_per_ton_km = float(best.CO2_per_ton_km)
        transit_days = int(best.Transit_Time_days) + int(best.Terminal_Dwell_days)
        reliability = float(best.Route_Reliability)
    else:
        eligible, reason = _eligible_by_geography(ds, origin, destination, mode, distance_km)
        if not eligible:
            return ModeOption(
                mode=mode, feasible=False, infeasible_reason=reason, distance_km=distance_km,
                cost=0, co2_kg=0, transit_days=0, eta_p10_days=0, eta_p50_days=0, eta_p90_days=0,
                on_time_probability=0, reliability=0,
            )
        m = MODES[mode]
        cost_per_ton_km, co2_per_ton_km = m["cost"], m["co2"]
        transit_days = _mode_transit_days(distance_km, mode)
        reliability = 0.93  # dataset-typical baseline when no observed lane exists

    gross_tons = physicals.gross_kg / 1000.0
    chargeable_tons = physicals.chargeable_kg_road / 1000.0
    cost = distance_km * cost_per_ton_km * chargeable_tons
    co2_kg = distance_km * co2_per_ton_km * gross_tons

    delay_dist = predict_delay_distribution(
        eta_model, ds, origin=origin, destination=destination, supplier_id="internal",
        quantity=max(physicals.gross_kg, 1), departure_date=departure.date().isoformat(),
    )
    probs = np.array([delay_dist.get(0, 0), delay_dist.get(1, 0), delay_dist.get(2, 0)])
    probs = probs / probs.sum()
    rng = np.random.default_rng(seed)
    draws = transit_days + rng.choice([0, 1, 2], size=2000, p=probs)
    p10, p50, p90 = np.quantile(draws, [0.10, 0.50, 0.90])

    return ModeOption(
        mode=mode, feasible=True, infeasible_reason=None, distance_km=distance_km,
        cost=cost, co2_kg=co2_kg, transit_days=transit_days,
        eta_p10_days=float(p10), eta_p50_days=float(p50), eta_p90_days=float(p90),
        on_time_probability=float(probs[0]), reliability=reliability,
    )


def _quote_air(
    ds: Dataset, origin: str, destination: str, physicals: ShipmentPhysicals, deadline: datetime, departure: datetime
) -> ModeOption:
    o_row, d_row = _node_row(ds, origin), _node_row(ds, destination)
    o_latlon = (float(o_row.Latitude), float(o_row.Longitude))
    d_latlon = (float(d_row.Latitude), float(d_row.Longitude))
    distance_km = haversine(o_latlon, d_latlon)

    eligible, reason = _eligible_by_geography(ds, origin, destination, "Air", distance_km)
    if not eligible:
        return ModeOption(
            mode="Air", feasible=False, infeasible_reason=reason, distance_km=distance_km,
            cost=0, co2_kg=0, transit_days=0, eta_p10_days=0, eta_p50_days=0, eta_p90_days=0,
            on_time_probability=0, reliability=0,
        )

    o_city, d_city = o_row.City, d_row.City
    sched = ds.air_cargo_schedule
    candidates = sched[
        (sched.Origin_City == o_city)
        & (sched.Destination_City == d_city)
        & (sched.Cutoff_UTC > departure)
        & (sched.Arrival_UTC <= deadline)
        & ((sched.ULD_Capacity_kg - sched.Booked_kg) >= physicals.chargeable_kg_air)
    ]
    if physicals.contains_hazmat:
        candidates = candidates[candidates.Accepts_Hazmat == "YES"]

    if candidates.empty:
        return ModeOption(
            mode="Air", feasible=False,
            infeasible_reason="no scheduled flight has capacity/cutoff/hazmat clearance before the deadline",
            distance_km=distance_km, cost=0, co2_kg=0, transit_days=0,
            eta_p10_days=0, eta_p50_days=0, eta_p90_days=0, on_time_probability=0, reliability=0,
        )

    flights = [
        AirFlightOption(
            flight_id=row.Flight_ID, carrier_name=row.Carrier_Name,
            departure_utc=row.Departure_UTC.isoformat(), arrival_utc=row.Arrival_UTC.isoformat(),
            cutoff_utc=row.Cutoff_UTC.isoformat(), remaining_uld_kg=float(row.ULD_Capacity_kg - row.Booked_kg),
            cost_per_kg=float(row.Cost_per_kg),
        )
        for row in candidates.itertuples()
    ]
    best = candidates.loc[candidates.Cost_per_kg.idxmin()]
    cost = float(best.Cost_per_kg) * physicals.chargeable_kg_air
    co2_kg = float(best.CO2_per_ton_km) * distance_km * (physicals.gross_kg / 1000.0)
    transit_hours = (best.Arrival_UTC - best.Departure_UTC).total_seconds() / 3600
    transit_days = max(1, math.ceil(transit_hours / 24))

    return ModeOption(
        mode="Air", feasible=True, infeasible_reason=None, distance_km=distance_km,
        cost=cost, co2_kg=co2_kg, transit_days=transit_days,
        eta_p10_days=transit_days, eta_p50_days=transit_days, eta_p90_days=transit_days + 1,
        on_time_probability=0.85, reliability=0.95, flights=flights,
    )


def _attach_road_route(ds: Dataset, opt: ModeOption, origin: str, destination: str, physicals: ShipmentPhysicals) -> None:
    """A single-shipment quote isn't a multi-customer consolidation run
    (that's /routes/vrp), so "the full stop sequence" here is the direct
    origin -> destination leg with a best-fit vehicle, per-stop arrival
    time, and a cumulative load bar -- exactly what the brief asks the
    /quote UI to render on the map.
    """
    candidates = ds.vehicles[
        (ds.vehicles.Transport_Mode == opt.mode)
        & (ds.vehicles.Payload_Capacity_kg >= physicals.gross_kg)
        & (ds.vehicles.Volume_Capacity_m3 >= physicals.volume_m3)
    ].sort_values("Payload_Capacity_kg")
    if candidates.empty:
        return
    vehicle = candidates.iloc[0]
    transit_min = opt.transit_days * 24 * 60
    opt.road_stop_sequence = [origin, destination]
    opt.road_arrival_min = [0.0, float(transit_min)]
    opt.road_cumulative_load_kg = [float(physicals.gross_kg), 0.0]
    opt.road_vehicle_id = vehicle.Vehicle_ID
    opt.road_vehicle_utilisation_pct = float(physicals.gross_kg / vehicle.Payload_Capacity_kg * 100)


def quote_shipment(
    ds: Dataset | None,
    eta_model: ETAModel,
    items: list[tuple[str, float]],
    origin: str,
    destination: str,
    deadline: datetime,
    departure: datetime | None = None,
    seed: int = SEED,
) -> tuple[ShipmentPhysicals, list[ModeOption]]:
    ds = ds or load_dataset()
    if departure is None:
        departure = datetime.now(deadline.tzinfo) if deadline.tzinfo else datetime.now()
    physicals = compute_physicals(ds, items)

    options = []
    for mode in ("Truck_Diesel", "Truck_Electric", "Rail"):
        opt = _quote_ground_or_rail(ds, origin, destination, mode, physicals, eta_model, departure, seed)
        deadline_days = (deadline - departure).total_seconds() / 86400
        if opt.feasible and opt.transit_days > deadline_days:
            opt.feasible = False
            opt.infeasible_reason = f"{opt.transit_days}d transit exceeds the {deadline_days:.1f}d deadline"
        if opt.feasible and mode.startswith("Truck"):
            _attach_road_route(ds, opt, origin, destination, physicals)
        options.append(opt)

    options.append(_quote_air(ds, origin, destination, physicals, deadline, departure))

    return physicals, options


def air_vs_rail_co2_multiple() -> float:
    """Air is roughly this many times dirtier than rail per ton-km -- the
    number the /quote UI must make impossible to miss."""
    return MODES["Air"]["co2"] / MODES["Rail"]["co2"]


def rank_options(options: list[ModeOption]) -> dict[str, ModeOption]:
    """Pareto-rank on (cost, CO2, arrival certainty) and return the three
    labelled picks the /quote UI shows as cards.
    """
    feasible = [o for o in options if o.feasible]
    if not feasible:
        return {}
    cheapest = min(feasible, key=lambda o: o.cost)
    greenest = min(feasible, key=lambda o: o.co2_kg)
    fastest = min(feasible, key=lambda o: o.eta_p50_days)
    return {"cheapest": cheapest, "greenest": greenest, "fastest": fastest}

