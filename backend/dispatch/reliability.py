"""Beta-Binomial posterior reliability per supplier.

A supplier's declared Reliability figure becomes the prior mean; observed
On_Time shipments update it into a posterior. With RELIABILITY_PRIOR_STRENGTH
~= 15 pseudo-observations, a supplier with few shipments (S001, n=26) shrinks
meaningfully toward the declared prior, while one with many (S003, n=78)
barely moves -- exactly the point of a Bayesian shrinkage estimator over a
raw point estimate on small samples.

Never expose a point estimate: everything downstream (risk.py's Monte Carlo,
the network's edge reliability) draws from posterior_samples().
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dispatch.config import RELIABILITY_PRIOR_STRENGTH, SEED
from dispatch.data_loader import Dataset, load_dataset


@dataclass(frozen=True)
class SupplierPosterior:
    supplier_id: str
    prior_mean: float
    n_obs: int
    n_on_time: int
    alpha: float
    beta: float

    @property
    def posterior_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def raw_rate(self) -> float:
        return self.n_on_time / self.n_obs if self.n_obs else float("nan")

    @property
    def shrinkage(self) -> float:
        """Fraction of the raw-to-prior gap that the posterior mean moved:
        0 = no shrinkage (posterior == raw rate, e.g. huge n), 1 = fully
        anchored to the prior (e.g. n=0). Small samples (S001, n=26) should
        show high shrinkage; large ones (S003, n=78) should show low.
        """
        if self.prior_mean == self.raw_rate:
            return 0.0
        return abs(self.posterior_mean - self.raw_rate) / abs(self.prior_mean - self.raw_rate)

    def posterior_samples(self, n: int, seed: int = SEED) -> np.ndarray:
        """Draws from the posterior -- never a point estimate."""
        rng = np.random.default_rng(seed)
        return rng.beta(self.alpha, self.beta, size=n)


def _declared_reliability_by_supplier(ds: Dataset) -> pd.Series:
    return ds.suppliers.groupby("Supplier_ID")["Reliability"].mean()


def fit_supplier_posteriors(
    ds: Dataset | None = None, prior_strength: float = RELIABILITY_PRIOR_STRENGTH
) -> dict[str, SupplierPosterior]:
    ds = ds or load_dataset()
    prior_by_supplier = _declared_reliability_by_supplier(ds)

    obs = ds.shipments.groupby("Supplier_ID")["On_Time"].agg(n_obs="count", n_on_time="sum")

    posteriors: dict[str, SupplierPosterior] = {}
    for supplier_id, prior_mean in prior_by_supplier.items():
        prior_mean = float(prior_mean)
        alpha0 = prior_mean * prior_strength
        beta0 = (1 - prior_mean) * prior_strength

        if supplier_id in obs.index:
            n_obs = int(obs.loc[supplier_id, "n_obs"])
            n_on_time = int(obs.loc[supplier_id, "n_on_time"])
        else:
            n_obs, n_on_time = 0, 0

        posteriors[supplier_id] = SupplierPosterior(
            supplier_id=supplier_id,
            prior_mean=prior_mean,
            n_obs=n_obs,
            n_on_time=n_on_time,
            alpha=alpha0 + n_on_time,
            beta=beta0 + (n_obs - n_on_time),
        )
    return posteriors


def posterior_samples(supplier_id: str, n: int, seed: int = SEED, ds: Dataset | None = None) -> np.ndarray:
    posteriors = fit_supplier_posteriors(ds)
    if supplier_id not in posteriors:
        raise KeyError(f"Unknown supplier id: {supplier_id}")
    return posteriors[supplier_id].posterior_samples(n, seed=seed)
