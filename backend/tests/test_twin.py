import warnings

import pytest

from dispatch.twin import DigitalTwin

warnings.filterwarnings("ignore")


def test_baseline_has_six_state_layers_and_is_never_mutated():
    twin = DigitalTwin()
    baseline = twin.baseline
    assert len(baseline.inventory) > 0
    assert len(baseline.supplier) == 15
    assert len(baseline.lane) == 389
    assert len(baseline.demand) == 24
    assert len(baseline.vehicle) == 12
    assert baseline.in_transit == ()

    twin.run_ticks(3)
    assert twin.baseline is baseline
    assert twin.baseline.tick == 0
    assert twin.log[0] is baseline


def test_log_is_append_only():
    twin = DigitalTwin()
    twin.run_ticks(4)
    ticks = [s.tick for s in twin.log]
    assert ticks == sorted(ticks)
    assert ticks == list(range(5))


def test_scenario_route_closure_closes_all_modes_on_that_lane():
    twin = DigitalTwin()
    twin.apply_scenario("SC004")  # Route closure, Affected_Node=R020 (original id)
    state = twin.run_ticks(1)[0]
    closed = [l for l in state.lane if l.status == "CLOSED"]
    assert len(closed) > 1  # multiple mode-variants on that lane, all closed


def test_scenario_expires_after_duration():
    twin = DigitalTwin()
    twin.apply_scenario("SC004")  # Duration_days = 4
    states = twin.run_ticks(6)
    assert states[0].active_scenarios == ("SC004",)
    assert states[-1].active_scenarios == ()


def test_reset_to_baseline_restores_state():
    twin = DigitalTwin()
    twin.apply_scenario("SC001")
    twin.run_ticks(3)
    assert len(twin.log) == 4
    twin.reset_to_baseline()
    assert len(twin.log) == 1
    assert twin.log[0] is twin.baseline
    assert twin.reoptimizations == []


def test_reoptimization_uses_live_perturbed_capacity_not_static():
    twin = DigitalTwin()
    twin.apply_scenario("SC011")  # Supplier outage, S001
    states = twin.run_ticks(1)
    live_md = twin._live_model_data(states[0])
    s001_capacity = live_md.supplier_offers.loc[live_md.supplier_offers.Supplier_ID == "S001", "Capacity"]
    assert (s001_capacity == 0).all()


def test_variance_table_reports_planned_vs_realized():
    twin = DigitalTwin()
    twin.run_ticks(6)
    rows = twin.variance_table()
    assert len(rows) > 0
    for r in rows:
        assert "planned_transit_days" in r and "realized_transit_days" in r
        assert r["drift_days"] == pytest.approx(r["realized_transit_days"] - r["planned_transit_days"])
