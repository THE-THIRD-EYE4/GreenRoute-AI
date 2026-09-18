import warnings

import pytest

from dispatch.risk import run_monte_carlo

warnings.filterwarnings("ignore")

REPORT = run_monte_carlo(n_samples=300, horizon_days=14, seed=42)


def test_monte_carlo_simulates_warehouse_factory_and_customer_nodes():
    node_types = {r.node_type for r in REPORT.node_reports}
    assert node_types == {"Warehouse", "Factory", "Customer"}
    assert len(REPORT.node_reports) > 100


def test_probabilities_are_valid():
    for r in REPORT.node_reports:
        assert 0.0 <= r.p_stockout <= 1.0
        assert r.expected_stockout_units >= 0.0


def test_cvar_90_is_at_least_mean_cost():
    # CVaR-90 averages the worst 10% of trajectories, so it can never be
    # below the plain mean unless every trajectory has identical cost.
    assert REPORT.cvar_90_cost >= REPORT.mean_total_cost - 1e-6


def test_upstream_capacity_is_lower_risk_than_customer_edge():
    # Matches the brief's own framing: supplier/factory capacity is not
    # binding, so risk should concentrate at the zero-buffer customer edge,
    # not upstream.
    import numpy as np

    factory_p = [r.p_stockout for r in REPORT.node_reports if r.node_type == "Factory"]
    customer_p = [r.p_stockout for r in REPORT.node_reports if r.node_type == "Customer"]
    assert np.median(factory_p) < np.median(customer_p)


def test_reproducible_with_same_seed():
    a = run_monte_carlo(n_samples=200, horizon_days=10, seed=7)
    b = run_monte_carlo(n_samples=200, horizon_days=10, seed=7)
    a_map = {(r.node_id, r.product_id): r.p_stockout for r in a.node_reports}
    b_map = {(r.node_id, r.product_id): r.p_stockout for r in b.node_reports}
    assert a_map == b_map
