from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from dispatch import pareto_cache
from dispatch.optimizer import solve_min_cost
from dispatch.schemas import OptimizeRequest, OptimizeResponse, ParetoPoint, ParetoResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    pareto_cache.warm_cache()
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
