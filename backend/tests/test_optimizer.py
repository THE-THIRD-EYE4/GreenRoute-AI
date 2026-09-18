from collections import defaultdict

import pytest

from dispatch.config import MAX_SUPPLIER_SHARE
from dispatch.optimizer import build_model_data, pareto_front, solve_min_cost

MD = build_model_data()


def test_min_cost_solves_optimal():
    result = solve_min_cost(MD)
    assert result.status == "Optimal"
    assert result.cost > 0
    assert result.co2_kg > 0


def test_demand_satisfied_exactly():
    result = solve_min_cost(MD)
    route_dest = {leg.route_id: leg.destination for leg in MD.legs}
    delivered = defaultdict(float)
    for (route_id, product), qty in result.ship.items():
        c = route_dest.get(route_id)
        if c in MD.customer_product and MD.customer_product[c] == product:
            delivered[c] += qty
    for c, p in MD.customer_product.items():
        expected = MD.demand.get((c, p), 0.0)
        assert delivered[c] == pytest.approx(expected, abs=1e-4)


def test_supplier_diversification_cap_respected():
    result = solve_min_cost(MD)
    route_origin = {leg.route_id: leg.origin for leg in MD.legs}
    by_product = defaultdict(lambda: defaultdict(float))
    for (route_id, product), qty in result.ship.items():
        origin = route_origin.get(route_id, "")
        if origin.startswith("S") and qty > 1e-6:
            by_product[product][origin] += qty
    for product, suppliers in by_product.items():
        total = sum(suppliers.values())
        for supplier, qty in suppliers.items():
            assert qty / total <= MAX_SUPPLIER_SHARE + 1e-6, (
                f"{supplier} exceeds {MAX_SUPPLIER_SHARE:.0%} share of {product}"
            )


def test_bom_component_balance():
    result = solve_min_cost(MD)
    route_origin = {leg.route_id: leg.origin for leg in MD.legs}
    route_dest = {leg.route_id: leg.destination for leg in MD.legs}

    # Recompute production implied by factory output legs (F->C, F->W) and
    # verify it matches inbound component flow scaled by the BOM.
    outbound_finished = defaultdict(float)
    for (route_id, product), qty in result.ship.items():
        if product not in MD.finished_products:
            continue
        origin = route_origin.get(route_id)
        if origin and origin.startswith("F"):
            outbound_finished[(origin, product)] += qty

    inbound_components = defaultdict(float)
    for (route_id, product), qty in result.ship.items():
        if product not in MD.component_products:
            continue
        origin, dest = route_origin.get(route_id), route_dest.get(route_id)
        if origin and origin.startswith("W") and dest and dest.startswith("F"):
            inbound_components[(dest, product)] += qty

    for f in sorted({f for f, _ in outbound_finished}):
        for k in MD.component_products:
            required = sum(
                MD.bom.get((g, k), 0.0) * outbound_finished.get((f, g), 0.0) for g in MD.finished_products
            )
            actual = inbound_components.get((f, k), 0.0)
            assert actual == pytest.approx(required, abs=1e-3), f"BOM imbalance at {f} for {k}"


def test_pareto_front_has_multiple_points():
    front = pareto_front(MD, n_co2_points=5, n_leadtime_points=2)
    assert len(front) > 1
    costs = [r.cost for r in front]
    co2s = [r.co2_kg for r in front]
    assert max(costs) > min(costs)
    assert max(co2s) > min(co2s)


def test_pareto_front_is_non_dominated():
    front = pareto_front(MD, n_co2_points=5, n_leadtime_points=2)
    for r in front:
        for other in front:
            if other is r:
                continue
            strictly_better_or_equal = (
                other.cost <= r.cost and other.co2_kg <= r.co2_kg and other.lead_time_days <= r.lead_time_days
            )
            strictly_better = (
                other.cost < r.cost or other.co2_kg < r.co2_kg or other.lead_time_days < r.lead_time_days
            )
            assert not (strictly_better_or_equal and strictly_better), "front contains a dominated point"
