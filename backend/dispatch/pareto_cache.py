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

# Warm-up progress, so /health can report readiness instead of the server simply
# not answering. Warming 55 NSGA/epsilon fronts takes minutes; blocking the event
# loop on it means every frontend request gets connection-refused and the UI looks
# broken when it is merely starting.
_warm_state: dict[str, object] = {
    "started": False,
    "done": False,
    "total": 0,
    "completed": 0,
    "current": None,
    "error": None,
}
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


def warm_status() -> dict:
    """Snapshot of warm-up progress for /health."""
    with _lock:
        return dict(_warm_state)


def start_warm_cache_background() -> None:
    """Warm the cache on a daemon thread so the API serves immediately.

    A request that lands before its front is warm still gets a correct answer:
    get_front() falls back to an on-demand solve. It is slower, not broken.
    """
    with _lock:
        if _warm_state["started"]:
            return
        _warm_state["started"] = True

    def _run() -> None:
        try:
            warm_cache()
        except Exception as exc:  # never let a warm failure kill the server
            with _lock:
                _warm_state["error"] = str(exc)
        finally:
            with _lock:
                _warm_state["done"] = True
                _warm_state["current"] = None

    threading.Thread(target=_run, name="pareto-warm", daemon=True).start()


def warm_cache() -> None:
    """Compute and cache the baseline front plus all 54 scenario fronts.

    Safe to call directly (tests do), but production startup uses
    start_warm_cache_background() so uvicorn binds the port immediately.
    """
    ds = get_dataset()
    g = get_graph()
    md = get_model_data()
    lane_map = _get_route_lane_map()

    scenario_ids = list(ds.scenarios.Scenario_ID)
    with _lock:
        _warm_state["total"] = len(scenario_ids) + 1
        _warm_state["completed"] = 0

    # NOTE: the solve happens OUTSIDE the lock. Holding _lock across a multi-minute
    # NSGA/epsilon sweep would serialise every concurrent /pareto request behind it,
    # which is the same stall this refactor exists to remove.
    if BASELINE_SCENARIO not in _cache:
        with _lock:
            _warm_state["current"] = BASELINE_SCENARIO
        baseline = pareto_front(md, **_BASELINE_GRID)
        with _lock:
            _cache[BASELINE_SCENARIO] = baseline
            _computed_at[BASELINE_SCENARIO] = datetime.now(timezone.utc).isoformat()
    with _lock:
        _warm_state["completed"] = 1

    for i, scenario_id in enumerate(scenario_ids, start=2):
        with _lock:
            already = scenario_id in _cache
            _warm_state["current"] = scenario_id
        if not already:
            scenario_md = model_data_for_scenario(ds, md, g, scenario_id, lane_map)
            front = pareto_front(scenario_md, **_SCENARIO_GRID)
            with _lock:
                _cache[scenario_id] = front
                _computed_at[scenario_id] = datetime.now(timezone.utc).isoformat()
        with _lock:
            _warm_state["completed"] = i


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
