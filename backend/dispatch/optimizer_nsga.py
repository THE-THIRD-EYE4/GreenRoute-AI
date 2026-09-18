"""NSGA-II over the same decision space as optimizer.py's MILP, validated
against it.

A literal genotype = raw ship[route,product] quantities would be enormous
(hundreds of continuous variables) and mostly infeasible under BOM/flow-
conservation equalities -- a poor fit for a population-based GA, which
needs many cheap, valid evaluations rather than a handful of exactly
constrained ones. Instead the chromosome encodes the network's real
DECISIONS at a coarser grain, with feasibility built into the decoder
rather than discovered by penalty:

  - supplier shares per component (67 genes): normalized then cap-and-
    renormalized at the same 40% diversification ceiling the MILP enforces
    as a hard constraint, so it is never violated by construction here
    either.
  - factory production shares per finished good (20 genes): normalized
    across the 4 factories; factory capacity is a genuine pymoo inequality
    constraint (G), not a penalty hack.
  - a single "green bias" per product (15 genes, component + finished):
    blends normalized cost and CO2 rank to pick among that product's
    candidate routes at each echelon it crosses. This is the one lever
    pymoo actually gets to explore mode/fleet-tier trade-offs with, and it
    is exactly the same two-lever trade-off the dataset's own README
    documents (mode choice, fleet tier).

Simplification, stated plainly: this decoder does NOT track literal
per-warehouse component/finished-goods inventory the way the MILP's flow-
conservation constraints do -- it treats each echelon stage's cost/CO2/
lead-time as quantity x a representative candidate route's rate, picked by
the green-bias blend. That is a real relaxation (warehouse storage
capacity is not enforced here), acceptable because this module's job is
fast multi-objective trade-off exploration and validation against the
MILP's ground truth on the objectives both share a consistent formula for
(cost, CO2, lead time) -- not to be a second exact flow planner.

Five objectives: total cost, kg CO2, volume-weighted (customer-facing)
lead time, 1 - joint reliability, expected stockout units. The last two are
both derived from the same chain-reliability figure (probability-scaled vs
unit-scaled) -- they are meant to be correlated, as reliability and
stockout risk genuinely are in a real network.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import Problem
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.optimize import minimize
from pymoo.termination import get_termination

from dispatch.config import MAX_SUPPLIER_SHARE, SEED
from dispatch.data_loader import Dataset, load_dataset
from dispatch.network import build_network
from dispatch.optimizer import ModelData, RouteLeg, build_model_data

N_POP_DEFAULT = 120
N_GEN_DEFAULT = 300


@dataclass
class NsgaContext:
    md: ModelData
    component_products: list[str]
    finished_products: list[str]
    supplier_gene_index: list[tuple[str, str]]  # (supplier_id, product_id), one per gene in block A
    supplier_by_product: dict[str, list[str]]
    total_component_demand: dict[str, float]
    total_finished_demand: dict[str, float]
    sw_legs_by_supplier: dict[str, list[RouteLeg]]
    wf_legs: list[RouteLeg]
    fc_legs_by_product: dict[str, list[RouteLeg]]
    fw_legs: list[RouteLeg]
    wc_legs_by_product: dict[str, list[RouteLeg]]
    supplier_unit_cost: dict[tuple[str, str], float]
    n_genes_a: int
    n_genes_b: int
    n_genes_c: int


def _build_context(ds: Dataset, md: ModelData) -> NsgaContext:
    component_products = md.component_products
    finished_products = md.finished_products

    supplier_gene_index = [(row.Supplier_ID, row.Product_ID) for row in md.supplier_offers.itertuples()]
    supplier_by_product: dict[str, list[str]] = {}
    for s, p in supplier_gene_index:
        supplier_by_product.setdefault(p, []).append(s)
    supplier_unit_cost = {
        (row.Supplier_ID, row.Product_ID): float(row.Unit_Cost) for row in md.supplier_offers.itertuples()
    }

    total_finished_demand: dict[str, float] = {}
    for c, g in md.customer_product.items():
        total_finished_demand[g] = total_finished_demand.get(g, 0.0) + md.demand.get((c, g), 0.0)

    total_component_demand: dict[str, float] = {k: 0.0 for k in component_products}
    for (g, k), qty_per in md.bom.items():
        total_component_demand[k] = total_component_demand.get(k, 0.0) + total_finished_demand.get(g, 0.0) * qty_per

    sw_legs_by_supplier: dict[str, list[RouteLeg]] = {}
    for leg in md.legs_by_type.get(("Supplier", "Warehouse"), []):
        sw_legs_by_supplier.setdefault(leg.origin, []).append(leg)

    wf_legs = list(md.legs_by_type.get(("Warehouse", "Factory"), []))
    fw_legs = list(md.legs_by_type.get(("Factory", "Warehouse"), []))

    fc_legs_by_product: dict[str, list[RouteLeg]] = {}
    for leg in md.legs_by_type.get(("Factory", "Customer"), []):
        g = md.customer_product.get(leg.destination)
        if g:
            fc_legs_by_product.setdefault(g, []).append(leg)

    wc_legs_by_product: dict[str, list[RouteLeg]] = {}
    for leg in md.legs_by_type.get(("Warehouse", "Customer"), []):
        g = md.customer_product.get(leg.destination)
        if g:
            wc_legs_by_product.setdefault(g, []).append(leg)

    return NsgaContext(
        md=md,
        component_products=component_products,
        finished_products=finished_products,
        supplier_gene_index=supplier_gene_index,
        supplier_by_product=supplier_by_product,
        total_component_demand=total_component_demand,
        total_finished_demand=total_finished_demand,
        sw_legs_by_supplier=sw_legs_by_supplier,
        wf_legs=wf_legs,
        fc_legs_by_product=fc_legs_by_product,
        fw_legs=fw_legs,
        wc_legs_by_product=wc_legs_by_product,
        supplier_unit_cost=supplier_unit_cost,
        n_genes_a=len(supplier_gene_index),
        n_genes_b=4 * len(finished_products),
        n_genes_c=len(component_products) + len(finished_products),
    )


def _cap_and_normalize(raw: np.ndarray, cap: float) -> np.ndarray:
    w = raw + 1e-9
    w = w / w.sum()
    for _ in range(len(w) + 1):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        under = ~over
        if not under.any():
            break
        w[under] += excess * (w[under] / w[under].sum())
    return w


def _blend_pick(legs: list[RouteLeg], bias: float) -> RouteLeg:
    if len(legs) == 1:
        return legs[0]
    costs = np.array([l.cost_per_ton for l in legs])
    co2s = np.array([l.co2_per_ton for l in legs])
    cost_range = costs.max() - costs.min()
    co2_range = co2s.max() - co2s.min()
    cost_n = (costs - costs.min()) / (cost_range or 1.0)
    co2_n = (co2s - co2s.min()) / (co2_range or 1.0)
    score = (1 - bias) * cost_n + bias * co2_n
    return legs[int(np.argmin(score))]


@dataclass
class DecodedPlan:
    cost: float
    co2_kg: float
    lead_time_days: float
    joint_reliability: float
    expected_stockout_units: float
    factory_capacity_violation: np.ndarray  # length 4, >0 means over capacity


def decode(genes: np.ndarray, ctx: NsgaContext) -> DecodedPlan:
    md = ctx.md
    a_end = ctx.n_genes_a
    b_end = a_end + ctx.n_genes_b
    genes_a, genes_b, genes_c = genes[:a_end], genes[a_end:b_end], genes[b_end:]

    # -- A: supplier shares per component, capped at MAX_SUPPLIER_SHARE ------
    supplier_share: dict[tuple[str, str], float] = {}
    for p in ctx.component_products:
        suppliers = ctx.supplier_by_product[p]
        idxs = [i for i, (s, pp) in enumerate(ctx.supplier_gene_index) if pp == p]
        raw = np.array([genes_a[i] for i in idxs])
        capped = _cap_and_normalize(raw, MAX_SUPPLIER_SHARE)
        for s, w in zip(suppliers, capped):
            supplier_share[(s, p)] = float(w)

    # -- B: factory shares per finished good -----------------------------------
    factories = sorted(md.factory_capacity)
    factory_share: dict[tuple[str, str], float] = {}
    for gi, g in enumerate(ctx.finished_products):
        raw = genes_b[gi * 4 : gi * 4 + 4]
        w = raw / (raw.sum() + 1e-9)
        for fi, f in enumerate(factories):
            factory_share[(f, g)] = float(w[fi])

    # -- C: green bias per product ----------------------------------------------
    green_bias = {p: float(np.clip(genes_c[i], 0, 1)) for i, p in enumerate(ctx.component_products + ctx.finished_products)}

    cost = 0.0
    co2 = 0.0
    leadtime_numerator = 0.0
    chain_reliability_by_product: dict[str, float] = {}

    # -- components: S->W then W->F -------------------------------------------
    for p in ctx.component_products:
        w = md.unit_weight_kg[p]
        qty_total = ctx.total_component_demand[p]
        bias = green_bias[p]

        wf_pool = ctx.wf_legs
        wf_leg = _blend_pick(wf_pool, bias) if wf_pool else None
        wf_reliability = wf_leg.reliability if wf_leg else 1.0
        wf_cost_co2 = (wf_leg.cost_per_ton * w / 1000.0, wf_leg.co2_per_ton * w / 1000.0) if wf_leg else (0.0, 0.0)

        stage_reliabilities = []
        for s in ctx.supplier_by_product[p]:
            share = supplier_share.get((s, p), 0.0)
            if share <= 0:
                continue
            qty = qty_total * share
            sw_pool = ctx.sw_legs_by_supplier.get(s, [])
            if not sw_pool:
                continue
            sw_leg = _blend_pick(sw_pool, bias)
            cost += qty * (ctx.supplier_unit_cost.get((s, p), 0.0) + sw_leg.cost_per_ton * w / 1000.0)
            co2 += qty * sw_leg.co2_per_ton * w / 1000.0
            cost += qty * wf_cost_co2[0]
            co2 += qty * wf_cost_co2[1]
            stage_reliabilities.append((qty, sw_leg.reliability * wf_reliability))

        total_qty = sum(q for q, _ in stage_reliabilities)
        chain_reliability_by_product[p] = (
            sum(q * r for q, r in stage_reliabilities) / total_qty if total_qty > 0 else 1.0
        )

    # -- finished goods: production split, then F->C direct or F->W->C -------
    for g in ctx.finished_products:
        w = md.unit_weight_kg[g]
        qty_total = ctx.total_finished_demand.get(g, 0.0)
        bias = green_bias[g]

        fc_pool = ctx.fc_legs_by_product.get(g, [])
        wc_pool = ctx.wc_legs_by_product.get(g, [])
        fw_pool = ctx.fw_legs

        fc_leg = _blend_pick(fc_pool, bias) if fc_pool else None
        via_warehouse = None
        if fw_pool and wc_pool:
            fw_leg = _blend_pick(fw_pool, bias)
            wc_leg = _blend_pick(wc_pool, bias)
            via_cost = fw_leg.cost_per_ton + wc_leg.cost_per_ton
            via_warehouse = (fw_leg, wc_leg, via_cost)

        use_via_warehouse = fc_leg is None or (via_warehouse is not None and via_warehouse[2] < fc_leg.cost_per_ton)

        if use_via_warehouse and via_warehouse is not None:
            fw_leg, wc_leg, _ = via_warehouse
            delivery_cost_per_ton = fw_leg.cost_per_ton + wc_leg.cost_per_ton
            delivery_co2_per_ton = fw_leg.co2_per_ton + wc_leg.co2_per_ton
            delivery_transit_days = fw_leg.transit_days + wc_leg.transit_days
            delivery_reliability = fw_leg.reliability * wc_leg.reliability
        elif fc_leg is not None:
            delivery_cost_per_ton = fc_leg.cost_per_ton
            delivery_co2_per_ton = fc_leg.co2_per_ton
            delivery_transit_days = fc_leg.transit_days
            delivery_reliability = fc_leg.reliability
        else:
            delivery_cost_per_ton = delivery_co2_per_ton = 0.0
            delivery_transit_days = 0.0
            delivery_reliability = 1.0

        cost += qty_total * delivery_cost_per_ton * w / 1000.0
        co2 += qty_total * delivery_co2_per_ton * w / 1000.0
        leadtime_numerator += qty_total * delivery_transit_days

        component_chain = 0.0
        component_weight_total = 0.0
        for (fin, comp), qty_per in md.bom.items():
            if fin != g:
                continue
            component_weight_total += qty_per
            component_chain += qty_per * chain_reliability_by_product.get(comp, 1.0)
        component_reliability = component_chain / component_weight_total if component_weight_total > 0 else 1.0

        chain_reliability_by_product[g] = component_reliability * delivery_reliability

    factories = sorted(md.factory_capacity)
    factory_load = np.zeros(len(factories))
    for gi, g in enumerate(ctx.finished_products):
        for fi, f in enumerate(factories):
            factory_load[fi] += factory_share[(f, g)] * ctx.total_finished_demand.get(g, 0.0)
    factory_cap_arr = np.array([md.factory_capacity[f] for f in factories])
    violation = factory_load - factory_cap_arr

    total_demand = sum(ctx.total_finished_demand.values())
    joint_reliability = (
        sum(ctx.total_finished_demand.get(g, 0.0) * chain_reliability_by_product.get(g, 1.0) for g in ctx.finished_products)
        / total_demand
        if total_demand > 0
        else 1.0
    )
    expected_stockout_units = sum(
        ctx.total_finished_demand.get(g, 0.0) * (1 - chain_reliability_by_product.get(g, 1.0)) for g in ctx.finished_products
    )
    lead_time_days = leadtime_numerator / total_demand if total_demand > 0 else 0.0

    return DecodedPlan(
        cost=cost, co2_kg=co2, lead_time_days=lead_time_days, joint_reliability=joint_reliability,
        expected_stockout_units=expected_stockout_units, factory_capacity_violation=violation,
    )


class DispatchNSGAProblem(Problem):
    def __init__(self, ctx: NsgaContext):
        self.ctx = ctx
        n_var = ctx.n_genes_a + ctx.n_genes_b + ctx.n_genes_c
        super().__init__(n_var=n_var, n_obj=5, n_constr=len(ctx.md.factory_capacity), xl=0.0, xu=1.0)

    def _evaluate(self, X, out, *args, **kwargs):
        n = X.shape[0]
        F = np.zeros((n, 5))
        G = np.zeros((n, self.n_constr))
        for i in range(n):
            plan = decode(X[i], self.ctx)
            F[i] = [plan.cost, plan.co2_kg, plan.lead_time_days, 1 - plan.joint_reliability, plan.expected_stockout_units]
            G[i] = plan.factory_capacity_violation
        out["F"] = F
        out["G"] = G


@dataclass
class ValidationReport:
    milp_n_points: int
    nsga_n_points: int
    hypervolume_milp: float
    hypervolume_nsga: float
    generational_distance: float
    milp_front_norm: np.ndarray
    nsga_front_norm: np.ndarray


def validate_against_milp(
    ds: Dataset | None = None,
    md: ModelData | None = None,
    pop_size: int = N_POP_DEFAULT,
    n_gen: int = N_GEN_DEFAULT,
    seed: int = SEED,
) -> ValidationReport:
    """Runs both solvers and compares them on the (cost, CO2, lead-time)
    objectives they share a consistent formula for -- reliability/stockout
    aren't part of the MILP's decision space in this design, so they're
    excluded from the cross-solver comparison (not from NSGA-II's own
    5-objective front, which callers can still read off `result.F`).

    Hypervolume and generational distance are computed on min-max
    normalized objectives (combined across both fronts) since cost lives on
    a ~10^6 scale and CO2/lead-time on a ~10^0-10^1 scale -- unnormalized
    Euclidean distances would be entirely cost-dominated.
    """
    from pymoo.indicators.gd import GD
    from pymoo.indicators.hv import HV

    from dispatch.optimizer import pareto_front

    ds = ds or load_dataset()
    md = md or build_model_data(ds, build_network(ds))

    milp_results = pareto_front(md)
    milp_front = np.array([[r.cost, r.co2_kg, r.lead_time_days] for r in milp_results])

    nsga_result, _ctx = run_nsga2(ds, md, pop_size=pop_size, n_gen=n_gen, seed=seed)
    nsga_front = nsga_result.F[:, :3]

    combined = np.vstack([milp_front, nsga_front])
    lo, hi = combined.min(axis=0), combined.max(axis=0)
    span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
    milp_norm = (milp_front - lo) / span
    nsga_norm = (nsga_front - lo) / span

    ref_point = np.array([1.1, 1.1, 1.1])
    hv = HV(ref_point=ref_point)
    gd = GD(milp_norm)

    return ValidationReport(
        milp_n_points=len(milp_results),
        nsga_n_points=len(nsga_front),
        hypervolume_milp=float(hv(milp_norm)),
        hypervolume_nsga=float(hv(nsga_norm)),
        generational_distance=float(gd(nsga_norm)),
        milp_front_norm=milp_norm,
        nsga_front_norm=nsga_norm,
    )


def run_nsga2(
    ds: Dataset | None = None,
    md: ModelData | None = None,
    pop_size: int = N_POP_DEFAULT,
    n_gen: int = N_GEN_DEFAULT,
    seed: int = SEED,
):
    ds = ds or load_dataset()
    md = md or build_model_data(ds, build_network(ds))
    ctx = _build_context(ds, md)
    problem = DispatchNSGAProblem(ctx)

    algorithm = NSGA2(
        pop_size=pop_size,
        sampling=FloatRandomSampling(),
        crossover=SBX(prob=0.9, eta=15),
        mutation=PM(eta=20),
        eliminate_duplicates=True,
    )

    result = minimize(
        problem, algorithm, get_termination("n_gen", n_gen), seed=seed, save_history=False, verbose=False
    )
    return result, ctx
