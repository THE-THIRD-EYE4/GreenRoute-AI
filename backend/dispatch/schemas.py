"""Pydantic response/request models for the FastAPI surface."""
from __future__ import annotations

from pydantic import BaseModel, Field

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
