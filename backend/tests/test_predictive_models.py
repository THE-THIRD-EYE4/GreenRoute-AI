import warnings

import numpy as np
import pytest

from dispatch.data_loader import load_dataset
from dispatch.eta import holdout_evaluate, predict_delay_distribution, train_eta_model
from dispatch.forecast import backtest_customer_product, forecast_customer_product
from dispatch.reliability import fit_supplier_posteriors

warnings.filterwarnings("ignore")

DS = load_dataset()


# --- reliability.py ---------------------------------------------------


def test_small_sample_supplier_shrinks_more_than_large_sample():
    posteriors = fit_supplier_posteriors(DS)
    s001, s003 = posteriors["S001"], posteriors["S003"]
    assert s001.n_obs == 26
    assert s003.n_obs == 78
    assert s001.shrinkage > s003.shrinkage


def test_posterior_samples_are_draws_not_a_point_estimate():
    posteriors = fit_supplier_posteriors(DS)
    samples = posteriors["S001"].posterior_samples(200)
    assert samples.shape == (200,)
    assert samples.std() > 0
    assert (samples >= 0).all() and (samples <= 1).all()


# --- forecast.py --------------------------------------------------------


def test_forecast_quantiles_are_ordered():
    f = forecast_customer_product(DS, "C001", "P011", horizon_days=5)
    for lo, mid, hi in zip(f.p10, f.p50, f.p90):
        assert lo <= mid <= hi


def test_forecast_horizon_matches_request():
    f = forecast_customer_product(DS, "C002", "P012", horizon_days=10)
    assert len(f.dates) == len(f.p10) == len(f.p50) == len(f.p90) == 10


def test_backtest_reports_reasonable_coverage():
    bt = backtest_customer_product(DS, "C001", "P011", test_days=20, n_boot=200)
    assert bt.n_test_days == 20
    assert 0.3 <= bt.coverage_p10_p90 <= 1.0
    assert bt.pinball_loss_p50 >= 0


# --- eta.py ---------------------------------------------------------------


def test_eta_model_outputs_full_distribution():
    model = train_eta_model(DS)
    dist = predict_delay_distribution(
        model, DS, origin="S001", destination="W03", supplier_id="S001", quantity=100, departure_date="2026-09-20"
    )
    assert set(dist.keys()) == {0, 1, 2}
    assert pytest.approx(sum(dist.values()), abs=1e-6) == 1.0
    assert all(0 <= p <= 1 for p in dist.values())


def test_eta_holdout_report():
    report = holdout_evaluate(DS, test_days=20)
    assert report.n_test > 0
    assert 0 <= report.brier_score <= 2.0
    assert len(report.calibration_observed) == len(report.calibration_predicted)
