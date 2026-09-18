"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Header } from "@/components/console/Header";
import { ScenarioPicker } from "@/components/console/ScenarioPicker";
import { MapPanel } from "@/components/console/MapPanel";
import { ParetoChart } from "@/components/console/ParetoChart";
import { PlanDetail } from "@/components/console/PlanDetail";
import { LaneDetail } from "@/components/console/LaneDetail";
import { VarianceStrip } from "@/components/console/VarianceStrip";
import { api, ApiError } from "@/lib/api";
import { ApiStatus } from "@/components/ui/ApiStatus";
import type {
  NodeCoordinate,
  ObjectiveWeights,
  ParetoPoint,
  ParetoResponse,
  RouteEdge,
  ScenarioRow,
  TwinState,
  VarianceRow,
} from "@/lib/types";

const DEFAULT_WEIGHTS: ObjectiveWeights = {
  cost: 0.34,
  co2: 0.33,
  lead_time: 0.11,
  reliability: 0.11,
  stockout: 0.11,
};

export default function ConsolePage() {
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<NodeCoordinate[]>([]);
  const [routes, setRoutes] = useState<RouteEdge[]>([]);
  const [scenarios, setScenarios] = useState<ScenarioRow[]>([]);
  const [twin, setTwin] = useState<TwinState | null>(null);
  const [variance, setVariance] = useState<VarianceRow[]>([]);
  const [front, setFront] = useState<ParetoResponse | null>(null);
  const [selectedPlan, setSelectedPlan] = useState<ParetoPoint | null>(null);
  const [selectedPlanLabel, setSelectedPlanLabel] = useState<string | null>(null);
  const [weights, setWeights] = useState<ObjectiveWeights>(DEFAULT_WEIGHTS);
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);
  const [selectedLaneId, setSelectedLaneId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [flashKey, setFlashKey] = useState(0);

  const loadedOnce = useRef(false);

  useEffect(() => {
    if (loadedOnce.current) return;
    loadedOnce.current = true;
    (async () => {
      const [n, r, s, t, p] = await Promise.all([
        api.networkNodes(),
        api.networkRoutes(),
        api.scenarios(),
        api.twinState(),
        api.pareto(),
      ]);
      setNodes(n);
      setRoutes(r);
      setScenarios(s);
      setTwin(t);
      setFront(p);
    })().catch((err) => {
      // Surface it: a swallowed load failure renders as a blank console with no
      // explanation, which is the single most confusing failure mode here.
      console.error("initial load failed", err);
      setLoadError(err instanceof ApiError ? err.userMessage : String(err));
    });
  }, []);

  const refreshVariance = useCallback(async () => {
    try {
      const v = await api.twinVariance();
      setVariance(v.rows);
    } catch (err) {
      console.error("variance refresh failed", err);
    }
  }, []);

  const runScenario = useCallback(
    async (scenarioId: string) => {
      setIsRunning(true);
      try {
        const [event, paretoResp] = await Promise.all([
          api.twinEvent(scenarioId, 3),
          api.pareto(scenarioId),
        ]);
        const lastState = event.states[event.states.length - 1];
        setTwin(lastState);
        setActiveScenarioId(scenarioId);
        setFront(paretoResp);
        setSelectedPlan(null);
        setSelectedPlanLabel(null);
        setFlashKey((k) => k + 1);
        await refreshVariance();
      } catch (err) {
        console.error("run scenario failed", err);
      } finally {
        setIsRunning(false);
      }
    },
    [refreshVariance],
  );

  const resetTwin = useCallback(async () => {
    setIsRunning(true);
    try {
      await api.twinReset();
      const [t, p] = await Promise.all([api.twinState(), api.pareto()]);
      setTwin(t);
      setFront(p);
      setActiveScenarioId(null);
      setSelectedPlan(null);
      setSelectedPlanLabel(null);
      setSelectedLaneId(null);
      setVariance([]);
    } catch (err) {
      console.error("reset failed", err);
    } finally {
      setIsRunning(false);
    }
  }, []);

  const handleWeightsChange = useCallback(
    (w: ObjectiveWeights) => {
      setWeights(w);
      api
        .optimize({ weights: w, scenario_id: activeScenarioId ?? undefined })
        .then((res) => {
          setSelectedPlan(res.selected);
          setSelectedPlanLabel(null);
        })
        .catch((err) => console.error("optimize failed", err));
    },
    [activeScenarioId],
  );

  const handleSelectParetoPoint = useCallback(
    (point: ParetoPoint) => {
      setSelectedPlan(point);
      if (front) {
        if (point.id === front.cheapest_id) setSelectedPlanLabel("Cheapest");
        else if (point.id === front.greenest_id) setSelectedPlanLabel("Greenest");
        else if (point.id === front.most_resilient_id) setSelectedPlanLabel("Most resilient");
        else setSelectedPlanLabel(null);
      }
    },
    [front],
  );

  const daysOfCoverByNode = useMemo(() => {
    const m = new Map<string, number>();
    if (!twin) return m;
    for (const inv of twin.inventory) {
      const existing = m.get(inv.node_id);
      if (existing === undefined || inv.days_of_cover < existing) {
        m.set(inv.node_id, inv.days_of_cover);
      }
    }
    return m;
  }, [twin]);

  const activeRouteIds = useMemo(() => {
    const s = new Set<string>();
    if (!twin) return s;
    for (const shipment of twin.in_transit) {
      if (shipment.status === "IN_TRANSIT") s.add(shipment.route_id);
    }
    return s;
  }, [twin]);

  const selectedVarianceRow = useMemo(
    () => variance.find((r) => r.route_id === selectedLaneId) ?? null,
    [variance, selectedLaneId],
  );

  const freshness = isRunning ? "reoptimizing" : activeScenarioId ? "drifted" : "current";

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-paper">
      <ApiStatus />
      {loadError && (
        <div
          className="shrink-0 border-b border-rule px-4 py-2 text-11"
          style={{ backgroundColor: "var(--signal-12)", color: "var(--signal)" }}
          role="alert"
        >
          Initial data load failed: {loadError}
        </div>
      )}
      <Header tick={twin?.tick ?? 0} freshness={freshness} driftSince={activeScenarioId} />
      <div className="flex min-h-0 flex-1">
        <ScenarioPicker
          scenarios={scenarios}
          activeScenarioId={activeScenarioId}
          onRunScenario={runScenario}
          weights={weights}
          onWeightsChange={handleWeightsChange}
          onResetTwin={resetTwin}
          isRunning={isRunning}
        />

        <main className="flex min-h-0 flex-1 flex-col">
          <div key={flashKey} className="h-[55%] min-h-0 animate-flash-once">
            <MapPanel
              nodes={nodes}
              routes={routes}
              daysOfCoverByNode={daysOfCoverByNode}
              activeRouteIds={activeRouteIds}
              onSelectLane={setSelectedLaneId}
              selectedLaneId={selectedLaneId}
            />
          </div>
          <div className="h-[45%] min-h-0">
            <ParetoChart front={front} selectedId={selectedPlan?.id ?? null} onSelect={handleSelectParetoPoint} />
          </div>
        </main>

        <aside className="flex w-[360px] shrink-0 flex-col border-l border-rule bg-surface">
          <PlanDetail plan={selectedPlan} label={selectedPlanLabel} />
          <LaneDetail row={selectedVarianceRow} />
          <VarianceStrip rows={variance} selectedRouteId={selectedLaneId} onSelectRoute={setSelectedLaneId} />
        </aside>
      </div>
    </div>
  );
}
