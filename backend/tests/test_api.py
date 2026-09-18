from fastapi.testclient import TestClient

from dispatch.api import app


def test_health():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_pareto_endpoint_returns_labelled_picks():
    with TestClient(app) as client:
        r = client.get("/pareto")
        assert r.status_code == 200
        body = r.json()
        assert len(body["front"]) > 1
        ids = {p["id"] for p in body["front"]}
        assert body["cheapest_id"] in ids
        assert body["greenest_id"] in ids
        assert body["most_resilient_id"] in ids

        by_id = {p["id"]: p for p in body["front"]}
        cheapest = by_id[body["cheapest_id"]]
        greenest = by_id[body["greenest_id"]]
        assert all(cheapest["cost"] <= p["cost"] for p in body["front"])
        assert all(greenest["co2_kg"] <= p["co2_kg"] for p in body["front"])


def test_pareto_is_cached_and_instant():
    import time

    with TestClient(app) as client:
        t0 = time.time()
        client.get("/pareto")
        elapsed = time.time() - t0
        assert elapsed < 0.5, "pareto endpoint should serve from cache, not re-solve"


def test_optimize_with_explicit_caps():
    with TestClient(app) as client:
        r = client.post("/optimize", json={"co2_cap": 45.0, "lead_time_cap": 5.0})
        assert r.status_code == 200
        body = r.json()
        assert body["selected"]["co2_kg"] <= 45.0 + 1e-3


def test_optimize_with_weights_picks_from_front():
    with TestClient(app) as client:
        r = client.post("/optimize", json={"weights": {"cost": 0.1, "co2": 0.8, "lead_time": 0.05, "reliability": 0.05, "stockout": 0.0}})
        assert r.status_code == 200
        body = r.json()
        assert body["selected"] is not None
        ids = {p["id"] for p in body["front"]}
        assert body["selected"]["id"] in ids


def test_optimize_infeasible_caps_returns_422():
    with TestClient(app) as client:
        r = client.post("/optimize", json={"co2_cap": 0.001})
        assert r.status_code == 422
