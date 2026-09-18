"""Global constants shared across the dispatch package.

Every stochastic process in this package is seeded with SEED so a demo run
is bit-for-bit reproducible between runs.
"""
from __future__ import annotations

from pathlib import Path

SEED = 42

# backend/dispatch/config.py -> backend/ -> repo root -> data/
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Supplier diversification cap: no supplier may exceed this share of a
# product's total allocated volume. Without this the optimizer trivially
# picks one cheapest source per product (supplier capacity is not binding)
# and the Pareto front collapses to a single point.
MAX_SUPPLIER_SHARE = 0.40

# Beta-Binomial prior strength (pseudo-observations) for supplier reliability
# shrinkage. n=26 (S001) shrinks meaningfully toward the prior; n=78 (S003)
# barely moves.
RELIABILITY_PRIOR_STRENGTH = 15.0

# Chargeable-weight volumetric divisors (cm^3 per kg) by mode.
CHARGEABLE_DIVISOR_AIR = 6000
CHARGEABLE_DIVISOR_ROAD = 3000

# Standard Indian road detour factor applied to haversine great-circle
# distance to approximate real road distance.
ROAD_DETOUR_FACTOR = 1.29

# Ground-mode cost/CO2 correlation must stay below this threshold; it is the
# premise of the whole "balance cost against carbon" project. Assert it on
# every network build.
MAX_GROUND_MODE_COST_CO2_CORR = 0.80

GROUND_MODES = {"Truck_Diesel", "Truck_Electric", "Rail"}
