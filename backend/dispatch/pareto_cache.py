"""Precomputed Pareto fronts, keyed by scenario id.

`BASELINE_SCENARIO` (no active disruption) plus all 54 rows of
scenarios_augmented.csv are solved and cached once at FastAPI startup
(`warm_cache`), each against its own live-perturbed ModelData
(dispatch.twin.model_data_for_scenario) rather than the static baseline --
so /pareto and /twin/event never block on a live solve. That is the whole
point: the live demo must never show a spinner.

The baseline gets the full epsilon-constraint sweep grid (9 CO2 points x 4
lead-time points). The 54 scenario fronts use a smaller grid so the full
warm-up finishes in roughly a minute rather than several -- still an exact,
deterministic front for each one, just fewer swept points.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import networkx as nx

from dispatch.data_loader import Dataset, load_dataset
from dispatch.network import build_network
from dispatch.optimizer import ModelData, SolveResult, build_model_data, pareto_front
from dispatch.schemas import ParetoPoint, ParetoResponse
from dispatch.twin import build_route_lane_map, model_data_for_scenario

BASELINE_SCENARIO = "baseline"
_SCENARIO_GRID = dict(n_co2_points=6, n_leadtime_points=3)
_BASELINE_GRID = dict(n_co2_points=9, n_leadtime_points=4)

_lock = threading.Lock()
_cache: dict[str, list[SolveResult]] = {}
_computed_at: dict[str, str] = {}
_model_data: ModelData | None = None
_dataset: Dataset | None = None
_graph: nx.MultiDiGraph | None = None
_route_lane_map: dict[str, list[str]] | None = None


def get_model_data() -> ModelData:
    global _model_data
    if _model_data is None:
        _model_data = build_model_data(get_dataset(), get_graph())
    return _model_data


def get_dataset() -> Dataset:
    global _dataset
    if _dataset is None:
        _dataset = load_dataset()
    return _dataset


def get_graph() -> nx.MultiDiGraph:
    global _graph
    if _graph is None:
        _graph = build_network(get_dataset())
    return _graph


def _get_route_lane_map() -> dict[str, list[str]]:
    global _route_lane_map
    if _route_lane_map is None:
        _route_lane_map = build_route_lane_map(get_dataset())
    return _route_lane_map


def warm_cache() -> None:
    """Compute and cache the baseline front plus all 54 scenario fronts.
    Called once at FastAPI startup so no request ever pays the solve cost.
    """
    ds = get_dataset()
    g = get_graph()
    md = get_model_data()
    lane_map = _get_route_lane_map()

    with _lock:
        if BASELINE_SCENARIO not in _cache:
            _cache[BASELINE_SCENARIO] = pareto_front(md, **_BASELINE_GRID)
            _computed_at[BASELINE_SCENARIO] = datetime.now(timezone.utc).isoformat()

        for scenario_id in ds.scenarios.Scenario_ID:
            if scenario_id in _cache:
                continue
            scenario_md = model_data_for_scenario(ds, md, g, scenario_id, lane_map)
            _cache[scenario_id] = pareto_front(scenario_md, **_SCENARIO_GRID)
            _computed_at[scenario_id] = datetime.now(timezone.utc).isoformat()


def get_front(scenario_id: str = BASELINE_SCENARIO) -> list[SolveResult]:
    with _lock:
        if scenario_id in _cache:
            return _cache[scenario_id]

    # Not precomputed (e.g. an unrecognised scenario id) -- solve on demand
    # and cache it so the next call is instant too.
    ds = get_dataset()
    if scenario_id != BASELINE_SCENARIO and scenario_id in set(ds.scenarios.Scenario_ID):
        scenario_md = model_data_for_scenario(ds, get_model_data(), get_graph(), scenario_id, _get_route_lane_map())
        results = pareto_front(scenario_md, **_SCENARIO_GRID)
    else:
        results = pareto_front(get_model_data())

    with _lock:
        _cache[scenario_id] = results
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
