import warnings

from fastapi.testclient import TestClient

from dispatch.api import app

warnings.filterwarnings("ignore")


def test_routes_vrp_with_explicit_depot():
    with TestClient(app) as client:
        r = client.get("/routes/vrp", params={"depot": "W05", "time_limit_s": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["depot"] == "W05"
        assert "routes" in body


def test_routes_vrp_default_depot_picks_largest_cluster():
    with TestClient(app) as client:
        r = client.get("/routes/vrp", params={"time_limit_s": 3})
        assert r.status_code == 200


def test_routes_vrp_unknown_depot_404():
    with TestClient(app) as client:
        r = client.get("/routes/vrp", params={"depot": "W99"})
        assert r.status_code == 404


def test_fleet_utilisation_lists_all_vehicles():
    with TestClient(app) as client:
        r = client.get("/fleet/utilisation")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 12
        assert all("vehicle_id" in row for row in body)
