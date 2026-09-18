"""Two solvers over one shared allocation model.

Decision variable family: ship[route_id, product_id] = quantity of
product_id allocated to route_id, for every (route, product) pair that is
structurally eligible (a supplier's own product on its outbound lane, any
component on a warehouse<->factory lane, and a customer's single demanded
product on the lanes that reach it). A second family, produce[factory_id,
finished_product_id], is the single-level-BOM production plan.

(a) PRIMARY: `solve_min_cost` builds one exact PuLP MILP for a given
    (co2_cap, lead_time_cap) pair. `pareto_front` sweeps those two caps on a
    grid and keeps the non-dominated (cost, co2, lead_time) points -- an
    exact, deterministic epsilon-constraint Pareto front, not an
    approximation. This is ground truth against which optimizer_nsga.py's
    NSGA-II front is validated.

(b) NSGA-II lives in dispatch/optimizer_nsga.py and reuses the same
    variable/eligibility scaffolding (`build_model_data`) so both solvers
    optimise literally the same decision space.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import pandas as pd
import pulp

from dispatch.config import MAX_SUPPLIER_SHARE
from dispatch.data_loader import Dataset, load_dataset
from dispatch.network import build_network

BIG_M_SLACK = 1.05  # headroom so the binary "supplier active" link never binds tighter than capacity


@dataclass
class RouteLeg:
    route_id: str
    origin: str
    destination: str
    origin_type: str
    destination_type: str
    mode: str
    fleet_tier: str
    cost_per_ton: float
    co2_per_ton: float
    transit_days: float
    reliability: float


@dataclass
class ModelData:
    """Everything the LP/MILP and NSGA-II both need, computed once."""

    legs: list[RouteLeg]
    legs_by_type: dict[tuple[str, str], list[RouteLeg]]
    component_products: list[str]
    finished_products: list[str]
    unit_weight_kg: dict[str, float]
    supplier_offers: pd.DataFrame  # Supplier_ID, Product_ID, Unit_Cost, Capacity
    customer_product: dict[str, str]  # Customer_ID -> the one product it demands
    demand: dict[tuple[str, str], float]  # (Customer_ID, Product_ID) -> avg daily qty
    warehouse_capacity: dict[str, float]
    factory_capacity: dict[str, float]
    bom: dict[tuple[str, str], float]  # (finished, component) -> qty per unit
    total_demand: float


def build_model_data(ds: Dataset | None = None, g: nx.MultiDiGraph | None = None) -> ModelData:
    ds = ds or load_dataset()
    g = g if g is not None else build_network(ds)

    legs: list[RouteLeg] = []
    legs_by_type: dict[tuple[str, str], list[RouteLeg]] = {}
    for u, v, data in g.edges(data=True):
        leg = RouteLeg(
            route_id=data["route_id"],
            origin=u,
            destination=v,
            origin_type=g.nodes[u]["node_type"],
            destination_type=g.nodes[v]["node_type"],
            mode=data["mode"],
            fleet_tier=data["fleet_tier"],
            cost_per_ton=data["cost_per_ton"],
            co2_per_ton=data["co2_per_ton"],
            transit_days=data["transit_days"],
            reliability=data["reliability"],
        )
        legs.append(leg)
        legs_by_type.setdefault((leg.origin_type, leg.destination_type), []).append(leg)

    component_products = sorted(
        ds.products.loc[ds.products.Product_Type.isin(["raw_material", "component"]), "Product_ID"]
    )
    finished_products = sorted(ds.products.loc[ds.products.Product_Type == "finished_good", "Product_ID"])
    unit_weight_kg = dict(zip(ds.products.Product_ID, ds.products.Unit_Weight_kg))

    customer_product = dict(zip(ds.customers.Customer_ID, ds.customers.Product_ID))

    demand_avg = ds.demand.groupby(["Customer_ID", "Product_ID"])["Demand_Quantity"].mean()
    demand = {idx: float(val) for idx, val in demand_avg.items()}
    total_demand = float(sum(demand.get((c, p), 0.0) for c, p in customer_product.items()))

    warehouse_capacity = dict(zip(ds.warehouses.Warehouse_ID, ds.warehouses.Storage_Capacity))
    factory_capacity = dict(zip(ds.factories.Factory_ID, ds.factories.Production_Capacity))

    bom = {
        (row.Finished_Product_ID, row.Component_Product_ID): float(row.Qty_Per_Finished_Unit)
        for row in ds.bill_of_materials.itertuples()
    }

    return ModelData(
        legs=legs,
        legs_by_type=legs_by_type,
        component_products=component_products,
        finished_products=finished_products,
        unit_weight_kg=unit_weight_kg,
        supplier_offers=ds.suppliers[["Supplier_ID", "Product_ID", "Unit_Cost", "Capacity"]].copy(),
        customer_product=customer_product,
        demand=demand,
        warehouse_capacity=warehouse_capacity,
        factory_capacity=factory_capacity,
        bom=bom,
        total_demand=total_demand,
    )


def _leg_unit_cost(leg: RouteLeg, weight_kg: float) -> float:
    return leg.cost_per_ton * (weight_kg / 1000.0)


def _leg_unit_co2(leg: RouteLeg, weight_kg: float) -> float:
    return leg.co2_per_ton * (weight_kg / 1000.0)


@dataclass
class BuiltProblem:
    prob: pulp.LpProblem
    ship: dict[tuple[str, str], pulp.LpVariable]  # (route_id, product_id) -> var
    produce: dict[tuple[str, str], pulp.LpVariable]  # (factory_id, product_id) -> var
    supplier_active: dict[tuple[str, str], pulp.LpVariable]  # (supplier_id, product_id) -> binary
    cost_expr: pulp.LpAffineExpression
    co2_expr: pulp.LpAffineExpression
    leadtime_numerator_expr: pulp.LpAffineExpression
    md: ModelData


def build_problem(md: ModelData, name: str = "dispatch") -> BuiltProblem:
    prob = pulp.LpProblem(name, pulp.LpMinimize)

    sw_legs = md.legs_by_type.get(("Supplier", "Warehouse"), [])
    wf_legs = md.legs_by_type.get(("Warehouse", "Factory"), [])
    fw_legs = md.legs_by_type.get(("Factory", "Warehouse"), [])
    fc_legs = md.legs_by_type.get(("Factory", "Customer"), [])
    wc_legs = md.legs_by_type.get(("Warehouse", "Customer"), [])

    offers = md.supplier_offers
    supplier_products: dict[str, set[str]] = {}
    supplier_capacity: dict[tuple[str, str], float] = {}
    supplier_unit_cost: dict[tuple[str, str], float] = {}
    for row in offers.itertuples():
        supplier_products.setdefault(row.Supplier_ID, set()).add(row.Product_ID)
        supplier_capacity[(row.Supplier_ID, row.Product_ID)] = float(row.Capacity)
        supplier_unit_cost[(row.Supplier_ID, row.Product_ID)] = float(row.Unit_Cost)

    ship: dict[tuple[str, str], pulp.LpVariable] = {}

    def add_ship_var(leg: RouteLeg, product_id: str) -> pulp.LpVariable:
        key = (leg.route_id, product_id)
        if key not in ship:
            ship[key] = pulp.LpVariable(f"ship_{leg.route_id}_{product_id}", lowBound=0)
        return ship[key]

    sw_legs_by_product: dict[str, list[RouteLeg]] = {}
    for leg in sw_legs:
        for p in supplier_products.get(leg.origin, set()):
            add_ship_var(leg, p)
            sw_legs_by_product.setdefault(p, []).append(leg)

    wf_legs_used = []
    for leg in wf_legs:
        for p in md.component_products:
            add_ship_var(leg, p)
        wf_legs_used.append(leg)

    fw_legs_used = []
    for leg in fw_legs:
        for p in md.finished_products:
            add_ship_var(leg, p)
        fw_legs_used.append(leg)

    fc_legs_used = []
    for leg in fc_legs:
        p = md.customer_product.get(leg.destination)
        if p is not None:
            add_ship_var(leg, p)
            fc_legs_used.append((leg, p))

    wc_legs_used = []
    for leg in wc_legs:
        p = md.customer_product.get(leg.destination)
        if p is not None:
            add_ship_var(leg, p)
            wc_legs_used.append((leg, p))

    produce: dict[tuple[str, str], pulp.LpVariable] = {}
    factories = sorted(md.factory_capacity)
    for f in factories:
        for g in md.finished_products:
            produce[(f, g)] = pulp.LpVariable(f"produce_{f}_{g}", lowBound=0)

    supplier_active: dict[tuple[str, str], pulp.LpVariable] = {
        (s, p): pulp.LpVariable(f"active_{s}_{p}", cat="Binary") for (s, p) in supplier_capacity
    }

    cost_terms = []
    co2_terms = []
    for leg in sw_legs:
        for p in supplier_products.get(leg.origin, set()):
            w = md.unit_weight_kg[p]
            var = ship[(leg.route_id, p)]
            unit_cost = supplier_unit_cost[(leg.origin, p)] + _leg_unit_cost(leg, w)
            cost_terms.append(unit_cost * var)
            co2_terms.append(_leg_unit_co2(leg, w) * var)
    for leg in wf_legs_used:
        for p in md.component_products:
            w = md.unit_weight_kg[p]
            var = ship[(leg.route_id, p)]
            cost_terms.append(_leg_unit_cost(leg, w) * var)
            co2_terms.append(_leg_unit_co2(leg, w) * var)
    for leg in fw_legs_used:
        for p in md.finished_products:
            w = md.unit_weight_kg[p]
            var = ship[(leg.route_id, p)]
            cost_terms.append(_leg_unit_cost(leg, w) * var)
            co2_terms.append(_leg_unit_co2(leg, w) * var)
    for leg, p in fc_legs_used:
        w = md.unit_weight_kg[p]
        var = ship[(leg.route_id, p)]
        cost_terms.append(_leg_unit_cost(leg, w) * var)
        co2_terms.append(_leg_unit_co2(leg, w) * var)
    for leg, p in wc_legs_used:
        w = md.unit_weight_kg[p]
        var = ship[(leg.route_id, p)]
        cost_terms.append(_leg_unit_cost(leg, w) * var)
        co2_terms.append(_leg_unit_co2(leg, w) * var)

    cost_expr = pulp.lpSum(cost_terms)
    co2_expr = pulp.lpSum(co2_terms)

    leadtime_terms = [leg.transit_days * ship[(leg.route_id, p)] for leg, p in fc_legs_used]
    leadtime_terms += [leg.transit_days * ship[(leg.route_id, p)] for leg, p in wc_legs_used]
    leadtime_numerator_expr = pulp.lpSum(leadtime_terms)

    prob += cost_expr

    # 1. Supplier offer capacity + binary "supplier active" link (the MILP part)
    for (s, p), cap in supplier_capacity.items():
        legs_sp = [leg for leg in sw_legs_by_product.get(p, []) if leg.origin == s]
        total = pulp.lpSum(ship[(leg.route_id, p)] for leg in legs_sp)
        prob += total <= cap, f"supplier_capacity_{s}_{p}"
        prob += total <= cap * BIG_M_SLACK * supplier_active[(s, p)], f"supplier_active_link_{s}_{p}"

    # 2. Supplier diversification cap: no supplier > MAX_SUPPLIER_SHARE of a product's sourced volume
    for p in md.component_products:
        legs_p = sw_legs_by_product.get(p, [])
        if not legs_p:
            continue
        total_p = pulp.lpSum(ship[(leg.route_id, p)] for leg in legs_p)
        by_supplier: dict[str, list[RouteLeg]] = {}
        for leg in legs_p:
            by_supplier.setdefault(leg.origin, []).append(leg)
        for s, legs_sp in by_supplier.items():
            supplier_total = pulp.lpSum(ship[(leg.route_id, p)] for leg in legs_sp)
            prob += (
                supplier_total <= MAX_SUPPLIER_SHARE * total_p,
                f"diversification_{s}_{p}",
            )

    # 3. Warehouse storage capacity: total inbound quantity (components + finished) <= capacity
    warehouses = sorted(md.warehouse_capacity)
    for w in warehouses:
        inbound_terms = []
        for leg in sw_legs:
            if leg.destination != w:
                continue
            for p in supplier_products.get(leg.origin, set()):
                inbound_terms.append(ship[(leg.route_id, p)])
        for leg in fw_legs_used:
            if leg.destination != w:
                continue
            for p in md.finished_products:
                inbound_terms.append(ship[(leg.route_id, p)])
        if inbound_terms:
            prob += pulp.lpSum(inbound_terms) <= md.warehouse_capacity[w], f"warehouse_capacity_{w}"

    # 4. Factory production capacity
    for f in factories:
        prob += (
            pulp.lpSum(produce[(f, g)] for g in md.finished_products) <= md.factory_capacity[f],
            f"factory_capacity_{f}",
        )

    # 5. BOM component balance: inbound components at f == production requirement
    for f in factories:
        for k in md.component_products:
            inbound = pulp.lpSum(
                ship[(leg.route_id, k)] for leg in wf_legs_used if leg.destination == f
            )
            required = pulp.lpSum(
                md.bom.get((g, k), 0.0) * produce[(f, g)] for g in md.finished_products if (g, k) in md.bom
            )
            prob += inbound == required, f"bom_balance_{f}_{k}"

    # 6. Factory output balance: production == what leaves the factory
    for f in factories:
        for g in md.finished_products:
            outbound = pulp.lpSum(
                ship[(leg.route_id, g)] for leg, p in fc_legs_used if leg.origin == f and p == g
            )
            outbound += pulp.lpSum(ship[(leg.route_id, g)] for leg in fw_legs_used if leg.origin == f)
            prob += produce[(f, g)] == outbound, f"output_balance_{f}_{g}"

    # 7. Warehouse finished-goods pass-through (no held inventory in this single-period plan)
    for w in warehouses:
        for g in md.finished_products:
            inbound = pulp.lpSum(ship[(leg.route_id, g)] for leg in fw_legs_used if leg.destination == w)
            outbound = pulp.lpSum(
                ship[(leg.route_id, g)] for leg, p in wc_legs_used if leg.origin == w and p == g
            )
            prob += inbound == outbound, f"warehouse_finished_passthrough_{w}_{g}"

    # 8. Warehouse component pass-through
    for w in warehouses:
        for k in md.component_products:
            inbound = pulp.lpSum(ship[(leg.route_id, k)] for leg in sw_legs if leg.destination == w and k in supplier_products.get(leg.origin, set()))
            outbound = pulp.lpSum(ship[(leg.route_id, k)] for leg in wf_legs_used if leg.origin == w)
            prob += inbound == outbound, f"warehouse_component_passthrough_{w}_{k}"

    # 9. Demand satisfaction: every customer's single product, met exactly
    for c, p in md.customer_product.items():
        qty = md.demand.get((c, p), 0.0)
        inbound = pulp.lpSum(ship[(leg.route_id, p)] for leg, pp in fc_legs_used if leg.destination == c and pp == p)
        inbound += pulp.lpSum(ship[(leg.route_id, p)] for leg, pp in wc_legs_used if leg.destination == c and pp == p)
        prob += inbound == qty, f"demand_{c}_{p}"

    return BuiltProblem(
        prob=prob,
        ship=ship,
        produce=produce,
        supplier_active=supplier_active,
        cost_expr=cost_expr,
        co2_expr=co2_expr,
        leadtime_numerator_expr=leadtime_numerator_expr,
        md=md,
    )


@dataclass
class SolveResult:
    status: str
    cost: float
    co2_kg: float
    lead_time_days: float
    reliability: float
    n_suppliers_active: int
    co2_cap: float | None
    lead_time_cap: float | None
    ship: dict[tuple[str, str], float] = field(repr=False)


def _extract_solution(built: BuiltProblem, co2_cap: float | None, lead_time_cap: float | None) -> SolveResult:
    md = built.md
    ship_vals = {k: v.value() or 0.0 for k, v in built.ship.items()}

    total_units = 0.0
    reliability_weighted = 0.0
    route_reliability = {leg.route_id: leg.reliability for leg in md.legs}
    route_origin = {leg.route_id: leg.origin for leg in md.legs}
    for (route_id, _product), qty in ship_vals.items():
        if qty <= 1e-9:
            continue
        total_units += qty
        reliability_weighted += qty * route_reliability.get(route_id, 1.0)
    reliability = reliability_weighted / total_units if total_units > 0 else 1.0

    # Suppliers with material flow > 0, not the (cost-free, hence
    # non-binding) supplier_active binaries -- those only enforce the
    # capacity link, they carry no incentive to switch off.
    active_supplier_products = {
        (route_origin[route_id], product)
        for (route_id, product), qty in ship_vals.items()
        if qty > 1e-6 and route_origin.get(route_id, "").startswith("S")
    }
    n_active = len({s for s, _p in active_supplier_products})

    status = pulp.LpStatus[built.prob.status]
    cost = pulp.value(built.cost_expr) or 0.0
    co2 = pulp.value(built.co2_expr) or 0.0
    leadtime_num = pulp.value(built.leadtime_numerator_expr) or 0.0
    lead_time = leadtime_num / md.total_demand if md.total_demand > 0 else 0.0

    return SolveResult(
        status=status,
        cost=cost,
        co2_kg=co2,
        lead_time_days=lead_time,
        reliability=reliability,
        n_suppliers_active=n_active,
        co2_cap=co2_cap,
        lead_time_cap=lead_time_cap,
        ship=ship_vals,
    )


def solve_min_cost(
    md: ModelData,
    co2_cap: float | None = None,
    lead_time_cap: float | None = None,
    solver: pulp.LpSolver | None = None,
) -> SolveResult:
    built = build_problem(md)
    if co2_cap is not None:
        built.prob += built.co2_expr <= co2_cap, "co2_cap"
    if lead_time_cap is not None:
        built.prob += built.leadtime_numerator_expr <= lead_time_cap * md.total_demand, "lead_time_cap"
    solver = solver or pulp.PULP_CBC_CMD(msg=False)
    built.prob.solve(solver)
    return _extract_solution(built, co2_cap, lead_time_cap)


def solve_min_co2(md: ModelData, solver: pulp.LpSolver | None = None) -> SolveResult:
    built = build_problem(md)
    built.prob.setObjective(built.co2_expr)
    solver = solver or pulp.PULP_CBC_CMD(msg=False)
    built.prob.solve(solver)
    return _extract_solution(built, None, None)


def pareto_front(
    md: ModelData | None = None,
    n_co2_points: int = 9,
    n_leadtime_points: int = 4,
) -> list[SolveResult]:
    """Exact epsilon-constraint Pareto front: sweep CO2 and lead-time caps on
    a grid, minimise cost at each, and keep the non-dominated (cost, co2,
    lead_time) points. Deterministic -- CBC's simplex is exact for a fixed
    LP/MILP, so repeated runs against the same data return the same front.
    """
    md = md or build_model_data()
    solver = pulp.PULP_CBC_CMD(msg=False)

    baseline = solve_min_cost(md, solver=solver)
    min_co2_result = solve_min_co2(md, solver=solver)

    # CBC solves the cost-min and co2-min problems via different simplex
    # paths, so the true co2 optimum can differ from a later re-solve by a
    # few parts in 1e8. Capping at the raw value makes that boundary point
    # spuriously Infeasible; a tiny relative slack keeps it solvable without
    # materially widening the sweep.
    co2_lo, co2_hi = min_co2_result.co2_kg * 1.0005, baseline.co2_kg
    if co2_hi <= co2_lo:
        co2_caps = [co2_hi]
    else:
        step = (co2_hi - co2_lo) / (n_co2_points - 1)
        co2_caps = [co2_lo + i * step for i in range(n_co2_points)]

    lt_lo = min(baseline.lead_time_days, min_co2_result.lead_time_days)
    lt_hi = max(baseline.lead_time_days, min_co2_result.lead_time_days) * 1.5 + 1.0
    lt_step = (lt_hi - lt_lo) / (n_leadtime_points - 1) if n_leadtime_points > 1 else 0
    lt_caps = [lt_lo + i * lt_step for i in range(n_leadtime_points)] if lt_step > 0 else [lt_hi]

    candidates: list[SolveResult] = []
    for co2_cap in co2_caps:
        for lt_cap in lt_caps:
            result = solve_min_cost(md, co2_cap=co2_cap, lead_time_cap=lt_cap, solver=solver)
            if result.status == "Optimal":
                candidates.append(result)

    return _non_dominated(candidates)


def _non_dominated(results: list[SolveResult]) -> list[SolveResult]:
    front = []
    for r in results:
        dominated = False
        for other in results:
            if other is r:
                continue
            if (
                other.cost <= r.cost
                and other.co2_kg <= r.co2_kg
                and other.lead_time_days <= r.lead_time_days
                and (other.cost, other.co2_kg, other.lead_time_days) != (r.cost, r.co2_kg, r.lead_time_days)
            ):
                dominated = True
                break
        if not dominated:
            front.append(r)
    seen = set()
    deduped = []
    for r in sorted(front, key=lambda r: (r.cost, r.co2_kg, r.lead_time_days)):
        key = (round(r.cost, 2), round(r.co2_kg, 2), round(r.lead_time_days, 3))
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped
