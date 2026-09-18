// Mirrors backend/dispatch/schemas.py

export interface ParetoPoint {
  id: string;
  cost: number;
  co2_kg: number;
  lead_time_days: number;
  reliability: number;
  n_suppliers_active: number;
  co2_cap: number | null;
  lead_time_cap: number | null;
}

export interface ParetoResponse {
  scenario_id: string;
  front: ParetoPoint[];
  cheapest_id: string;
  greenest_id: string;
  most_resilient_id: string;
  computed_at: string;
}

export interface ObjectiveWeights {
  cost: number;
  co2: number;
  lead_time: number;
  reliability: number;
  stockout: number;
}

export interface OptimizeRequest {
  weights?: ObjectiveWeights;
  co2_cap?: number;
  lead_time_cap?: number;
  scenario_id?: string;
}

export interface OptimizeResponse {
  scenario_id: string;
  front: ParetoPoint[];
  selected: ParetoPoint | null;
}

// --- Twin ---

export interface InventoryState {
  node_id: string;
  product_id: string;
  level: number;
  safety_stock: number;
  avg_daily_consumption: number;
  days_of_cover: number;
  horizon_days: number;
}

export interface InTransitShipment {
  shipment_id: string;
  route_id: string;
  product_id: string;
  quantity: number;
  origin: string;
  destination: string;
  departure_tick: number;
  planned_arrival_tick: number;
  sampled_arrival_tick: number;
  status: string;
}

export interface SupplierState {
  supplier_id: string;
  status: "ONLINE" | "DEGRADED" | "OFFLINE";
  remaining_capacity_frac: number;
  posterior_alpha: number;
  posterior_beta: number;
}

export interface LaneState {
  route_id: string;
  status: "AVAILABLE" | "CONGESTED" | "CLOSED";
  planned_transit_days: number;
  realized_transit_days: number | null;
  consecutive_late_ticks: number;
}

export interface DemandState {
  customer_id: string;
  product_id: string;
  observed: number;
  forecast: number;
}

export interface VehicleState {
  vehicle_id: string;
  node_id: string;
  load_factor: number;
  hours_driven_today: number;
  next_stop: string | null;
}

export interface TwinState {
  tick: number;
  timestamp: string;
  inventory: InventoryState[];
  in_transit: InTransitShipment[];
  supplier: SupplierState[];
  lane: LaneState[];
  demand: DemandState[];
  vehicle: VehicleState[];
  reoptimization_triggered: boolean;
  trigger_reasons: string[];
  active_scenarios: string[];
}

export interface ReoptimizationOut {
  tick: number;
  trigger_reasons: string[];
  plan: ParetoPoint;
  delta_cost: number;
  delta_co2: number;
}

export interface TwinEventResponse {
  states: TwinState[];
  reoptimizations: ReoptimizationOut[];
}

export interface VarianceRow {
  route_id: string;
  planned_transit_days: number;
  realized_transit_days: number;
  drift_days: number;
  consecutive_late_ticks: number;
  breached: boolean;
}

export interface VarianceResponse {
  rows: VarianceRow[];
}

// --- Routing / fleet ---

export interface RouteLegOut {
  from_node: string;
  to_node: string;
  distance_km: number;
  duration_min: number;
  co2_kg: number;
  load_kg_after: number;
}

export interface VehicleRouteOut {
  vehicle_id: string;
  stop_sequence: string[];
  arrival_time_min: number[];
  cumulative_load_kg: number[];
  legs: RouteLegOut[];
  utilisation_pct: number;
}

export interface VRPResponse {
  depot: string;
  routes: VehicleRouteOut[];
  total_distance_km: number;
  total_co2_kg: number;
  unassigned_stops: string[];
}

export interface FleetUtilisationRow {
  vehicle_id: string;
  vehicle_class: string;
  home_depot: string;
  utilisation_pct: number | null;
}

// --- Shipment quote ---

export interface QuoteItem {
  product_id: string;
  quantity: number;
}

export interface QuoteRequest {
  items: QuoteItem[];
  origin: string;
  destination: string;
  deadline: string;
  departure?: string;
}

export interface PhysicalsOut {
  gross_kg: number;
  volume_m3: number;
  chargeable_kg_air: number;
  chargeable_kg_road: number;
  contains_hazmat: boolean;
}

export interface AirFlightOut {
  flight_id: string;
  carrier_name: string;
  departure_utc: string;
  arrival_utc: string;
  cutoff_utc: string;
  remaining_uld_kg: number;
  cost_per_kg: number;
}

export interface ModeOptionOut {
  mode: "Truck_Diesel" | "Truck_Electric" | "Rail" | "Air";
  feasible: boolean;
  infeasible_reason: string | null;
  distance_km: number;
  cost: number;
  co2_kg: number;
  transit_days: number;
  eta_p10_days: number;
  eta_p50_days: number;
  eta_p90_days: number;
  on_time_probability: number;
  reliability: number;
  flights: AirFlightOut[];
  road_stop_sequence: string[] | null;
  road_arrival_min: number[] | null;
  road_cumulative_load_kg: number[] | null;
  road_vehicle_id: string | null;
  road_vehicle_utilisation_pct: number | null;
}

export interface QuoteResponse {
  physicals: PhysicalsOut;
  options: ModeOptionOut[];
  cheapest: ModeOptionOut | null;
  greenest: ModeOptionOut | null;
  fastest: ModeOptionOut | null;
  air_vs_rail_co2_multiple: number;
}

// --- Network / scenarios ---

export interface NodeCoordinate {
  node_id: string;
  node_type: "Supplier" | "Warehouse" | "Factory" | "Customer";
  city: string;
  latitude: number;
  longitude: number;
  airport_iata: string | null;
  rail_hub: boolean;
}

export interface RouteEdge {
  route_id: string;
  origin: string;
  destination: string;
  mode: string;
  route_status: "AVAILABLE" | "CONGESTED" | "CLOSED";
  distance_km: number;
}

export interface ScenarioRow {
  scenario_id: string;
  scenario_type: string;
  affected_node: string;
  start_date: string;
  duration_days: number;
  severity: number;
  capacity_reduction: number;
  lead_time_increase: number;
  demand_change: number;
  route_status: string;
}
