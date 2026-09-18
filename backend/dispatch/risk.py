"""Monte Carlo inventory-risk simulation.

N=1000 seeded trials per node-product. Demand is sampled from forecast
residual bootstraps (customers) or a CV-scaled normal (warehouses/factories,
which have no per-series ETS model); replenishment arrivals are sampled from
supplier reliability posteriors (reliability.py, for supplier->warehouse
legs) or route-level reliability (network.py edges, for every other leg) and
from the trained ETA delay-day classifier (eta.py). The inventory ledger is
then rolled forward under a simple (reorder-point, order-up-to) policy and
P(stockout) / CVaR-90 are read off the simulated trajectories.

This module never reads the Inventory_Risk column -- that would be training
on the label we are supposed to be deriving independently, by simulation.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from dispatch.config import SEED
from dispatch.data_loader import Dataset, load_dataset
from dispatch.eta import ETAModel, predict_delay_distribution, train_eta_model
from dispatch.forecast import fit_ets, series_for_customer_product
from dispatch.network import build_network
from dispatch.reliability import fit_supplier_posteriors

HORIZON_DAYS_DEFAULT = 14
N_SAMPLES_DEFAULT = 1000
STOCKOUT_PENALTY_MULTIPLIER = 1.5  # lost-sales premium over unit replacement cost
SIM_NODE_TYPES = ("Warehouse", "Factory", "Customer")  # Suppliers are sources, not replenishment sinks


@dataclass(frozen=True)
class NodeProductParams:
    node_id: str
    product_id: str
    node_type: str
    initial_inventory: float
    safety_stock: float
    mean_daily_demand: float
    std_daily_demand: float
    base_lead_time: float
    delay_probs: np.ndarray  # P(delay = 0/1/2)
    reliability_alpha: float
    reliability_beta: float
    unit_cost: float


@dataclass(frozen=True)
class NodeProductRisk:
    node_id: str
    product_id: str
    node_type: str
    p_stockout: float
    expected_stockout_units: float


@dataclass(frozen=True)
class RiskReport:
    n_samples: int
    horizon_days: int
    node_reports: list[NodeProductRisk]
    cvar_90_cost: float
    mean_total_cost: float


def _unit_cost_proxies(ds: Dataset) -> dict[str, float]:
    avg_component_cost = ds.suppliers.groupby("Product_ID")["Unit_Cost"].mean().to_dict()
    proxies = dict(avg_component_cost)
    for row in ds.bill_of_materials.groupby("Finished_Product_ID"):
        finished_id, rows = row
        cost = sum(r.Qty_Per_Finished_Unit * avg_component_cost.get(r.Component_Product_ID, 0.0) for r in rows.itertuples())
        proxies[finished_id] = cost
    return proxies


def _representative_inbound_edge(g: nx.MultiDiGraph, ds: Dataset, node_id: str, product_id: str, node_type: str):
    component_products = set(
        ds.products.loc[ds.products.Product_Type.isin(["raw_material", "component"]), "Product_ID"]
    )
    is_component = product_id in component_products

    candidates = []
    for u, v, key, data in g.in_edges(node_id, keys=True, data=True):
        origin_type = g.nodes[u]["node_type"]
        if node_type == "Warehouse":
            wanted_origin = "Supplier" if is_component else "Factory"
        elif node_type == "Factory":
            wanted_origin = "Warehouse"
        elif node_type == "Customer":
            wanted_origin = None  # Factory or Warehouse both eligible
        else:
            continue
        if wanted_origin is not None and origin_type != wanted_origin:
            continue
        if wanted_origin is None and origin_type not in ("Factory", "Warehouse"):
            continue
        candidates.append((u, v, key, data))

    if not candidates:
        return None
    # Deterministic pick: highest declared reliability, tie-broken by route_id.
    candidates.sort(key=lambda c: (-c[3]["reliability"], c[3]["route_id"]))
    return candidates[0]


def _build_node_product_params(
    ds: Dataset,
    g: nx.MultiDiGraph,
    twin: pd.DataFrame,
    eta_model: ETAModel,
    supplier_posteriors: dict,
    unit_cost_proxies: dict[str, float],
    global_demand_cv: float,
) -> list[NodeProductParams]:
    rows = twin[twin.Node_Type.isin(SIM_NODE_TYPES)]
    params: list[NodeProductParams] = []

    # Warehouse/Factory rows carry Expected_Demand=0 in the twin snapshot --
    # that field is customer-facing consumption, not pass-through flow. Their
    # actual daily outflow is the historical average Outgoing_Quantity from
    # the inventory ledger.
    avg_outgoing = ds.inventory.groupby(["Node_ID", "Product_ID"])["Outgoing_Quantity"].mean()

    for row in rows.itertuples():
        node_id, product_id, node_type = row.Node_ID, row.Product_ID, row.Node_Type

        if node_type == "Customer":
            series = series_for_customer_product(ds, node_id, product_id)
            if series.empty:
                continue
            fitted = fit_ets(series)
            residuals = series.values - fitted.fittedvalues
            mean_demand = float(np.asarray(fitted.forecast(1))[0])
            std_demand = float(np.std(residuals)) if len(residuals) > 1 else mean_demand * global_demand_cv
        else:
            mean_demand = float(avg_outgoing.get((node_id, product_id), 0.0))
            std_demand = mean_demand * global_demand_cv

        if mean_demand <= 0:
            continue  # nothing flows through this node-product; not a meaningful stockout candidate

        edge = _representative_inbound_edge(g, ds, node_id, product_id, node_type)
        if edge is None:
            continue
        u, v, key, data = edge
        origin_type = g.nodes[u]["node_type"]

        if origin_type == "Supplier" and u in supplier_posteriors:
            post = supplier_posteriors[u]
            alpha, beta = post.alpha, post.beta
        else:
            alpha, beta = data["reliability_alpha"], data["reliability_beta"]

        delay_dist = predict_delay_distribution(
            eta_model,
            ds,
            origin=u,
            destination=v,
            supplier_id=u if origin_type == "Supplier" else "internal",
            quantity=max(mean_demand, 1),
            departure_date="2026-09-01",  # representative weekday; delay model is only weakly weekday-sensitive
        )
        delay_probs = np.array([delay_dist.get(0, 0.0), delay_dist.get(1, 0.0), delay_dist.get(2, 0.0)])
        delay_probs = delay_probs / delay_probs.sum()

        base_lead_time = float(row.Expected_Lead_Time) if row.Expected_Lead_Time else data["transit_days"]

        params.append(
            NodeProductParams(
                node_id=node_id,
                product_id=product_id,
                node_type=node_type,
                initial_inventory=float(row.Inventory_Level),
                safety_stock=float(row.Safety_Stock),
                mean_daily_demand=mean_demand,
                std_daily_demand=max(std_demand, 1e-6),
                base_lead_time=max(base_lead_time, 1.0),
                delay_probs=delay_probs,
                reliability_alpha=alpha,
                reliability_beta=beta,
                unit_cost=unit_cost_proxies.get(product_id, 1.0),
            )
        )
    return params


def _simulate_node_product(p: NodeProductParams, horizon_days: int, n_samples: int, seed: int) -> tuple[NodeProductRisk, np.ndarray]:
    """Vectorised across the N sample axis; loops only over horizon days."""
    rng = np.random.default_rng(seed)

    lead_time = int(round(p.base_lead_time))
    buffer_days = lead_time + 3  # room for the max eta delay (+2) plus one day slack
    total_days = horizon_days + buffer_days

    # Standard (s, S) policy: the reorder point must cover expected demand
    # across the lead time itself, with safety stock as the buffer against
    # variance during that window -- not just the safety stock alone, which
    # would trigger a reorder only after the lead-time cover is already gone.
    reorder_point = p.safety_stock + p.mean_daily_demand * lead_time
    order_up_to = reorder_point + p.mean_daily_demand * 5  # ~5 extra days of cover per order cycle

    inventory = np.full(n_samples, p.initial_inventory, dtype=float)
    scheduled_arrivals = np.zeros((n_samples, total_days), dtype=float)

    # Warm start: the twin snapshot's on-hand inventory doesn't include
    # goods already in transit from orders placed before day 0. Without a
    # pipeline, every trajectory is forced into an artificial stockout for
    # the first `lead_time` days regardless of policy. Seed a steady-state
    # pipeline arriving at the mean demand rate over the first lead-time
    # days, as a deterministic stand-in for "orders already in flight".
    if lead_time > 0:
        scheduled_arrivals[:, :lead_time] = p.mean_daily_demand

    # Tracks, per sample, the last day an *explicitly placed* reorder is due
    # to arrive -- kept separate from the warm-start pipeline above so a
    # depleting sample can still reorder during the warm-start window
    # instead of being blocked until it subsides.
    order_open_until_day = np.full(n_samples, -1, dtype=int)

    stockout_units = np.zeros((n_samples, horizon_days), dtype=float)
    stockout_flag = np.zeros((n_samples, horizon_days), dtype=bool)

    demand_draws = rng.normal(p.mean_daily_demand, p.std_daily_demand, size=(n_samples, horizon_days))
    demand_draws = np.clip(demand_draws, 0, None)

    for day in range(horizon_days):
        inventory += scheduled_arrivals[:, day]

        demand_today = demand_draws[:, day]
        shortfall = np.maximum(demand_today - inventory, 0.0)
        stockout_units[:, day] = shortfall
        stockout_flag[:, day] = shortfall > 1e-9
        inventory = np.maximum(inventory - demand_today, 0.0)

        needs_reorder = (inventory <= reorder_point) & (day > order_open_until_day)
        n_reorder = int(needs_reorder.sum())
        if n_reorder > 0:
            order_qty = np.maximum(order_up_to - inventory[needs_reorder], 0.0)
            fulfil_fraction = rng.beta(p.reliability_alpha, p.reliability_beta, size=n_reorder)
            delay = rng.choice(np.array([0, 1, 2]), size=n_reorder, p=p.delay_probs)
            arrival_day = day + lead_time + delay
            arrival_day = np.clip(arrival_day, 0, total_days - 1)

            reorder_idx = np.nonzero(needs_reorder)[0]
            np.add.at(scheduled_arrivals, (reorder_idx, arrival_day), order_qty * fulfil_fraction)
            order_open_until_day[reorder_idx] = arrival_day

    p_stockout = float(stockout_flag.any(axis=1).mean())
    expected_stockout_units = float(stockout_units.sum(axis=1).mean())

    cost_per_sample = stockout_units.sum(axis=1) * p.unit_cost * STOCKOUT_PENALTY_MULTIPLIER

    return (
        NodeProductRisk(
            node_id=p.node_id,
            product_id=p.product_id,
            node_type=p.node_type,
            p_stockout=p_stockout,
            expected_stockout_units=expected_stockout_units,
        ),
        cost_per_sample,
    )


def run_monte_carlo(
    ds: Dataset | None = None,
    g: nx.MultiDiGraph | None = None,
    eta_model: ETAModel | None = None,
    horizon_days: int = HORIZON_DAYS_DEFAULT,
    n_samples: int = N_SAMPLES_DEFAULT,
    seed: int = SEED,
) -> RiskReport:
    ds = ds or load_dataset()
    g = g if g is not None else build_network(ds)
    eta_model = eta_model or train_eta_model(ds, seed=seed)

    supplier_posteriors = fit_supplier_posteriors(ds)
    unit_cost_proxies = _unit_cost_proxies(ds)

    demand_stats = ds.demand.groupby(["Customer_ID", "Product_ID"])["Demand_Quantity"].agg(["mean", "std"])
    demand_stats = demand_stats[demand_stats["mean"] > 0]
    global_cv = float((demand_stats["std"] / demand_stats["mean"]).mean())

    params_list = _build_node_product_params(
        ds, g, ds.twin_state, eta_model, supplier_posteriors, unit_cost_proxies, global_cv
    )

    node_reports: list[NodeProductRisk] = []
    total_cost_per_sample = np.zeros(n_samples, dtype=float)

    for i, p in enumerate(params_list):
        report, cost = _simulate_node_product(p, horizon_days, n_samples, seed=seed + i)
        node_reports.append(report)
        total_cost_per_sample += cost

    sorted_cost = np.sort(total_cost_per_sample)
    tail_start = int(np.ceil(0.90 * n_samples))
    tail = sorted_cost[tail_start:]
    cvar_90 = float(tail.mean()) if len(tail) > 0 else float(sorted_cost[-1])

    return RiskReport(
        n_samples=n_samples,
        horizon_days=horizon_days,
        node_reports=node_reports,
        cvar_90_cost=cvar_90,
        mean_total_cost=float(total_cost_per_sample.mean()),
    )
