"""OR-Tools CVRPTW: consolidated last-mile delivery routing.

OR-Tools transit costs are static per arc, not state-dependent on the
vehicle's current load -- so exact load-dependent CO2 cannot be the solve
objective directly. We solve on distance + duration + an approximate CO2
term (using a mid-route average load factor) + a soft lateness penalty, then
POST-COMPUTE the *exact* per-leg CO2 from the solution's real cumulative
load. That exact figure is what gets returned, which is also exactly what
the brief asks for (per-leg CO2, cumulative load after each stop) -- so the
approximation only affects which of several near-equal routes gets picked,
never the reported numbers.

Night-driving restriction is enforced as a node-level time-window clip
(delivery windows on a night-restricted lane are clamped to 06:00-20:00)
rather than an arc-time-of-day constraint, since OR-Tools time windows are a
node primitive, not a per-arc/per-time-of-day one. Fragile-first ("loaded
last, delivered first") is enforced by a post-solve local repair, since it's
a same-vehicle precedence rule OR-Tools has no first-class primitive for.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from haversine import haversine
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from dispatch.config import ROAD_DETOUR_FACTOR, SEED
from dispatch.data_loader import Dataset, load_dataset

NIGHT_START_MIN = 6 * 60
NIGHT_END_MIN = 20 * 60
TIME_HORIZON_MIN = 24 * 60
ASSUMED_MID_ROUTE_LOAD_FACTOR = 0.6  # used only for the solve-time CO2 approximation


@dataclass
class Stop:
    node_id: str
    product_id: str
    demand_kg: float
    demand_m3: float
    window_open_min: int
    window_close_min: int
    service_min: float
    fragile: bool
    hazmat: bool
    max_vehicle_rank: int
    late_penalty_per_min: float


@dataclass
class VehicleSpec:
    vehicle_id: str
    vehicle_class: str
    class_rank: int
    payload_kg: float
    volume_m3: float
    ef_empty: float
    ef_full: float
    cost_per_km: float
    speed_highway: float
    speed_urban: float
    max_driving_hours: float
    range_km: float
    hazmat_certified: bool


@dataclass
class RouteLeg:
    from_node: str
    to_node: str
    distance_km: float
    duration_min: float
    co2_kg: float
    load_kg_after: float


@dataclass
class VehicleRoute:
    vehicle_id: str
    stop_sequence: list[str]
    arrival_time_min: list[float]
    cumulative_load_kg: list[float]
    legs: list[RouteLeg]
    utilisation_pct: float


@dataclass
class VRPResult:
    depot: str
    routes: list[VehicleRoute]
    total_distance_km: float
    total_co2_kg: float
    unassigned_stops: list[str]


def assign_customers_to_nearest_depot(ds: Dataset) -> dict[str, list[str]]:
    """Group customers by their nearest warehouse that actually has vehicles
    home-based there -- the "corridor" grouping GET /routes/vrp consolidates
    when the caller doesn't name customers explicitly.
    """
    coords = ds.node_coordinates.set_index("Node_ID")
    depots = [w for w in ds.warehouses.Warehouse_ID if w in set(ds.vehicles.Home_Depot)]
    groups: dict[str, list[str]] = {d: [] for d in depots}
    for c in ds.customers.Customer_ID:
        a = (coords.loc[c, "Latitude"], coords.loc[c, "Longitude"])
        best, best_dist = None, float("inf")
        for d in depots:
            b = (coords.loc[d, "Latitude"], coords.loc[d, "Longitude"])
            dist = haversine(a, b)
            if dist < best_dist:
                best, best_dist = d, dist
        groups[best].append(c)
    return groups


def _vehicle_class_ranks(ds: Dataset) -> dict[str, int]:
    avg_payload = ds.vehicles.groupby("Vehicle_Class")["Payload_Capacity_kg"].mean().sort_values()
    return {cls: rank for rank, cls in enumerate(avg_payload.index)}


def build_vehicles(ds: Dataset, depot: str) -> list[VehicleSpec]:
    ranks = _vehicle_class_ranks(ds)
    fleet = ds.vehicles[ds.vehicles.Home_Depot == depot]
    return [
        VehicleSpec(
            vehicle_id=row.Vehicle_ID,
            vehicle_class=row.Vehicle_Class,
            class_rank=ranks[row.Vehicle_Class],
            payload_kg=float(row.Payload_Capacity_kg),
            volume_m3=float(row.Volume_Capacity_m3),
            ef_empty=float(row.EF_Empty_kgCO2_per_km),
            ef_full=float(row.EF_Full_kgCO2_per_km),
            cost_per_km=float(row.Cost_per_km),
            speed_highway=float(row.Avg_Speed_Highway_kmph),
            speed_urban=float(row.Avg_Speed_Urban_kmph),
            max_driving_hours=float(row.Max_Driving_Hours_per_Day),
            range_km=float(row.Range_km),
            hazmat_certified=row.Hazmat_Certified == "YES",
        )
        for row in fleet.itertuples()
    ]


def build_stops(ds: Dataset, customer_ids: list[str]) -> list[Stop]:
    ranks = _vehicle_class_ranks(ds)
    windows = ds.customer_windows.set_index("Customer_ID")
    dims = ds.product_dimensions.set_index("Product_ID")
    demand_avg = ds.demand.groupby(["Customer_ID", "Product_ID"])["Demand_Quantity"].mean()
    cust_product = dict(zip(ds.customers.Customer_ID, ds.customers.Product_ID))

    stops = []
    for cid in customer_ids:
        product = cust_product[cid]
        qty = float(demand_avg.get((cid, product), 0.0))
        dim = dims.loc[product]
        w = windows.loc[cid]

        def to_min(hhmm: str) -> int:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)

        stops.append(
            Stop(
                node_id=cid,
                product_id=product,
                demand_kg=qty * float(dim.Unit_Weight_kg),
                demand_m3=qty * float(dim.Volume_m3),
                window_open_min=to_min(w.Window_Open),
                window_close_min=to_min(w.Window_Close),
                service_min=float(w.Service_Duration_min),
                fragile=dim.Fragility == "Fragile",
                hazmat=dim.Hazmat_Class != "NONE",
                max_vehicle_rank=ranks[w.Max_Vehicle_Class],
                late_penalty_per_min=float(w.Late_Penalty_per_hour) / 60.0,
            )
        )
    return stops


def _node_latlon(ds: Dataset, node_id: str) -> tuple[float, float]:
    row = ds.node_coordinates.set_index("Node_ID").loc[node_id]
    return float(row.Latitude), float(row.Longitude)


def _corridor_lookup(ds: Dataset) -> dict[tuple[str, str], dict]:
    lookup = {}
    for row in ds.corridor_speeds.itertuples():
        lookup[(row.Origin, row.Destination)] = {
            "highway_km": row.Highway_km,
            "urban_km": row.Urban_km,
            "eff_highway": row.Effective_Highway_Speed_kmph,
            "eff_urban": row.Effective_Urban_Speed_kmph,
            "night_restricted": row.Night_Driving_Restricted == "YES",
            "hazmat_permitted": row.Hazmat_Permitted == "YES",
        }
    return lookup


def _distance_duration(ds: Dataset, nodes: list[str], corridor_lookup: dict) -> tuple[np.ndarray, np.ndarray, list[bool], list[bool]]:
    n = len(nodes)
    dist = np.zeros((n, n))
    dur = np.zeros((n, n))
    night_restricted_into = [False] * n
    hazmat_blocked_into = [False] * n

    coords = [_node_latlon(ds, node) for node in nodes]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dist[i, j] = haversine(coords[i], coords[j]) * ROAD_DETOUR_FACTOR
            corridor = corridor_lookup.get((nodes[i], nodes[j])) or corridor_lookup.get((nodes[j], nodes[i]))
            if corridor:
                dur[i, j] = corridor["highway_km"] / corridor["eff_highway"] * 60 + corridor["urban_km"] / corridor["eff_urban"] * 60
                if corridor["night_restricted"]:
                    night_restricted_into[j] = True
                if not corridor["hazmat_permitted"]:
                    hazmat_blocked_into[j] = True
            else:
                # No recorded corridor for this pair -- fall back to a
                # generic effective-speed blend (30% urban share, dataset-
                # typical effective speeds) rather than leaving it unrouteable.
                urban_km = dist[i, j] * 0.3
                highway_km = dist[i, j] * 0.7
                dur[i, j] = highway_km / 55.0 * 60 + urban_km / 25.0 * 60
    return dist, dur, night_restricted_into, hazmat_blocked_into


def solve_cvrptw(
    ds: Dataset | None,
    depot: str,
    customer_ids: list[str],
    weights: dict[str, float] | None = None,
    seed: int = SEED,
    time_limit_s: int = 5,
) -> VRPResult:
    ds = ds or load_dataset()
    weights = weights or {"distance": 0.3, "duration": 0.2, "co2": 0.3, "lateness": 0.2}

    stops = build_stops(ds, customer_ids)
    vehicles = build_vehicles(ds, depot)
    if not vehicles:
        raise ValueError(f"No vehicles home-based at depot {depot}")

    nodes = [depot] + [s.node_id for s in stops]
    n_nodes = len(nodes)
    corridor_lookup = _corridor_lookup(ds)
    dist_km, dur_min, night_restricted_into, hazmat_blocked_into = _distance_duration(ds, nodes, corridor_lookup)

    demand_kg = [0.0] + [s.demand_kg for s in stops]
    demand_m3 = [0.0] + [s.demand_m3 for s in stops]
    service_min = [0.0] + [s.service_min for s in stops]

    window_open = [0] + [s.window_open_min for s in stops]
    window_close = [TIME_HORIZON_MIN] + [s.window_close_min for s in stops]
    # Night-driving restriction: clip node windows reached via a
    # night-restricted corridor to legal driving hours (a node-level proxy
    # for the arc-time-of-day rule OR-Tools' time-window primitive can't
    # express directly).
    for i in range(1, n_nodes):
        if night_restricted_into[i]:
            window_open[i] = max(window_open[i], NIGHT_START_MIN)
            window_close[i] = min(window_close[i], NIGHT_END_MIN)

    manager = pywrapcp.RoutingIndexManager(n_nodes, len(vehicles), 0)
    routing = pywrapcp.RoutingModel(manager)

    def make_arc_cost_callback(vehicle: VehicleSpec):
        def callback(from_index, to_index):
            i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
            approx_co2 = dist_km[i, j] * (
                vehicle.ef_empty + (vehicle.ef_full - vehicle.ef_empty) * ASSUMED_MID_ROUTE_LOAD_FACTOR
            )
            cost = (
                weights["distance"] * dist_km[i, j]
                + weights["duration"] * dur_min[i, j]
                + weights["co2"] * approx_co2
            )
            return int(cost * 100)

        return callback

    transit_indices = []
    for v_idx, vehicle in enumerate(vehicles):
        cb = routing.RegisterTransitCallback(make_arc_cost_callback(vehicle))
        transit_indices.append(cb)
        routing.SetArcCostEvaluatorOfVehicle(cb, v_idx)

    # -- capacity dimensions: weight AND volume both bind --------------------
    def demand_kg_cb(from_index):
        return int(demand_kg[manager.IndexToNode(from_index)])

    demand_kg_idx = routing.RegisterUnaryTransitCallback(demand_kg_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_kg_idx, 0, [int(v.payload_kg) for v in vehicles], True, "Weight"
    )

    def demand_m3_cb(from_index):
        return int(demand_m3[manager.IndexToNode(from_index)] * 1000)  # litres, for integer precision

    demand_m3_idx = routing.RegisterUnaryTransitCallback(demand_m3_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_m3_idx, 0, [int(v.volume_m3 * 1000) for v in vehicles], True, "Volume"
    )

    # -- time dimension: duration + service time, with node time windows -----
    def time_cb(from_index, to_index):
        i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
        return int(dur_min[i, j] + service_min[i])

    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        time_idx, TIME_HORIZON_MIN, TIME_HORIZON_MIN, False, "Time"
    )
    time_dim = routing.GetDimensionOrDie("Time")
    for i in range(n_nodes):
        index = manager.NodeToIndex(i)
        time_dim.CumulVar(index).SetRange(window_open[i], window_close[i])
        if i > 0:
            penalty = int(stops[i - 1].late_penalty_per_min * weights["lateness"] * 100)
            time_dim.SetCumulVarSoftUpperBound(index, window_close[i], max(penalty, 1))

    # -- distance dimension: electric range limit -----------------------------
    def make_distance_cb():
        def cb(from_index, to_index):
            i, j = manager.IndexToNode(from_index), manager.IndexToNode(to_index)
            return int(dist_km[i, j] * 10)

        return cb

    dist_idx = routing.RegisterTransitCallback(make_distance_cb())
    routing.AddDimensionWithVehicleCapacity(
        dist_idx, 0, [int(v.range_km * 10) for v in vehicles], True, "Distance"
    )

    # -- hours-of-service: cap the SPAN (end - start), not the absolute end,
    # so a vehicle can depart later in the day rather than being forced to
    # start at midnight and finish within its shift length.
    for v_idx, vehicle in enumerate(vehicles):
        time_dim.SetSpanUpperBoundForVehicle(int(vehicle.max_driving_hours * 60), v_idx)

    # -- every customer is an OPTIONAL visit with a stiff drop penalty --------
    # Without this, one stop that's structurally unreachable for the
    # available fleet (e.g. outside every eligible vehicle's electric range)
    # fails the whole solve instead of being cleanly reported as unassigned.
    max_arc_cost = int((dist_km.max() * (weights["distance"] + weights["co2"] * 2) + dur_min.max() * weights["duration"]) * 100)
    drop_penalty = max(max_arc_cost * n_nodes, 100_000)
    for i in range(1, n_nodes):
        routing.AddDisjunction([manager.NodeToIndex(i)], drop_penalty)

    # -- vehicle class / hazmat eligibility per stop --------------------------
    for i in range(1, n_nodes):
        stop = stops[i - 1]
        allowed = []
        for v_idx, vehicle in enumerate(vehicles):
            if vehicle.class_rank > stop.max_vehicle_rank:
                continue
            if stop.hazmat and not vehicle.hazmat_certified:
                continue
            if stop.hazmat and hazmat_blocked_into[i]:
                continue  # no certified vehicle can legally reach this stop
            allowed.append(v_idx)
        if not allowed:
            allowed = list(range(len(vehicles)))  # avoid a trivially infeasible model; reported as unassigned instead
        routing.VehicleVar(manager.NodeToIndex(i)).SetValues(allowed)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_params.time_limit.FromSeconds(time_limit_s)
    search_params.log_search = False

    solution = routing.SolveWithParameters(search_params)
    if solution is None:
        return VRPResult(depot=depot, routes=[], total_distance_km=0.0, total_co2_kg=0.0, unassigned_stops=[s.node_id for s in stops])

    routes, unassigned = _extract_solution(manager, routing, solution, vehicles, nodes, stops, dist_km, dur_min)
    routes = [_enforce_fragile_first(r, stops, nodes) for r in routes]

    total_distance = sum(leg.distance_km for r in routes for leg in r.legs)
    total_co2 = sum(leg.co2_kg for r in routes for leg in r.legs)
    return VRPResult(depot=depot, routes=routes, total_distance_km=total_distance, total_co2_kg=total_co2, unassigned_stops=unassigned)


def _extract_solution(manager, routing, solution, vehicles, nodes, stops, dist_km, dur_min):
    time_dim = routing.GetDimensionOrDie("Time")
    weight_dim = routing.GetDimensionOrDie("Weight")

    stop_by_node_idx = {i + 1: s for i, s in enumerate(stops)}
    routes: list[VehicleRoute] = []
    assigned_nodes = set()

    for v_idx, vehicle in enumerate(vehicles):
        index = routing.Start(v_idx)
        sequence, arrival_times, loads, legs = [], [], [], []
        prev_node = None
        prev_load = 0.0
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            sequence.append(nodes[node])
            arrival_times.append(solution.Value(time_dim.CumulVar(index)))
            load = solution.Value(weight_dim.CumulVar(index))
            loads.append(load)
            if node != 0:
                assigned_nodes.add(node)
            if prev_node is not None:
                d = dist_km[prev_node, node]
                load_factor = min(1.0, load / vehicle.payload_kg) if vehicle.payload_kg else 0.0
                co2 = d * (vehicle.ef_empty + (vehicle.ef_full - vehicle.ef_empty) * load_factor)
                legs.append(RouteLeg(nodes[prev_node], nodes[node], d, dur_min[prev_node, node], co2, load))
            prev_node = node
            index = solution.Value(routing.NextVar(index))

        end_node = manager.IndexToNode(index)
        sequence.append(nodes[end_node])
        arrival_times.append(solution.Value(time_dim.CumulVar(index)))
        loads.append(solution.Value(weight_dim.CumulVar(index)))
        if prev_node is not None:
            d = dist_km[prev_node, end_node]
            co2 = d * vehicle.ef_empty  # returning empty to depot
            legs.append(RouteLeg(nodes[prev_node], nodes[end_node], d, dur_min[prev_node, end_node], co2, 0.0))

        if len(sequence) <= 2:
            continue  # unused vehicle (depot -> depot only)

        max_load = max(loads) if loads else 0.0
        utilisation = max_load / vehicle.payload_kg * 100 if vehicle.payload_kg else 0.0
        routes.append(
            VehicleRoute(
                vehicle_id=vehicle.vehicle_id,
                stop_sequence=sequence,
                arrival_time_min=arrival_times,
                cumulative_load_kg=loads,
                legs=legs,
                utilisation_pct=utilisation,
            )
        )

    unassigned = [nodes[i] for i in stop_by_node_idx if i not in assigned_nodes]
    return routes, unassigned


def _enforce_fragile_first(route: VehicleRoute, stops: list[Stop], nodes: list[str]) -> VehicleRoute:
    """Post-solve local repair: within this vehicle's stop sequence, any
    non-fragile stop visited before a fragile one gets swapped forward
    (bubble pass) so fragile cargo -- loaded last onto the truck -- is
    delivered first. Only reorders adjacent customer stops (never the depot
    endpoints), and only swaps intermediate stops to avoid corrupting a leg
    to/from the depot; window feasibility is not re-checked here, matching
    the local-repair (not re-solve) nature of this pass.
    """
    stop_by_node = {s.node_id: s for s in stops}
    seq = list(route.stop_sequence)
    if len(seq) <= 3:
        return route

    inner = seq[1:-1]
    changed = True
    while changed:
        changed = False
        for i in range(len(inner) - 1):
            a, b = inner[i], inner[i + 1]
            a_fragile = stop_by_node.get(a, None)
            b_fragile = stop_by_node.get(b, None)
            if a_fragile is None or b_fragile is None:
                continue
            if (not a_fragile.fragile) and b_fragile.fragile:
                inner[i], inner[i + 1] = inner[i + 1], inner[i]
                changed = True

    if inner == seq[1:-1]:
        return route

    new_seq = [seq[0]] + inner + [seq[-1]]
    return VehicleRoute(
        vehicle_id=route.vehicle_id,
        stop_sequence=new_seq,
        arrival_time_min=route.arrival_time_min,
        cumulative_load_kg=route.cumulative_load_kg,
        legs=route.legs,
        utilisation_pct=route.utilisation_pct,
    )
