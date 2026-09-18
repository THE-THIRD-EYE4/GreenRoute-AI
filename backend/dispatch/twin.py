"""Discrete-event digital twin: one tick = one day, six state layers.

The twin never overwrites its baseline snapshot -- every tick appends a new
TwinState to an append-only log. Shipments advance on a SAMPLED transit time
(drawn once at departure from the trained ETA delay classifier), not the
planned one, so realized lead time can and does drift from plan. A scenario
(scenarios_augmented.csv) perturbs supplier capacity, lane status/lead time,
or customer demand for its Duration_days window. Re-optimization fires when
a node's projected days of cover falls below its safety-stock horizon, or a
lane's realized lead time has exceeded plan by 2+ days for 3 consecutive
ticks -- and the replan is solved against the twin's LIVE, perturbed
capacities/lead-times, never static historical averages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import networkx as nx
import numpy as np
import pandas as pd
import simpy

from dispatch.config import DATA_DIR, SEED
from dispatch.data_loader import Dataset, load_dataset
from dispatch.eta import ETAModel, predict_delay_distribution, train_eta_model
from dispatch.network import build_network
from dispatch.optimizer import ModelData, SolveResult, build_model_data, solve_min_cost

BASELINE_TIMESTAMP = "2026-08-30T06:00:00+05:30"
LATE_STREAK_TRIGGER = 3
LATE_THRESHOLD_DAYS = 2


@dataclass(frozen=True)
class InventoryState:
    node_id: str
    product_id: str
    level: float
    safety_stock: float
    avg_daily_consumption: float

    @property
    def days_of_cover(self) -> float:
        return self.level / self.avg_daily_consumption if self.avg_daily_consumption > 0 else float("inf")

    @property
    def horizon_days(self) -> float:
        """Days of cover the safety stock alone is meant to buy."""
        return self.safety_stock / self.avg_daily_consumption if self.avg_daily_consumption > 0 else float("inf")


@dataclass(frozen=True)
class InTransitShipment:
    shipment_id: str
    route_id: str
    product_id: str
    quantity: float
    origin: str
    destination: str
    departure_tick: int
    planned_arrival_tick: int
    sampled_arrival_tick: int
    status: str  # IN_TRANSIT / ARRIVED

    def progress_fraction(self, as_of_tick: int) -> float:
        span = self.sampled_arrival_tick - self.departure_tick
        if span <= 0:
            return 1.0
        return float(np.clip((as_of_tick - self.departure_tick) / span, 0.0, 1.0))


@dataclass(frozen=True)
class SupplierState:
    supplier_id: str
    status: str  # ONLINE / DEGRADED / OFFLINE
    remaining_capacity_frac: float
    posterior_alpha: float
    posterior_beta: float


@dataclass(frozen=True)
class LaneState:
    route_id: str
    status: str  # AVAILABLE / CONGESTED / CLOSED
    planned_transit_days: float
    realized_transit_days: float | None
    consecutive_late_ticks: int


@dataclass(frozen=True)
class DemandState:
    customer_id: str
    product_id: str
    observed: float
    forecast: float


@dataclass(frozen=True)
class VehicleState:
    vehicle_id: str
    node_id: str
    load_factor: float
    hours_driven_today: float
    next_stop: str | None


@dataclass(frozen=True)
class TwinState:
    tick: int
    timestamp: str
    inventory: tuple[InventoryState, ...]
    in_transit: tuple[InTransitShipment, ...]
    supplier: tuple[SupplierState, ...]
    lane: tuple[LaneState, ...]
    demand: tuple[DemandState, ...]
    vehicle: tuple[VehicleState, ...]
    reoptimization_triggered: bool
    trigger_reasons: tuple[str, ...]
    active_scenarios: tuple[str, ...]


@dataclass
class _ScenarioOverride:
    scenario_id: str
    scenario_type: str
    target: str
    ticks_remaining: int
    severity: float
    capacity_reduction: float
    lead_time_increase: int
    demand_change: float
    route_status: str


@dataclass(frozen=True)
class ReoptimizationResult:
    tick: int
    trigger_reasons: tuple[str, ...]
    plan: SolveResult
    delta_cost: float
    delta_co2: float


class DigitalTwin:
    def __init__(
        self,
        ds: Dataset | None = None,
        g: nx.MultiDiGraph | None = None,
        eta_model: ETAModel | None = None,
        seed: int = SEED,
    ):
        self.ds = ds or load_dataset()
        self.g = g if g is not None else build_network(self.ds)
        self.eta_model = eta_model or train_eta_model(self.ds, seed=seed)
        self.seed = seed
        self._rng = np.random.default_rng(seed)

        self._route_lane_map = self._build_original_route_lane_map()
        self._active_overrides: dict[str, _ScenarioOverride] = {}
        self._lane_late_streak: dict[str, int] = {}
        self._next_shipment_id = 0
        self._vehicle_pool = list(self.ds.vehicles.Vehicle_ID)

        self.baseline = self._build_baseline_state()
        self.log: list[TwinState] = [self.baseline]
        self.reoptimizations: list[ReoptimizationResult] = []

    # -- setup -----------------------------------------------------------

    def _build_original_route_lane_map(self) -> dict[str, list[str]]:
        """scenarios_augmented.csv references pre-augmentation lane ids
        (R001-R045); map each to every augmented Route_ID sharing that
        (Origin, Destination) pair, since closing a lane closes it for all
        modes on it.
        """
        original = pd.read_csv(DATA_DIR / "original" / "transportation_routes.csv")
        od_to_augmented: dict[tuple[str, str], list[str]] = {}
        for row in self.ds.routes.itertuples():
            od_to_augmented.setdefault((row.Origin, row.Destination), []).append(row.Route_ID)
        mapping: dict[str, list[str]] = {}
        for row in original.itertuples():
            mapping[row.Route_ID] = od_to_augmented.get((row.Origin, row.Destination), [])
        return mapping

    def _build_baseline_state(self) -> TwinState:
        twin_df = self.ds.twin_state

        avg_outgoing = self.ds.inventory.groupby(["Node_ID", "Product_ID"])["Outgoing_Quantity"].mean()

        inventory: list[InventoryState] = []
        for row in twin_df[twin_df.Node_Type.isin(("Supplier", "Warehouse", "Factory", "Customer"))].itertuples():
            consumption = avg_outgoing.get((row.Node_ID, row.Product_ID), row.Expected_Demand or 0.0)
            inventory.append(
                InventoryState(
                    node_id=row.Node_ID,
                    product_id=row.Product_ID,
                    level=float(row.Inventory_Level),
                    safety_stock=float(row.Safety_Stock),
                    avg_daily_consumption=float(consumption) if consumption else 0.0,
                )
            )

        supplier_rows = self.ds.suppliers.groupby("Supplier_ID").agg(
            Reliability=("Reliability", "mean"), Capacity=("Capacity", "sum")
        )
        suppliers = tuple(
            SupplierState(
                supplier_id=sid,
                status="ONLINE",
                remaining_capacity_frac=1.0,
                posterior_alpha=float(row.Reliability) * 15.0,
                posterior_beta=(1 - float(row.Reliability)) * 15.0,
            )
            for sid, row in supplier_rows.iterrows()
        )

        lanes = tuple(
            LaneState(
                route_id=row.Route_ID,
                status=row.Route_Status,
                planned_transit_days=float(row.Transit_Time_days) + float(row.Terminal_Dwell_days),
                realized_transit_days=None,
                consecutive_late_ticks=0,
            )
            for row in self.ds.routes.itertuples()
        )

        demand = tuple(
            DemandState(customer_id=c, product_id=p, observed=0.0, forecast=0.0)
            for c, p in zip(self.ds.customers.Customer_ID, self.ds.customers.Product_ID)
        )

        vehicles = tuple(
            VehicleState(
                vehicle_id=row.Vehicle_ID, node_id=row.Home_Depot, load_factor=0.0, hours_driven_today=0.0, next_stop=None
            )
            for row in self.ds.vehicles.itertuples()
        )

        return TwinState(
            tick=0,
            timestamp=BASELINE_TIMESTAMP,
            inventory=tuple(inventory),
            in_transit=(),
            supplier=suppliers,
            lane=lanes,
            demand=demand,
            vehicle=vehicles,
            reoptimization_triggered=False,
            trigger_reasons=(),
            active_scenarios=(),
        )

    # -- scenario application ---------------------------------------------

    def apply_scenario(self, scenario_id: str) -> None:
        row = self.ds.scenarios.loc[self.ds.scenarios.Scenario_ID == scenario_id]
        if row.empty:
            raise KeyError(f"Unknown scenario id: {scenario_id}")
        r = row.iloc[0]
        self._active_overrides[scenario_id] = _ScenarioOverride(
            scenario_id=scenario_id,
            scenario_type=r["Scenario_Type"],
            target=r["Affected_Node"],
            ticks_remaining=int(r["Duration_days"]),
            severity=float(r["Severity"]),
            capacity_reduction=float(r["Capacity_Reduction"]),
            lead_time_increase=int(r["Lead_Time_Increase"]),
            demand_change=float(r["Demand_Change"]),
            route_status=r["Route_Status"],
        )

    def reset_to_baseline(self) -> None:
        self._active_overrides.clear()
        self._lane_late_streak.clear()
        self.log = [self.baseline]
        self.reoptimizations = []

    # -- tick advance -------------------------------------------------------

    def run_ticks(self, n: int) -> list[TwinState]:
        env = simpy.Environment()
        produced: list[TwinState] = []

        def process():
            for _ in range(n):
                produced.append(self._advance_one_day())
                yield env.timeout(1)

        env.process(process())
        env.run()
        return produced

    def _advance_one_day(self) -> TwinState:
        prev = self.log[-1]
        new_tick = prev.tick + 1
        timestamp = (
            datetime.fromisoformat(BASELINE_TIMESTAMP) + timedelta(days=new_tick)
        ).isoformat()

        for ov in self._active_overrides.values():
            ov.ticks_remaining -= 1
        self._active_overrides = {k: v for k, v in self._active_overrides.items() if v.ticks_remaining > 0}
        active_ids = tuple(sorted(self._active_overrides))

        lane_status, lane_leadtime_bump = self._lane_overrides()
        supplier_capacity_frac, supplier_status = self._supplier_overrides()
        demand_multiplier = self._demand_overrides()

        inventory_by_key = {(inv.node_id, inv.product_id): inv for inv in prev.inventory}

        # -- demand: sample today's draw, reduce inventory (customer nodes
        # pull from their representative upstream node's finished stock)
        new_demand: list[DemandState] = []
        for d in prev.demand:
            base = next((i.avg_daily_consumption for i in prev.inventory if i.node_id == d.customer_id and i.product_id == d.product_id), 0.0)
            mult = demand_multiplier.get(d.customer_id, 1.0)
            forecast = base * mult
            observed = max(0.0, self._rng.normal(forecast, forecast * 0.176 if forecast else 0.0))
            new_demand.append(DemandState(d.customer_id, d.product_id, observed=observed, forecast=forecast))

        # -- in-transit shipments: advance; arrivals deposit into destination inventory
        new_in_transit: list[InTransitShipment] = []
        arrivals: dict[tuple[str, str], float] = {}
        lane_realized: dict[str, list[float]] = {}
        for s in prev.in_transit:
            if s.status == "ARRIVED":
                continue
            if new_tick >= s.sampled_arrival_tick:
                arrivals[(s.destination, s.product_id)] = arrivals.get((s.destination, s.product_id), 0.0) + s.quantity
                lane_realized.setdefault(s.route_id, []).append(float(s.sampled_arrival_tick - s.departure_tick))
                new_in_transit.append(
                    InTransitShipment(
                        s.shipment_id, s.route_id, s.product_id, s.quantity, s.origin, s.destination,
                        s.departure_tick, s.planned_arrival_tick, s.sampled_arrival_tick, status="ARRIVED",
                    )
                )
            else:
                new_in_transit.append(s)

        # -- dispatch one new replenishment shipment per warehouse/factory/customer
        # node whose projected cover is running low (keeps the pipeline moving tick over tick)
        newly_dispatched: list[InTransitShipment] = []
        for inv in prev.inventory:
            if inv.node_id.startswith("S") or inv.avg_daily_consumption <= 0:
                continue
            if inv.days_of_cover > inv.horizon_days + 3:
                continue
            edge = self._pick_inbound_edge(inv.node_id, inv.product_id)
            if edge is None:
                continue
            u, v, key, data = edge
            route_id = data["route_id"]
            if lane_status.get(route_id, data["route_status"]) == "CLOSED":
                continue
            origin_type = self.g.nodes[u]["node_type"]
            if origin_type == "Supplier":
                sup = next((sp for sp in prev.supplier if sp.supplier_id == u), None)
                alpha, beta = (sup.posterior_alpha, sup.posterior_beta) if sup else (data["reliability_alpha"], data["reliability_beta"])
            else:
                alpha, beta = data["reliability_alpha"], data["reliability_beta"]

            delay_dist = predict_delay_distribution(
                self.eta_model, self.ds, origin=u, destination=v,
                supplier_id=u if origin_type == "Supplier" else "internal",
                quantity=max(inv.avg_daily_consumption, 1), departure_date="2026-09-01",
            )
            probs = np.array([delay_dist.get(0, 0), delay_dist.get(1, 0), delay_dist.get(2, 0)])
            probs = probs / probs.sum()
            delay = int(self._rng.choice([0, 1, 2], p=probs)) + lane_leadtime_bump.get(route_id, 0)
            fulfil = float(self._rng.beta(alpha, beta))
            planned_transit = data["transit_days"]
            order_qty = max(inv.horizon_days * inv.avg_daily_consumption, inv.avg_daily_consumption * 3)

            self._next_shipment_id += 1
            newly_dispatched.append(
                InTransitShipment(
                    shipment_id=f"TWSH{self._next_shipment_id:05d}",
                    route_id=route_id, product_id=inv.product_id, quantity=order_qty * fulfil,
                    origin=u, destination=v, departure_tick=new_tick,
                    planned_arrival_tick=new_tick + int(round(planned_transit)),
                    sampled_arrival_tick=new_tick + int(round(planned_transit)) + delay,
                    status="IN_TRANSIT",
                )
            )
        new_in_transit.extend(newly_dispatched)

        new_inventory: list[InventoryState] = []
        for inv in prev.inventory:
            arrived_qty = arrivals.get((inv.node_id, inv.product_id), 0.0)
            demand_hit = 0.0
            if inv.node_id.startswith("C"):
                demand_hit = next((d.observed for d in new_demand if d.customer_id == inv.node_id and d.product_id == inv.product_id), 0.0)
            else:
                demand_hit = inv.avg_daily_consumption
            new_level = max(0.0, inv.level + arrived_qty - demand_hit)
            new_inventory.append(InventoryState(inv.node_id, inv.product_id, new_level, inv.safety_stock, inv.avg_daily_consumption))

        # -- lanes: realized transit + late streak
        new_lanes: list[LaneState] = []
        trigger_reasons: list[str] = []
        for lane in prev.lane:
            realized_list = lane_realized.get(lane.route_id)
            realized = float(np.mean(realized_list)) if realized_list else lane.realized_transit_days
            status = lane_status.get(lane.route_id, lane.status)
            if realized is not None and realized > lane.planned_transit_days + LATE_THRESHOLD_DAYS:
                streak = self._lane_late_streak.get(lane.route_id, 0) + 1
            else:
                streak = 0
            self._lane_late_streak[lane.route_id] = streak
            if streak >= LATE_STREAK_TRIGGER:
                trigger_reasons.append(f"lane {lane.route_id} realized lead time >plan+{LATE_THRESHOLD_DAYS}d for {streak} ticks")
            new_lanes.append(LaneState(lane.route_id, status, lane.planned_transit_days, realized, streak))

        for inv in new_inventory:
            if inv.avg_daily_consumption > 0 and inv.days_of_cover < inv.horizon_days:
                trigger_reasons.append(f"{inv.node_id}/{inv.product_id} projected cover {inv.days_of_cover:.1f}d < safety horizon {inv.horizon_days:.1f}d")

        new_suppliers = tuple(
            SupplierState(
                sp.supplier_id,
                supplier_status.get(sp.supplier_id, sp.status),
                supplier_capacity_frac.get(sp.supplier_id, sp.remaining_capacity_frac),
                sp.posterior_alpha,
                sp.posterior_beta,
            )
            for sp in prev.supplier
        )

        new_vehicles = self._advance_vehicles(prev.vehicle, new_in_transit)

        triggered = len(trigger_reasons) > 0
        new_state = TwinState(
            tick=new_tick,
            timestamp=timestamp,
            inventory=tuple(new_inventory),
            in_transit=tuple(new_in_transit),
            supplier=new_suppliers,
            lane=tuple(new_lanes),
            demand=tuple(new_demand),
            vehicle=new_vehicles,
            reoptimization_triggered=triggered,
            trigger_reasons=tuple(trigger_reasons),
            active_scenarios=active_ids,
        )
        self.log.append(new_state)

        if triggered:
            self._reoptimize(new_state, tuple(trigger_reasons))

        return new_state

    # -- helpers ------------------------------------------------------------

    def _lane_overrides(self) -> tuple[dict[str, str], dict[str, int]]:
        status: dict[str, str] = {}
        bump: dict[str, int] = {}
        for ov in self._active_overrides.values():
            if not ov.target.startswith("R"):
                continue
            for route_id in self._route_lane_map.get(ov.target, []):
                if ov.route_status != "UNCHANGED":
                    status[route_id] = ov.route_status
                bump[route_id] = bump.get(route_id, 0) + ov.lead_time_increase
        return status, bump

    def _supplier_overrides(self) -> tuple[dict[str, float], dict[str, str]]:
        frac: dict[str, float] = {}
        status: dict[str, str] = {}
        for ov in self._active_overrides.values():
            if not ov.target.startswith("S"):
                continue
            # "Supplier outage" scenarios carry the capacity hit in Severity
            # (Capacity_Reduction is 0 for that scenario type); "Supplier
            # capacity reduction" carries it in Capacity_Reduction directly.
            reduction = ov.capacity_reduction if ov.capacity_reduction > 0 else (
                ov.severity if ov.scenario_type == "Supplier outage" else 0.0
            )
            remaining = max(0.0, 1.0 - reduction)
            frac[ov.target] = remaining
            status[ov.target] = "OFFLINE" if remaining <= 0.01 else ("DEGRADED" if remaining < 1.0 else "ONLINE")
        return frac, status

    def _demand_overrides(self) -> dict[str, float]:
        mult: dict[str, float] = {}
        for ov in self._active_overrides.values():
            if not ov.target.startswith("C"):
                continue
            mult[ov.target] = mult.get(ov.target, 1.0) * (1.0 + ov.demand_change)
        return mult

    def _pick_inbound_edge(self, node_id: str, product_id: str):
        component_products = set(
            self.ds.products.loc[self.ds.products.Product_Type.isin(["raw_material", "component"]), "Product_ID"]
        )
        is_component = product_id in component_products
        node_type = self.g.nodes[node_id]["node_type"]
        candidates = []
        for u, v, key, data in self.g.in_edges(node_id, keys=True, data=True):
            origin_type = self.g.nodes[u]["node_type"]
            if node_type == "Warehouse":
                wanted = "Supplier" if is_component else "Factory"
            elif node_type == "Factory":
                wanted = "Warehouse"
            elif node_type == "Customer":
                wanted = None
            else:
                continue
            if wanted is not None and origin_type != wanted:
                continue
            if wanted is None and origin_type not in ("Factory", "Warehouse"):
                continue
            candidates.append((u, v, key, data))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (-c[3]["reliability"], c[3]["route_id"]))
        return candidates[0]

    def _advance_vehicles(self, prev_vehicles: tuple[VehicleState, ...], in_transit: list[InTransitShipment]) -> tuple[VehicleState, ...]:
        active_by_vehicle: dict[str, InTransitShipment] = {}
        pool = self._vehicle_pool
        for i, s in enumerate(s for s in in_transit if s.status == "IN_TRANSIT"):
            vid = pool[i % len(pool)]
            active_by_vehicle[vid] = s

        vehicle_capacity = dict(zip(self.ds.vehicles.Vehicle_ID, self.ds.vehicles.Payload_Capacity_kg))
        new_vehicles = []
        for v in prev_vehicles:
            s = active_by_vehicle.get(v.vehicle_id)
            if s is None:
                new_vehicles.append(VehicleState(v.vehicle_id, v.node_id, 0.0, 0.0, None))
            else:
                cap = vehicle_capacity.get(v.vehicle_id, 1.0) or 1.0
                load_factor = min(1.0, (s.quantity * 1.0) / cap)
                new_vehicles.append(VehicleState(v.vehicle_id, s.origin, load_factor, min(8.0, v.hours_driven_today + 1), s.destination))
        return tuple(new_vehicles)

    def _reoptimize(self, state: TwinState, reasons: tuple[str, ...]) -> ReoptimizationResult:
        """Re-solve the MILP against the twin's LIVE, perturbed capacities
        and lead times -- never the static historical averages the initial
        build_model_data() call would otherwise use.
        """
        live_md = self._live_model_data(state)
        baseline_plan = solve_min_cost(build_model_data(self.ds, self.g))
        live_plan = solve_min_cost(live_md)
        result = ReoptimizationResult(
            tick=state.tick,
            trigger_reasons=reasons,
            plan=live_plan,
            delta_cost=live_plan.cost - baseline_plan.cost,
            delta_co2=live_plan.co2_kg - baseline_plan.co2_kg,
        )
        self.reoptimizations.append(result)
        return result

    def _live_model_data(self, state: TwinState) -> ModelData:
        md = build_model_data(self.ds, self.g)
        supplier_frac = {sp.supplier_id: sp.remaining_capacity_frac for sp in state.supplier}
        offers = md.supplier_offers.copy()
        offers["Capacity"] = offers.apply(
            lambda r: r.Capacity * supplier_frac.get(r.Supplier_ID, 1.0), axis=1
        )
        return ModelData(
            legs=md.legs, legs_by_type=md.legs_by_type, component_products=md.component_products,
            finished_products=md.finished_products, unit_weight_kg=md.unit_weight_kg, supplier_offers=offers,
            customer_product=md.customer_product, demand=md.demand, warehouse_capacity=md.warehouse_capacity,
            factory_capacity=md.factory_capacity, bom=md.bom, total_demand=md.total_demand,
        )

    # -- reporting ------------------------------------------------------------

    def variance_table(self) -> list[dict]:
        """Rolling twin-vs-plan drift: for every lane with a realized
        observation in the log, planned vs realized transit time and the
        drift beyond threshold.
        """
        rows: dict[str, dict] = {}
        for state in self.log:
            for lane in state.lane:
                if lane.realized_transit_days is None:
                    continue
                rows[lane.route_id] = {
                    "route_id": lane.route_id,
                    "planned_transit_days": lane.planned_transit_days,
                    "realized_transit_days": lane.realized_transit_days,
                    "drift_days": lane.realized_transit_days - lane.planned_transit_days,
                    "consecutive_late_ticks": lane.consecutive_late_ticks,
                    "breached": lane.consecutive_late_ticks >= LATE_STREAK_TRIGGER,
                }
        return list(rows.values())
