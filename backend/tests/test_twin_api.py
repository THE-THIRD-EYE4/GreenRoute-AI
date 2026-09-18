import warnings

from fastapi.testclient import TestClient

from dispatch.api import app

warnings.filterwarnings("ignore")


def test_twin_state_endpoint():
    with TestClient(app) as client:
        r = client.get("/twin/state")
        assert r.status_code == 200
        body = r.json()
        assert body["tick"] == 0
        assert len(body["supplier"]) == 15
        assert len(body["lane"]) == 389


def test_twin_event_fires_scenario_and_ticks():
    with TestClient(app) as client:
        r = client.post("/twin/event", json={"scenario_id": "SC004", "n_ticks": 2})
        assert r.status_code == 200
        body = r.json()
        assert len(body["states"]) == 2
        assert body["states"][0]["active_scenarios"] == ["SC004"]


def test_twin_variance_endpoint():
    with TestClient(app) as client:
        client.post("/twin/event", json={"scenario_id": "SC001", "n_ticks": 6})
        r = client.get("/twin/variance")
        assert r.status_code == 200
        assert "rows" in r.json()


def test_twin_reset_endpoint():
    with TestClient(app) as client:
        client.post("/twin/event", json={"scenario_id": "SC001", "n_ticks": 3})
        r = client.post("/twin/reset")
        assert r.status_code == 200
        assert r.json()["tick"] == 0
        state = client.get("/twin/state").json()
        assert state["tick"] == 0
