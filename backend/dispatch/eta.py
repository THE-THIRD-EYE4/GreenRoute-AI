"""Predicts a full delay-day probability distribution, never a point ETA.

Shipment delays are discrete {0, 1, 2} days (D2's repair): 62% on time
overall. dispatch/eta.py trains a GradientBoostingClassifier over that
3-class target and exposes predict_proba directly -- the UI renders "Tue,
71% on time, P90 Thu" from this distribution, not a single date.

Historical shipments (shipments_augmented.csv) carry pre-augmentation route
ids (R001-R045, the 45 original lanes) and a single generic "Truck" mode, so
distance/fleet-tier features are joined back in by (Origin, Destination),
preferring the Truck_Diesel row on that lane (falling back to any mode, then
to haversine) since that is the closest analogue to the historical "Truck"
shipments.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from haversine import haversine
from sklearn.calibration import calibration_curve
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from dispatch.config import ROAD_DETOUR_FACTOR, SEED
from dispatch.data_loader import Dataset, load_dataset

DELAY_CLASSES = (0, 1, 2)
DISTANCE_BINS = [0, 200, 500, 900, 1500, np.inf]
DISTANCE_LABELS = ["<200km", "200-500km", "500-900km", "900-1500km", ">1500km"]

FEATURE_COLUMNS = [
    "distance_bucket",
    "Supplier_ID",
    "Transport_Mode",
    "departure_weekday",
    "Quantity",
    "corridor",
    "Fleet_Tier",
]
CATEGORICAL = ["distance_bucket", "Supplier_ID", "Transport_Mode", "corridor", "Fleet_Tier"]
NUMERIC = ["departure_weekday", "Quantity"]


def _node_distance_fallback(ds: Dataset, origin: str, dest: str) -> float:
    coords = ds.node_coordinates.set_index("Node_ID")
    if origin not in coords.index or dest not in coords.index:
        return np.nan
    a = (coords.loc[origin, "Latitude"], coords.loc[origin, "Longitude"])
    b = (coords.loc[dest, "Latitude"], coords.loc[dest, "Longitude"])
    return haversine(a, b) * ROAD_DETOUR_FACTOR


def _route_lookup(ds: Dataset) -> pd.DataFrame:
    routes = ds.routes
    truck_diesel = routes[routes.Transport_Mode == "Truck_Diesel"][
        ["Origin", "Destination", "Distance_km", "Fleet_Tier"]
    ]
    any_mode = (
        routes.groupby(["Origin", "Destination"])
        .agg(Distance_km=("Distance_km", "mean"), Fleet_Tier=("Fleet_Tier", "first"))
        .reset_index()
    )
    combined = pd.concat([truck_diesel, any_mode]).drop_duplicates(subset=["Origin", "Destination"], keep="first")
    return combined


def build_feature_frame(ds: Dataset) -> pd.DataFrame:
    sh = ds.shipments.copy()
    route_lookup = _route_lookup(ds)

    corridor_lookup = ds.corridor_speeds[["Origin", "Destination", "Corridor_ID"]]

    df = sh.merge(route_lookup, on=["Origin", "Destination"], how="left")
    df = df.merge(corridor_lookup, on=["Origin", "Destination"], how="left")

    missing = df["Distance_km"].isna()
    if missing.any():
        df.loc[missing, "Distance_km"] = df.loc[missing].apply(
            lambda r: _node_distance_fallback(ds, r["Origin"], r["Destination"]), axis=1
        )
    df["Fleet_Tier"] = df["Fleet_Tier"].fillna("Standard")
    df["corridor"] = df["Corridor_ID"].fillna(df["Origin"] + "-" + df["Destination"])

    df["distance_bucket"] = pd.cut(df["Distance_km"], bins=DISTANCE_BINS, labels=DISTANCE_LABELS)
    df["departure_weekday"] = pd.to_datetime(df["Departure_Date"]).dt.weekday

    return df


@dataclass
class ETAModel:
    pipeline: Pipeline
    classes_: list[int]

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.pipeline.predict_proba(X[FEATURE_COLUMNS])


def _build_pipeline(seed: int) -> Pipeline:
    preprocess = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
        ],
        remainder="passthrough",
    )
    clf = GradientBoostingClassifier(random_state=seed, n_estimators=150, max_depth=3, learning_rate=0.08)
    return Pipeline([("prep", preprocess), ("clf", clf)])


def train_eta_model(ds: Dataset | None = None, seed: int = SEED) -> ETAModel:
    ds = ds or load_dataset()
    df = build_feature_frame(ds)
    X = df[FEATURE_COLUMNS]
    y = df["Delay_days"]
    pipeline = _build_pipeline(seed)
    pipeline.fit(X, y)
    return ETAModel(pipeline=pipeline, classes_=list(pipeline.classes_))


@dataclass(frozen=True)
class HoldoutReport:
    n_test: int
    brier_score: float
    calibration_bins: list[float]
    calibration_observed: list[float]
    calibration_predicted: list[float]


def holdout_evaluate(ds: Dataset | None = None, test_days: int = 20, seed: int = SEED) -> HoldoutReport:
    """Refit on everything before the last `test_days` calendar days of
    departures, score on that holdout: Brier score (multiclass, one-hot vs
    predicted-probability MSE) and a reliability/calibration curve for the
    "on time" (delay=0) probability.
    """
    ds = ds or load_dataset()
    df = build_feature_frame(ds)
    df["_day"] = pd.to_datetime(df["Departure_Date"]).dt.date

    cutoff_days = sorted(df["_day"].unique())[-test_days:]
    test_mask = df["_day"].isin(cutoff_days)
    train_df, test_df = df[~test_mask], df[test_mask]

    pipeline = _build_pipeline(seed)
    pipeline.fit(train_df[FEATURE_COLUMNS], train_df["Delay_days"])

    proba = pipeline.predict_proba(test_df[FEATURE_COLUMNS])
    classes = list(pipeline.classes_)

    y_true = test_df["Delay_days"].to_numpy()
    one_hot = np.zeros_like(proba)
    for i, cls in enumerate(classes):
        one_hot[:, i] = (y_true == cls).astype(float)
    brier = float(np.mean(np.sum((proba - one_hot) ** 2, axis=1)))

    on_time_idx = classes.index(0)
    on_time_true = (y_true == 0).astype(int)
    on_time_pred = proba[:, on_time_idx]
    frac_pos, mean_pred = calibration_curve(on_time_true, on_time_pred, n_bins=5, strategy="quantile")

    return HoldoutReport(
        n_test=len(test_df),
        brier_score=brier,
        calibration_bins=list(range(len(frac_pos))),
        calibration_observed=frac_pos.tolist(),
        calibration_predicted=mean_pred.tolist(),
    )


def predict_delay_distribution(model: ETAModel, ds: Dataset, **feature_overrides) -> dict[int, float]:
    """Build a single-row feature frame from overrides (Origin, Destination,
    Supplier_ID, Transport_Mode, Quantity, Departure_Date) and return
    {delay_days: probability}.
    """
    row = {
        "Origin": feature_overrides["origin"],
        "Destination": feature_overrides["destination"],
        "Supplier_ID": feature_overrides.get("supplier_id", "UNKNOWN"),
        "Transport_Mode": feature_overrides.get("transport_mode", "Truck"),
        "Quantity": feature_overrides.get("quantity", 1),
        "Departure_Date": feature_overrides.get("departure_date"),
        "Route_ID": None,
        "Shipment_ID": None,
        "Product_ID": None,
        "Expected_Arrival_Date": None,
        "Actual_Arrival_Date": None,
        "Status": None,
        "Delay_days": None,
        "On_Time": None,
    }
    single = pd.DataFrame([row])
    route_lookup = _route_lookup(ds)
    corridor_lookup = ds.corridor_speeds[["Origin", "Destination", "Corridor_ID"]]
    single = single.merge(route_lookup, on=["Origin", "Destination"], how="left")
    single = single.merge(corridor_lookup, on=["Origin", "Destination"], how="left")
    if single["Distance_km"].isna().any():
        single.loc[:, "Distance_km"] = _node_distance_fallback(ds, row["Origin"], row["Destination"])
    single["Fleet_Tier"] = single["Fleet_Tier"].fillna("Standard")
    single["corridor"] = single["Corridor_ID"].fillna(single["Origin"] + "-" + single["Destination"])
    single["distance_bucket"] = pd.cut(single["Distance_km"], bins=DISTANCE_BINS, labels=DISTANCE_LABELS)
    single["departure_weekday"] = pd.to_datetime(single["Departure_Date"]).dt.weekday

    proba = model.predict_proba(single)[0]
    return {cls: float(p) for cls, p in zip(model.classes_, proba)}
