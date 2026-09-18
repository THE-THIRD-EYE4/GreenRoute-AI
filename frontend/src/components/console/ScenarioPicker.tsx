"use client";

import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ObjectiveWeights, ScenarioRow } from "@/lib/types";

const SLIDERS: { key: keyof ObjectiveWeights; label: string }[] = [
  { key: "cost", label: "Cost" },
  { key: "co2", label: "Carbon" },
  { key: "lead_time", label: "Lead time" },
  { key: "reliability", label: "Reliability" },
  { key: "stockout", label: "Stockout risk" },
];

export function ScenarioPicker({
  scenarios,
  activeScenarioId,
  onRunScenario,
  weights,
  onWeightsChange,
  onResetTwin,
  isRunning,
}: {
  scenarios: ScenarioRow[];
  activeScenarioId: string | null;
  onRunScenario: (scenarioId: string) => void;
  weights: ObjectiveWeights;
  onWeightsChange: (w: ObjectiveWeights) => void;
  onResetTwin: () => void;
  isRunning: boolean;
}) {
  const total = Object.values(weights).reduce((a, b) => a + b, 0) || 1;

  function setWeight(key: keyof ObjectiveWeights, value: number) {
    onWeightsChange({ ...weights, [key]: value });
  }

  return (
    <aside className="flex w-[280px] shrink-0 flex-col border-r border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule px-3 py-2.5">
        <h2 className="text-13 font-medium text-ink">Scenario</h2>
        <Button variant="secondary" size="sm" onClick={onResetTwin}>
          Reset twin to baseline
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
        {scenarios.length === 0 ? (
          <p className="px-2 py-6 text-13 text-ink/50">Loading scenarios&hellip;</p>
        ) : (
          <ul>
            {scenarios.map((s) => {
              const active = s.scenario_id === activeScenarioId;
              return (
                <li key={s.scenario_id}>
                  <button
                    onClick={() => onRunScenario(s.scenario_id)}
                    disabled={isRunning}
                    className={cn(
                      "flex w-full flex-col gap-0.5 rounded px-2 py-1.5 text-left transition-colors",
                      active ? "bg-steel-10" : "hover:bg-paper",
                      isRunning && "opacity-50",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-13 text-ink">{s.scenario_type}</span>
                      <span className="shrink-0 text-11 tabular text-ink/40">{s.scenario_id}</span>
                    </div>
                    <span className="text-11 text-ink/50">
                      {s.affected_node} &middot; {s.duration_days}d &middot; severity {s.severity.toFixed(2)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="border-t border-rule px-3 py-3">
        <h3 className="mb-2 text-13 font-medium text-ink">Objective weights</h3>
        <div className="flex flex-col gap-3">
          {SLIDERS.map(({ key, label }) => (
            <div key={key} className="flex flex-col gap-1">
              <div className="flex items-center justify-between text-11 text-ink/60">
                <span>{label}</span>
                <span className="tabular">{Math.round((weights[key] / total) * 100)}%</span>
              </div>
              <Slider
                min={0}
                max={1}
                step={0.01}
                value={[weights[key]]}
                onValueChange={([v]) => setWeight(key, v)}
              />
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
