import warnings

from fastapi.testclient import TestClient

from dispatch.api import app

warnings.filterwarnings("ignore")


def test_shipment_quote_endpoint():
    with TestClient(app) as client:
        r = client.post(
            "/shipment/quote",
            json={
                "items": [{"product_id": "P011", "quantity": 20}],
                "origin": "F01",
                "destination": "C003",
                "deadline": "2026-09-30T00:00:00",
                "departure": "2026-09-18T00:00:00",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["physicals"]["gross_kg"] > 0
        assert body["cheapest"] is not None
        assert body["air_vs_rail_co2_multiple"] > 1
        assert any(o["mode"] == "Truck_Diesel" for o in body["options"])


def test_shipment_quote_unknown_node_404():
    with TestClient(app) as client:
        r = client.post(
            "/shipment/quote",
            json={
                "items": [{"product_id": "P011", "quantity": 20}],
                "origin": "NOPE",
                "destination": "C003",
                "deadline": "2026-09-30T00:00:00",
            },
        )
        assert r.status_code == 404


def test_shipment_quote_invalid_datetime_422():
    with TestClient(app) as client:
        r = client.post(
            "/shipment/quote",
            json={
                "items": [{"product_id": "P011", "quantity": 20}],
                "origin": "F01",
                "destination": "C003",
                "deadline": "not-a-date",
            },
        )
        assert r.status_code == 422
