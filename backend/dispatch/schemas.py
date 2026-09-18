"""Pydantic response/request models for the FastAPI surface."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dispatch.optimizer import SolveResult


class ObjectiveWeights(BaseModel):
    cost: float = 0.34
    co2: float = 0.33
    lead_time: float = 0.11
    reliability: float = 0.11
    stockout: float = 0.11


class OptimizeRequest(BaseModel):
    weights: ObjectiveWeights | None = None
    co2_cap: float | None = Field(default=None, description="kg CO2 cap for the epsilon-constraint MILP")
    lead_time_cap: float | None = Field(default=None, description="days, volume-weighted")
    scenario_id: str | None = None


class ParetoPoint(BaseModel):
    id: str
    cost: float
    co2_kg: float
    lead_time_days: float
    reliability: float
    n_suppliers_active: int
    co2_cap: float | None = None
    lead_time_cap: float | None = None

    @classmethod
    def from_result(cls, idx: int, r: SolveResult) -> "ParetoPoint":
        return cls(
            id=f"p{idx}",
            cost=round(r.cost, 2),
            co2_kg=round(r.co2_kg, 4),
            lead_time_days=round(r.lead_time_days, 4),
            reliability=round(r.reliability, 5),
            n_suppliers_active=r.n_suppliers_active,
            co2_cap=r.co2_cap,
            lead_time_cap=r.lead_time_cap,
        )


class ParetoResponse(BaseModel):
    scenario_id: str
    front: list[ParetoPoint]
    cheapest_id: str
    greenest_id: str
    most_resilient_id: str
    computed_at: str


class OptimizeResponse(BaseModel):
    scenario_id: str
    front: list[ParetoPoint]
    selected: ParetoPoint | None = None


# --- Twin ------------------------------------------------------------------


class InventoryStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    node_id: str
    product_id: str
    level: float
    safety_stock: float
    avg_daily_consumption: float
    days_of_cover: float
    horizon_days: float


class InTransitShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    shipment_id: str
    route_id: str
    product_id: str
    quantity: float
    origin: str
    destination: str
    departure_tick: int
    planned_arrival_tick: int
    sampled_arrival_tick: int
    status: str


class SupplierStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    supplier_id: str
    status: str
    remaining_capacity_frac: float
    posterior_alpha: float
    posterior_beta: float


class LaneStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    route_id: str
    status: str
    planned_transit_days: float
    realized_transit_days: float | None
    consecutive_late_ticks: int


class DemandStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    customer_id: str
    product_id: str
    observed: float
    forecast: float


class VehicleStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    vehicle_id: str
    node_id: str
    load_factor: float
    hours_driven_today: float
    next_stop: str | None


class TwinStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tick: int
    timestamp: str
    inventory: list[InventoryStateOut]
    in_transit: list[InTransitShipmentOut]
    supplier: list[SupplierStateOut]
    lane: list[LaneStateOut]
    demand: list[DemandStateOut]
    vehicle: list[VehicleStateOut]
    reoptimization_triggered: bool
    trigger_reasons: list[str]
    active_scenarios: list[str]


class TwinEventRequest(BaseModel):
    scenario_id: str
    n_ticks: int = Field(default=1, ge=1, le=30)


class TwinAdvanceRequest(BaseModel):
    n_ticks: int = Field(default=1, ge=1, le=30)


class ReoptimizationOut(BaseModel):
    tick: int
    trigger_reasons: list[str]
    plan: ParetoPoint
    delta_cost: float
    delta_co2: float


class TwinEventResponse(BaseModel):
    states: list[TwinStateOut]
    reoptimizations: list[ReoptimizationOut]


class VarianceRow(BaseModel):
    route_id: str
    planned_transit_days: float
    realized_transit_days: float
    drift_days: float
    consecutive_late_ticks: int
    breached: bool


class VarianceResponse(BaseModel):
    rows: list[VarianceRow]


# --- Routing / fleet ---------------------------------------------------------


class RouteLegOut(BaseModel):
    from_node: str
    to_node: str
    distance_km: float
    duration_min: float
    co2_kg: float
    load_kg_after: float


class VehicleRouteOut(BaseModel):
    vehicle_id: str
    stop_sequence: list[str]
    arrival_time_min: list[float]
    cumulative_load_kg: list[float]
    legs: list[RouteLegOut]
    utilisation_pct: float


class VRPResponse(BaseModel):
    depot: str
    routes: list[VehicleRouteOut]
    total_distance_km: float
    total_co2_kg: float
    unassigned_stops: list[str]


class FleetUtilisationRow(BaseModel):
    vehicle_id: str
    vehicle_class: str
    home_depot: str
    utilisation_pct: float | None = None


# --- Shipment quote ----------------------------------------------------------


class QuoteItem(BaseModel):
    product_id: str
    quantity: float


class QuoteRequest(BaseModel):
    items: list[QuoteItem]
    origin: str
    destination: str
    deadline: str = Field(description="ISO datetime")
    departure: str | None = Field(default=None, description="ISO datetime; defaults to now")


class PhysicalsOut(BaseModel):
    gross_kg: float
    volume_m3: float
    chargeable_kg_air: float
    chargeable_kg_road: float
    contains_hazmat: bool


class AirFlightOut(BaseModel):
    flight_id: str
    carrier_name: str
    departure_utc: str
    arrival_utc: str
    cutoff_utc: str
    remaining_uld_kg: float
    cost_per_kg: float


class ModeOptionOut(BaseModel):
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
    flights: list[AirFlightOut] = []
    road_stop_sequence: list[str] | None = None
    road_arrival_min: list[float] | None = None
    road_cumulative_load_kg: list[float] | None = None
    road_vehicle_id: str | None = None
    road_vehicle_utilisation_pct: float | None = None


class NodeOut(BaseModel):
    node_id: str
    node_type: str
    city: str
    latitude: float
    longitude: float
    airport_iata: str | None = None
    rail_hub: bool


class ScenarioOut(BaseModel):
    scenario_id: str
    scenario_type: str
    affected_node: str
    start_date: str
    duration_days: int
    severity: float
    capacity_reduction: float
    lead_time_increase: int
    demand_change: float
    route_status: str


class QuoteResponse(BaseModel):
    physicals: PhysicalsOut
    options: list[ModeOptionOut]
    cheapest: ModeOptionOut | None
    greenest: ModeOptionOut | None
    fastest: ModeOptionOut | None
    air_vs_rail_co2_multiple: float
