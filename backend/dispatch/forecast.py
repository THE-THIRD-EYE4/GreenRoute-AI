"""ETS demand forecasting with bootstrapped-residual uncertainty.

Demand CV is 0.18 with no trend (verified in network.verify_dataset_assumptions)
-- that is exactly the regime simple exponential smoothing (ETS, error-only,
no trend/seasonal component) is built for. An LSTM would be indefensible on
90 days x 24 series of near-stationary, low-variance counts; ETS is not a
compromise here, it is the correct model.

Every forecast is returned as a P10/P50/P90 band, built by bootstrapping the
in-sample residual pool -- never a bare point forecast.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.holtwinters import SimpleExpSmoothing

from dispatch.config import SEED
from dispatch.data_loader import Dataset, load_dataset

QUANTILES = (0.10, 0.50, 0.90)


@dataclass(frozen=True)
class ForecastResult:
    customer_id: str
    product_id: str
    horizon_days: int
    dates: list[str]
    p10: list[float]
    p50: list[float]
    p90: list[float]


def series_for_customer_product(ds: Dataset, customer_id: str, product_id: str) -> pd.Series:
    sub = ds.demand[(ds.demand.Customer_ID == customer_id) & (ds.demand.Product_ID == product_id)]
    sub = sub.sort_values("Date")
    return pd.Series(sub.Demand_Quantity.values, index=pd.DatetimeIndex(sub.Date.values))


def fit_ets(train: pd.Series) -> SimpleExpSmoothing:
    model = SimpleExpSmoothing(train.values, initialization_method="estimated")
    return model.fit(optimized=True)


def _bootstrap_quantiles(
    point_forecast: np.ndarray, residuals: np.ndarray, n_boot: int, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each horizon step, resample residuals n_boot times and add to the
    point forecast, then take empirical quantiles. Demand is non-negative,
    so paths are clipped at 0.
    """
    rng = np.random.default_rng(seed)
    horizon = len(point_forecast)
    draws = rng.choice(residuals, size=(n_boot, horizon), replace=True)
    paths = np.clip(point_forecast[None, :] + draws, 0, None)
    p10 = np.quantile(paths, 0.10, axis=0)
    p50 = np.quantile(paths, 0.50, axis=0)
    p90 = np.quantile(paths, 0.90, axis=0)
    return p10, p50, p90


def forecast_customer_product(
    ds: Dataset | None,
    customer_id: str,
    product_id: str,
    horizon_days: int = 7,
    n_boot: int = 1000,
    seed: int = SEED,
) -> ForecastResult:
    ds = ds or load_dataset()
    series = series_for_customer_product(ds, customer_id, product_id)
    if series.empty:
        raise KeyError(f"No demand history for {customer_id}/{product_id}")

    fitted = fit_ets(series)
    residuals = series.values - fitted.fittedvalues
    point_forecast = fitted.forecast(horizon_days)

    p10, p50, p90 = _bootstrap_quantiles(np.asarray(point_forecast), residuals, n_boot, seed)

    last_date = series.index[-1]
    dates = [str((last_date + pd.Timedelta(days=i + 1)).date()) for i in range(horizon_days)]

    return ForecastResult(
        customer_id=customer_id,
        product_id=product_id,
        horizon_days=horizon_days,
        dates=dates,
        p10=p10.tolist(),
        p50=p50.tolist(),
        p90=p90.tolist(),
    )


def _pinball_loss(actual: float, forecast: float, quantile: float) -> float:
    diff = actual - forecast
    return max(quantile * diff, (quantile - 1) * diff)


@dataclass(frozen=True)
class BacktestResult:
    customer_id: str
    product_id: str
    n_test_days: int
    pinball_loss_p10: float
    pinball_loss_p50: float
    pinball_loss_p90: float
    coverage_p10_p90: float  # fraction of actuals inside [P10, P90]


def backtest_customer_product(
    ds: Dataset | None,
    customer_id: str,
    product_id: str,
    test_days: int = 20,
    n_boot: int = 500,
    seed: int = SEED,
) -> BacktestResult:
    """Walk-forward 1-step backtest over the last `test_days` days: refit on
    everything up to t-1, forecast day t, score against the realised value.
    """
    ds = ds or load_dataset()
    series = series_for_customer_product(ds, customer_id, product_id)
    n = len(series)
    if n <= test_days + 5:
        raise ValueError(f"Not enough history to backtest {test_days} days for {customer_id}/{product_id}")

    losses = {0.10: [], 0.50: [], 0.90: []}
    inside_band = []

    for i in range(n - test_days, n):
        train = series.iloc[:i]
        actual = float(series.iloc[i])

        fitted = fit_ets(train)
        residuals = train.values - fitted.fittedvalues
        point_forecast = np.asarray(fitted.forecast(1))
        p10, p50, p90 = _bootstrap_quantiles(point_forecast, residuals, n_boot, seed + i)

        for q, val in zip(QUANTILES, (p10[0], p50[0], p90[0])):
            losses[q].append(_pinball_loss(actual, val, q))
        inside_band.append(p10[0] <= actual <= p90[0])

    return BacktestResult(
        customer_id=customer_id,
        product_id=product_id,
        n_test_days=test_days,
        pinball_loss_p10=float(np.mean(losses[0.10])),
        pinball_loss_p50=float(np.mean(losses[0.50])),
        pinball_loss_p90=float(np.mean(losses[0.90])),
        coverage_p10_p90=float(np.mean(inside_band)),
    )
