import networkx as nx
import pytest

from dispatch.data_loader import load_dataset
from dispatch.network import build_network, verify_dataset_assumptions


def test_dataset_loads():
    ds = load_dataset()
    assert len(ds.suppliers) == 67
    assert len(ds.warehouses) == 5
    assert len(ds.factories) == 4
    assert len(ds.customers) == 24
    assert len(ds.products) == 15
    assert len(ds.routes) == 389
    assert len(ds.scenarios) == 54


def test_dataset_assumptions_hold():
    report = verify_dataset_assumptions()
    assert report.supplier_capacity_min_days_cover > 5
    assert report.factory_capacity_per_day > 2 * report.factory_demand_per_day
    assert report.demand_cv < 0.5
    assert abs(report.ground_mode_cost_co2_corr) < 0.80


def test_network_builds_and_is_fully_connected():
    g = build_network()
    assert isinstance(g, nx.MultiDiGraph)

    suppliers = [n for n, d in g.nodes(data=True) if d["node_type"] == "Supplier"]
    customers = [n for n, d in g.nodes(data=True) if d["node_type"] == "Customer"]
    assert len(suppliers) == 15
    assert len(customers) == 24

    reachable = set()
    for s in suppliers:
        reachable |= nx.descendants(g, s)
    unreachable = [c for c in customers if c not in reachable]
    assert unreachable == []


def test_edge_attributes_present():
    g = build_network()
    _, _, data = next(iter(g.edges(data=True)))
    for key in (
        "cost_per_ton",
        "co2_per_ton",
        "transit_days",
        "reliability_alpha",
        "reliability_beta",
        "mode",
        "fleet_tier",
    ):
        assert key in data


def test_echelon_structure():
    g = build_network()
    edge_type_pairs = {
        (g.nodes[u]["node_type"], g.nodes[v]["node_type"]) for u, v, _ in g.edges(keys=True)
    }
    assert ("Supplier", "Warehouse") in edge_type_pairs
    assert ("Warehouse", "Factory") in edge_type_pairs
    assert ("Factory", "Customer") in edge_type_pairs
    assert ("Warehouse", "Customer") in edge_type_pairs
