import warnings

import numpy as np
import pytest

from dispatch.optimizer_nsga import run_nsga2, validate_against_milp

warnings.filterwarnings("ignore")


def test_nsga2_runs_and_returns_five_objectives():
    result, ctx = run_nsga2(pop_size=16, n_gen=8)
    assert result.F.shape[1] == 5
    assert result.F.shape[0] > 0
    assert np.all(np.isfinite(result.F))


def test_nsga2_objectives_are_non_negative():
    result, ctx = run_nsga2(pop_size=16, n_gen=8)
    # cost, co2, lead_time, (1 - reliability), stockout are all >= 0 by construction
    assert np.all(result.F >= -1e-6)


def test_green_bias_gene_trades_cost_for_co2_on_at_least_one_product():
    from dispatch.data_loader import load_dataset
    from dispatch.network import build_network
    from dispatch.optimizer import build_model_data
    from dispatch.optimizer_nsga import _build_context, decode

    ds = load_dataset()
    md = build_model_data(ds, build_network(ds))
    ctx = _build_context(ds, md)
    n = ctx.n_genes_a + ctx.n_genes_b + ctx.n_genes_c

    genes_cheap = np.full(n, 0.5)
    genes_cheap[-ctx.n_genes_c :] = 0.0
    genes_green = np.full(n, 0.5)
    genes_green[-ctx.n_genes_c :] = 1.0

    cheap = decode(genes_cheap, ctx)
    green = decode(genes_green, ctx)
    assert green.co2_kg < cheap.co2_kg


def test_supplier_diversification_cap_respected_in_decoded_plan():
    from dispatch.config import MAX_SUPPLIER_SHARE
    from dispatch.data_loader import load_dataset
    from dispatch.network import build_network
    from dispatch.optimizer import build_model_data
    from dispatch.optimizer_nsga import _build_context, _cap_and_normalize

    ds = load_dataset()
    md = build_model_data(ds, build_network(ds))
    ctx = _build_context(ds, md)

    rng = np.random.default_rng(0)
    for p in ctx.component_products:
        n_suppliers = len(ctx.supplier_by_product[p])
        raw = rng.random(n_suppliers)
        capped = _cap_and_normalize(raw, MAX_SUPPLIER_SHARE)
        assert capped.sum() == pytest.approx(1.0, abs=1e-6)
        assert (capped <= MAX_SUPPLIER_SHARE + 1e-6).all()


def test_validate_against_milp_produces_finite_report():
    report = validate_against_milp(pop_size=16, n_gen=8)
    assert report.milp_n_points > 1
    assert report.nsga_n_points > 0
    assert report.hypervolume_milp >= 0
    assert report.hypervolume_nsga >= 0
    assert report.generational_distance >= 0
    assert np.isfinite(report.generational_distance)


def test_nsga_front_is_reproducible_with_same_seed():
    r1, _ = run_nsga2(pop_size=16, n_gen=8, seed=123)
    r2, _ = run_nsga2(pop_size=16, n_gen=8, seed=123)
    assert np.allclose(np.sort(r1.F, axis=0), np.sort(r2.F, axis=0))
