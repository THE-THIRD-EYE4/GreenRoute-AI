from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dispatch import pareto_cache
from dispatch.data_loader import load_dataset
from dispatch.optimizer import solve_min_cost
from dispatch.routing import assign_customers_to_nearest_depot, solve_cvrptw
from dispatch.schemas import (
    FleetUtilisationRow,
    OptimizeRequest,
    OptimizeResponse,
    ParetoPoint,
    ParetoResponse,
    ReoptimizationOut,
    TwinEventRequest,
    TwinEventResponse,
    TwinStateOut,
    VarianceResponse,
    VarianceRow,
    VRPResponse,
)
from dispatch.twin import DigitalTwin

_twin: DigitalTwin | None = None


def get_twin() -> DigitalTwin:
    global _twin
    if _twin is None:
        _twin = DigitalTwin()
    return _twin


@asynccontextmanager
async def lifespan(app: FastAPI):
    pareto_cache.warm_cache()
    get_twin()
    yield


app = FastAPI(title="SU-02 Dispatch", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/pareto", response_model=ParetoResponse)
def get_pareto(scenario_id: str = pareto_cache.BASELINE_SCENARIO) -> ParetoResponse:
    return pareto_cache.to_response(scenario_id)


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest) -> OptimizeResponse:
    scenario_id = req.scenario_id or pareto_cache.BASELINE_SCENARIO

    if req.co2_cap is not None or req.lead_time_cap is not None:
        md = pareto_cache.get_model_data()
        result = solve_min_cost(md, co2_cap=req.co2_cap, lead_time_cap=req.lead_time_cap)
        if result.status != "Optimal":
            raise HTTPException(
                status_code=422,
                detail=f"No feasible plan under these caps (solver status: {result.status})",
            )
        selected = ParetoPoint.from_result(0, result)
        return OptimizeResponse(scenario_id=scenario_id, front=[selected], selected=selected)

    front_results = pareto_cache.get_front(scenario_id)
    points = [ParetoPoint.from_result(i, r) for i, r in enumerate(front_results)]

    weights = req.weights
    selected = None
    if weights is not None and points:
        def norm(values: list[float]) -> list[float]:
            lo, hi = min(values), max(values)
            span = hi - lo
            return [0.0 for _ in values] if span == 0 else [(v - lo) / span for v in values]

        cost_n = norm([p.cost for p in points])
        co2_n = norm([p.co2_kg for p in points])
        lead_n = norm([p.lead_time_days for p in points])
        unrel_n = norm([1 - p.reliability for p in points])

        scored = [
            (
                weights.cost * cost_n[i]
                + weights.co2 * co2_n[i]
                + weights.lead_time * lead_n[i]
                + weights.reliability * unrel_n[i],
                p,
            )
            for i, p in enumerate(points)
        ]
        selected = min(scored, key=lambda t: t[0])[1]

    return OptimizeResponse(scenario_id=scenario_id, front=points, selected=selected)


@app.get("/twin/state", response_model=TwinStateOut)
def twin_state() -> TwinStateOut:
    twin = get_twin()
    return TwinStateOut.model_validate(twin.log[-1])


@app.post("/twin/event", response_model=TwinEventResponse)
def twin_event(req: TwinEventRequest) -> TwinEventResponse:
    twin = get_twin()
    before = len(twin.reoptimizations)
    twin.apply_scenario(req.scenario_id)
    states = twin.run_ticks(req.n_ticks)
    new_reopts = twin.reoptimizations[before:]
    return TwinEventResponse(
        states=[TwinStateOut.model_validate(s) for s in states],
        reoptimizations=[
            ReoptimizationOut(
                tick=r.tick,
                trigger_reasons=list(r.trigger_reasons),
                plan=ParetoPoint.from_result(0, r.plan),
                delta_cost=round(r.delta_cost, 2),
                delta_co2=round(r.delta_co2, 4),
            )
            for r in new_reopts
        ],
    )


@app.get("/twin/variance", response_model=VarianceResponse)
def twin_variance() -> VarianceResponse:
    twin = get_twin()
    return VarianceResponse(rows=[VarianceRow(**row) for row in twin.variance_table()])


@app.post("/twin/reset")
def twin_reset() -> dict:
    twin = get_twin()
    twin.reset_to_baseline()
    return {"status": "ok", "tick": twin.log[-1].tick}


@app.get("/routes/vrp", response_model=VRPResponse)
def routes_vrp(depot: str | None = None, time_limit_s: int = 5) -> VRPResponse:
    dataset = load_dataset()
    if depot is None:
        groups = assign_customers_to_nearest_depot(dataset)
        depot = max(groups, key=lambda d: len(groups[d]))
        customer_ids = groups[depot]
    else:
        groups = assign_customers_to_nearest_depot(dataset)
        customer_ids = groups.get(depot, [])
        if not customer_ids:
            raise HTTPException(status_code=404, detail=f"No customers assigned to depot {depot}")

    result = solve_cvrptw(dataset, depot=depot, customer_ids=customer_ids, time_limit_s=time_limit_s)
    return VRPResponse(
        depot=result.depot,
        routes=[
            {
                "vehicle_id": r.vehicle_id,
                "stop_sequence": r.stop_sequence,
                "arrival_time_min": r.arrival_time_min,
                "cumulative_load_kg": r.cumulative_load_kg,
                "legs": [leg.__dict__ for leg in r.legs],
                "utilisation_pct": r.utilisation_pct,
            }
            for r in result.routes
        ],
        total_distance_km=result.total_distance_km,
        total_co2_kg=result.total_co2_kg,
        unassigned_stops=result.unassigned_stops,
    )


@app.get("/fleet/utilisation", response_model=list[FleetUtilisationRow])
def fleet_utilisation() -> list[FleetUtilisationRow]:
    dataset = load_dataset()
    groups = assign_customers_to_nearest_depot(dataset)

    utilisation_by_vehicle: dict[str, float] = {}
    for depot, customer_ids in groups.items():
        if not customer_ids:
            continue
        result = solve_cvrptw(dataset, depot=depot, customer_ids=customer_ids, time_limit_s=3)
        for r in result.routes:
            utilisation_by_vehicle[r.vehicle_id] = r.utilisation_pct

    rows = []
    for row in dataset.vehicles.itertuples():
        rows.append(
            FleetUtilisationRow(
                vehicle_id=row.Vehicle_ID,
                vehicle_class=row.Vehicle_Class,
                home_depot=row.Home_Depot,
                utilisation_pct=utilisation_by_vehicle.get(row.Vehicle_ID),
            )
        )
    return rows
