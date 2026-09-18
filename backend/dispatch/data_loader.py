"""Loads every CSV in ./data into typed, cached pandas DataFrames.

This is the single point of contact with the filesystem. Nothing else in the
package should call pandas.read_csv directly -- go through Dataset instead so
there is exactly one place that knows the on-disk schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import pandas as pd

from dispatch.config import DATA_DIR


def _read(name: str, **kwargs) -> pd.DataFrame:
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Expected dataset file missing: {path}. "
            f"Is DATA_DIR ({DATA_DIR}) pointed at the SU-02 augmented dataset?"
        )
    return pd.read_csv(path, **kwargs)


@dataclass
class Dataset:
    """All SU-02 tables, loaded once and shared read-only."""

    suppliers: pd.DataFrame
    warehouses: pd.DataFrame
    factories: pd.DataFrame
    customers: pd.DataFrame
    products: pd.DataFrame
    bill_of_materials: pd.DataFrame
    demand: pd.DataFrame
    routes: pd.DataFrame
    shipments: pd.DataFrame
    inventory: pd.DataFrame
    twin_state: pd.DataFrame
    scenarios: pd.DataFrame
    node_coordinates: pd.DataFrame
    vehicles: pd.DataFrame
    product_dimensions: pd.DataFrame
    air_cargo_schedule: pd.DataFrame
    rail_services: pd.DataFrame
    customer_windows: pd.DataFrame
    corridor_speeds: pd.DataFrame
    parcels: pd.DataFrame

    def node_type(self, node_id: str) -> str:
        row = self.node_coordinates.loc[self.node_coordinates.Node_ID == node_id]
        if row.empty:
            raise KeyError(f"Unknown node id: {node_id}")
        return row.iloc[0]["Node_Type"]

    def product_type(self, product_id: str) -> str:
        row = self.products.loc[self.products.Product_ID == product_id]
        if row.empty:
            raise KeyError(f"Unknown product id: {product_id}")
        return row.iloc[0]["Product_Type"]


@lru_cache(maxsize=1)
def load_dataset() -> Dataset:
    return Dataset(
        suppliers=_read("suppliers.csv"),
        warehouses=_read("warehouses.csv"),
        factories=_read("factories.csv"),
        customers=_read("customers.csv"),
        products=_read("products.csv"),
        bill_of_materials=_read("bill_of_materials.csv"),
        demand=_read("demand.csv", parse_dates=["Date"]),
        routes=_read("transportation_routes_augmented.csv"),
        shipments=_read(
            "shipments_augmented.csv",
            parse_dates=["Departure_Date", "Expected_Arrival_Date", "Actual_Arrival_Date"],
        ),
        inventory=_read("inventory_augmented.csv", parse_dates=["Date"]),
        twin_state=_read("digital_twin_state_augmented.csv", parse_dates=["Twin_Timestamp"]),
        scenarios=_read("scenarios_augmented.csv", parse_dates=["Start_Date"]),
        node_coordinates=_read("node_coordinates.csv"),
        vehicles=_read("vehicles.csv"),
        product_dimensions=_read("product_dimensions.csv"),
        air_cargo_schedule=_read(
            "air_cargo_schedule.csv",
            parse_dates=["Departure_UTC", "Arrival_UTC", "Cutoff_UTC"],
        ),
        rail_services=_read("rail_services.csv"),
        customer_windows=_read("customer_windows.csv"),
        corridor_speeds=_read("corridor_speeds.csv"),
        parcels=_read("parcels.csv", parse_dates=["Ready_At", "Deadline_At"]),
    )
