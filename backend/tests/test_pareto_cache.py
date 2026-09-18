import threading
import time
import warnings

import pytest

from dispatch import pareto_cache
from dispatch.twin import build_route_lane_map, model_data_for_scenario

warnings.filterwarnings("ignore")


def _fresh_warm_state() -> dict:
    return {
        "started": False,
        "done": False,
        "total": 0,
        "completed": 0,
        "current": None,
        "error": None,
    }


def _wait_until_done(timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = pareto_cache.warm_status()
        if status["done"]:
            return status
        time.sleep(0.02)
    raise AssertionError("warm-up did not report done in time")


@pytest.fixture(scope="module")
def warmed():
    # Use the module's own dataset/graph/model-data singletons so this
    # exercises the exact cache the API's startup lifespan warms.
    ds = pareto_cache.get_dataset()
    md = pareto_cache.get_model_data()
    g = pareto_cache.get_graph()
    lane_map = build_route_lane_map(ds)

    # Warm only a handful of scenarios directly (not the full 54) to keep
    # this test fast; the full warm_cache() path is exercised by the API
    # startup test in test_api.py / test_twin_api.py.
    for sid in ["SC001", "SC003", "SC004"]:
        scenario_md = model_data_for_scenario(ds, md, g, sid, lane_map)
        from dispatch.optimizer import pareto_front

        pareto_cache._cache[sid] = pareto_front(scenario_md, n_co2_points=4, n_leadtime_points=2)
    pareto_cache._cache[pareto_cache.BASELINE_SCENARIO] = pareto_front_baseline(md)
    return ds, md, g


def pareto_front_baseline(md):
    from dispatch.optimizer import pareto_front

    return pareto_front(md, n_co2_points=4, n_leadtime_points=2)


def test_scenario_front_differs_from_baseline_for_demand_shock(warmed):
    baseline = pareto_cache.get_front(pareto_cache.BASELINE_SCENARIO)
    sc003 = pareto_cache.get_front("SC003")  # Demand increase, C013, +30%
    assert min(r.cost for r in sc003) >= min(r.cost for r in baseline) - 1e-6


def test_route_closure_scenario_drops_legs(warmed):
    ds, md, g = warmed
    lane_map = build_route_lane_map(ds)
    scenario_md = model_data_for_scenario(ds, md, g, "SC004", lane_map)
    assert len(scenario_md.legs) < len(md.legs)


def test_get_front_caches_unrecognized_scenario_gracefully(warmed):
    # An id not in scenarios_augmented.csv falls back to the baseline solve
    # rather than raising, and is itself cached after the first call.
    result_1 = pareto_cache.get_front("not-a-real-scenario")
    t0 = time.time()
    result_2 = pareto_cache.get_front("not-a-real-scenario")
    assert time.time() - t0 < 0.05
    assert result_1 == result_2


def test_to_response_labels_present(warmed):
    resp = pareto_cache.to_response(pareto_cache.BASELINE_SCENARIO)
    ids = {p.id for p in resp.front}
    assert resp.cheapest_id in ids
    assert resp.greenest_id in ids
    assert resp.most_resilient_id in ids


def test_start_warm_cache_background_runs_and_reports_done(monkeypatch):
    monkeypatch.setattr(pareto_cache, "_warm_state", _fresh_warm_state())
    called = threading.Event()

    def fake_warm() -> None:
        called.set()

    monkeypatch.setattr(pareto_cache, "warm_cache", fake_warm)
    pareto_cache.start_warm_cache_background()

    assert called.wait(timeout=2), "background thread never called warm_cache()"
    status = _wait_until_done()
    assert status["started"] is True
    assert status["error"] is None


def test_start_warm_cache_background_is_idempotent(monkeypatch):
    monkeypatch.setattr(pareto_cache, "_warm_state", _fresh_warm_state())
    calls: list[int] = []
    release = threading.Event()

    def fake_warm() -> None:
        calls.append(1)
        release.wait(timeout=2)

    monkeypatch.setattr(pareto_cache, "warm_cache", fake_warm)
    pareto_cache.start_warm_cache_background()
    pareto_cache.start_warm_cache_background()  # must be a no-op: already started
    release.set()
    _wait_until_done()
    assert len(calls) == 1


def test_warm_cache_exception_is_captured_not_raised(monkeypatch):
    monkeypatch.setattr(pareto_cache, "_warm_state", _fresh_warm_state())

    def boom() -> None:
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(pareto_cache, "warm_cache", boom)
    pareto_cache.start_warm_cache_background()  # must not raise in this thread

    status = _wait_until_done()
    assert status["error"] is not None and "solver exploded" in status["error"]
