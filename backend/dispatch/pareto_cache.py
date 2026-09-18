"""Precomputed Pareto fronts, keyed by scenario id.

`BASELINE_SCENARIO` (no active disruption) is always present. Populating one
entry per row of scenarios_augmented.csv is added in a later build step
(the twin needs to exist first to perturb the network per scenario); for
now this module gives /pareto and /optimize a cache that never blocks on a
live solve, which is the whole point -- the live demo must never show a
spinner.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from dispatch.optimizer import ModelData, SolveResult, build_model_data, pareto_front
from dispatch.schemas import ParetoPoint, ParetoResponse

BASELINE_SCENARIO = "baseline"

_lock = threading.Lock()
_cache: dict[str, list[SolveResult]] = {}
_computed_at: dict[str, str] = {}
_model_data: ModelData | None = None


def get_model_data() -> ModelData:
    global _model_data
    if _model_data is None:
        _model_data = build_model_data()
    return _model_data


def warm_cache() -> None:
    """Compute and cache the baseline Pareto front. Called once at FastAPI
    startup so the first request never pays the solve cost.
    """
    md = get_model_data()
    with _lock:
        if BASELINE_SCENARIO not in _cache:
            _cache[BASELINE_SCENARIO] = pareto_front(md)
            _computed_at[BASELINE_SCENARIO] = datetime.now(timezone.utc).isoformat()


def get_front(scenario_id: str = BASELINE_SCENARIO) -> list[SolveResult]:
    with _lock:
        if scenario_id not in _cache:
            # Not precomputed (e.g. an unrecognised scenario id) -- solve on
            # demand and cache it so the next call is instant too.
            md = get_model_data()
            _cache[scenario_id] = pareto_front(md)
            _computed_at[scenario_id] = datetime.now(timezone.utc).isoformat()
        return _cache[scenario_id]


def to_response(scenario_id: str = BASELINE_SCENARIO) -> ParetoResponse:
    results = get_front(scenario_id)
    points = [ParetoPoint.from_result(i, r) for i, r in enumerate(results)]
    cheapest = min(points, key=lambda p: p.cost)
    greenest = min(points, key=lambda p: p.co2_kg)
    most_resilient = max(points, key=lambda p: p.reliability)
    return ParetoResponse(
        scenario_id=scenario_id,
        front=points,
        cheapest_id=cheapest.id,
        greenest_id=greenest.id,
        most_resilient_id=most_resilient.id,
        computed_at=_computed_at.get(scenario_id, datetime.now(timezone.utc).isoformat()),
    )
