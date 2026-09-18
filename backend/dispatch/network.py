"""Builds the S -> W -> F -> C (+ W -> C direct) network and verifies the
structural facts the whole project depends on.

Every fact in the brief's "verify on load, fail loudly if false" list is
checked in `verify_dataset_assumptions`. `build_network` raises if any
customer is unreachable from every supplier.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from dispatch.config import GROUND_MODES, MAX_GROUND_MODE_COST_CO2_CORR
from dispatch.data_loader import Dataset, load_dataset

# Beta-prior strength used to turn a route's single Route_Reliability point
# estimate into weak Beta(alpha, beta) parameters for Monte Carlo sampling.
# This is independent of, and much weaker than, the supplier-level
# Beta-Binomial posterior fit from shipment history in reliability.py.
_ROUTE_RELIABILITY_PRIOR_STRENGTH = 20.0


class DatasetAssumptionError(RuntimeError):
    """Raised when a structural fact the model depends on does not hold."""


@dataclass(frozen=True)
class AssumptionReport:
    supplier_capacity_min_days_cover: float
    supplier_capacity_max_days_cover: float
    factory_capacity_per_day: float
    factory_demand_per_day: float
    demand_cv: float
    ground_mode_cost_co2_corr: float
    all_mode_cost_co2_corr: float

    def summary(self) -> str:
        return (
            "Dataset assumptions verified:\n"
            f"  supplier capacity days-of-cover: {self.supplier_capacity_min_days_cover:.1f} "
            f"- {self.supplier_capacity_max_days_cover:.1f} (not binding)\n"
            f"  factory capacity/day: {self.factory_capacity_per_day:.0f} vs "
            f"demand/day: {self.factory_demand_per_day:.0f} "
            f"({self.factory_capacity_per_day / self.factory_demand_per_day:.1f}x headroom)\n"
            f"  demand CV: {self.demand_cv:.3f} (ETS is appropriate, not LSTM)\n"
            f"  ground-mode cost/CO2 correlation: {self.ground_mode_cost_co2_corr:+.3f} "
            f"(< {MAX_GROUND_MODE_COST_CO2_CORR}, the trade-off is real)\n"
            f"  all-mode cost/CO2 correlation: {self.all_mode_cost_co2_corr:+.3f} "
            "(air is both costly and dirty; expected to be high)"
        )


def verify_dataset_assumptions(ds: Dataset | None = None) -> AssumptionReport:
    """Verify the load-bearing facts in the project brief. Raises
    DatasetAssumptionError with the offending numbers if any fail.
    """
    ds = ds or load_dataset()
    errors: list[str] = []

    # --- Supplier capacity is not binding -----------------------------
    demand_by_product = ds.demand.groupby(["Customer_ID", "Product_ID"])["Demand_Quantity"].mean()
    finished_daily_demand = demand_by_product.groupby("Product_ID").sum()

    component_daily_demand = pd.Series(dtype=float)
    for _, row in ds.bill_of_materials.iterrows():
        fin, comp, qty = row["Finished_Product_ID"], row["Component_Product_ID"], row["Qty_Per_Finished_Unit"]
        contribution = finished_daily_demand.get(fin, 0.0) * qty
        component_daily_demand[comp] = component_daily_demand.get(comp, 0.0) + contribution

    daily_demand_by_product = pd.concat([finished_daily_demand, component_daily_demand])
    daily_demand_by_product = daily_demand_by_product.groupby(daily_demand_by_product.index).sum()

    capacity_by_product = ds.suppliers.groupby("Product_ID")["Capacity"].sum()
    sourced_products = capacity_by_product.index
    days_of_cover = capacity_by_product / daily_demand_by_product.reindex(sourced_products)
    days_of_cover = days_of_cover.dropna()

    min_cover, max_cover = float(days_of_cover.min()), float(days_of_cover.max())
    if min_cover < 5.0:
        errors.append(
            f"supplier capacity appears binding: min days-of-cover {min_cover:.1f} < 5 "
            f"(product {days_of_cover.idxmin()}). Diversification cap logic assumes ample capacity."
        )

    # --- Factory capacity is not binding -------------------------------
    factory_capacity = float(ds.factories["Production_Capacity"].sum())
    factory_demand = float(ds.factories["Daily_Demand"].sum())
    if factory_capacity < 2 * factory_demand:
        errors.append(
            f"factory capacity {factory_capacity:.0f}/day is not comfortably above "
            f"demand {factory_demand:.0f}/day"
        )

    # --- Demand CV is low, no trend: ETS is appropriate -----------------
    demand_stats = ds.demand.groupby(["Customer_ID", "Product_ID"])["Demand_Quantity"].agg(["mean", "std"])
    demand_stats = demand_stats[demand_stats["mean"] > 0]
    cv = float((demand_stats["std"] / demand_stats["mean"]).mean())
    if cv > 0.5:
        errors.append(f"demand CV {cv:.3f} is too high for ETS to be a defensible choice (expected ~0.18)")

    # --- Ground-mode cost/CO2 correlation stays below the threshold -----
    routes = ds.routes
    ground = routes[routes["Transport_Mode"].isin(GROUND_MODES)]
    ground_corr = float(np.corrcoef(ground["Cost_per_km_per_ton"], ground["CO2_per_ton_km"])[0, 1])
    all_corr = float(np.corrcoef(routes["Cost_per_km_per_ton"], routes["CO2_per_ton_km"])[0, 1])
    if abs(ground_corr) >= MAX_GROUND_MODE_COST_CO2_CORR:
        errors.append(
            f"ground-mode cost/CO2 correlation {ground_corr:+.3f} is at or above "
            f"{MAX_GROUND_MODE_COST_CO2_CORR}: the cost-vs-carbon trade-off has collapsed"
        )

    if errors:
        raise DatasetAssumptionError(
            "Dataset assumption verification failed:\n- " + "\n- ".join(errors)
        )

    return AssumptionReport(
        supplier_capacity_min_days_cover=min_cover,
        supplier_capacity_max_days_cover=max_cover,
        factory_capacity_per_day=factory_capacity,
        factory_demand_per_day=factory_demand,
        demand_cv=cv,
        ground_mode_cost_co2_corr=ground_corr,
        all_mode_cost_co2_corr=all_corr,
    )


def build_network(ds: Dataset | None = None) -> nx.MultiDiGraph:
    """Build the routing network as a MultiDiGraph (parallel edges = distinct
    routes/modes between the same node pair, keyed by Route_ID).

    Raises DatasetAssumptionError if any customer is unreachable from every
    supplier.
    """
    ds = ds or load_dataset()
    g = nx.MultiDiGraph()

    for _, row in ds.node_coordinates.iterrows():
        g.add_node(
            row["Node_ID"],
            node_type=row["Node_Type"],
            city=row["City"],
            lat=float(row["Latitude"]),
            lon=float(row["Longitude"]),
            airport_iata=row.get("Nearest_Airport_IATA"),
            rail_hub=row.get("Rail_Hub") == "YES",
        )

    for _, row in ds.routes.iterrows():
        reliability = float(row["Route_Reliability"])
        strength = _ROUTE_RELIABILITY_PRIOR_STRENGTH
        g.add_edge(
            row["Origin"],
            row["Destination"],
            key=row["Route_ID"],
            route_id=row["Route_ID"],
            cost_per_ton=float(row["Distance_km"]) * float(row["Cost_per_km_per_ton"]),
            co2_per_ton=float(row["Distance_km"]) * float(row["CO2_per_ton_km"]),
            distance_km=float(row["Distance_km"]),
            transit_days=float(row["Transit_Time_days"]) + float(row["Terminal_Dwell_days"]),
            reliability=reliability,
            reliability_alpha=reliability * strength,
            reliability_beta=(1.0 - reliability) * strength,
            mode=row["Transport_Mode"],
            fleet_tier=row["Fleet_Tier"],
            route_status=row["Route_Status"],
        )

    suppliers = [n for n, d in g.nodes(data=True) if d["node_type"] == "Supplier"]
    customers = [n for n, d in g.nodes(data=True) if d["node_type"] == "Customer"]

    reachable_from_suppliers: set[str] = set()
    for s in suppliers:
        reachable_from_suppliers |= nx.descendants(g, s)

    unreachable = [c for c in customers if c not in reachable_from_suppliers]
    if unreachable:
        raise DatasetAssumptionError(
            f"{len(unreachable)} customer(s) unreachable from every supplier: {unreachable}"
        )

    return g
