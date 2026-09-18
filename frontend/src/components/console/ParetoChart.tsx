"use client";

import { useMemo, useState } from "react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDays, formatINR, formatKg, formatPct } from "@/lib/utils";
import type { ParetoPoint, ParetoResponse } from "@/lib/types";

function reliabilityColor(reliability: number, min: number, max: number): string {
  const span = max - min || 1;
  const t = Math.min(1, Math.max(0, (reliability - min) / span));
  // moss -> amber ramp, sequential (never a rainbow)
  const lerp = (a: number, b: number) => Math.round(a + (b - a) * t);
  const from = [0x8a, 0x62, 0x12];
  const to = [0x3f, 0x6b, 0x47];
  const rgb = from.map((c, i) => lerp(c, to[i]));
  return `rgb(${rgb.join(",")})`;
}

export function ParetoChart({
  front,
  selectedId,
  onSelect,
}: {
  front: ParetoResponse | null;
  selectedId: string | null;
  onSelect: (point: ParetoPoint) => void;
}) {
  const [view, setView] = useState<"scatter" | "parallel">("scatter");

  const points = useMemo(() => front?.front ?? [], [front]);
  const relBounds = useMemo(() => {
    if (points.length === 0) return { min: 0, max: 1 };
    const vals = points.map((p) => p.reliability);
    return { min: Math.min(...vals), max: Math.max(...vals) };
  }, [points]);

  const labelFor = (id: string) => {
    if (!front) return null;
    if (id === front.cheapest_id) return "Cheapest";
    if (id === front.greenest_id) return "Greenest";
    if (id === front.most_resilient_id) return "Most resilient";
    return null;
  };

  return (
    <section className="flex h-full min-h-0 flex-col border-t border-rule bg-surface">
      <div className="flex items-center justify-between border-b border-rule px-3 py-2">
        <h2 className="text-13 font-medium text-ink">Pareto front</h2>
        <Tabs value={view} onValueChange={(v) => setView(v as "scatter" | "parallel")}>
          <TabsList>
            <TabsTrigger value="scatter">Cost vs CO2</TabsTrigger>
            <TabsTrigger value="parallel">Parallel coordinates</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <div className="min-h-0 flex-1 p-2">
        {points.length === 0 ? (
          <div className="flex h-full items-center justify-center text-13 text-ink/40">
            No plan computed. Pick a scenario to see the trade-off.
          </div>
        ) : view === "scatter" ? (
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke="var(--rule)" strokeDasharray="0" vertical={false} />
              <XAxis
                type="number"
                dataKey="cost"
                name="Cost"
                tickFormatter={(v) => `₹${formatINR(v)}`}
                tick={{ fontSize: 11, fill: "var(--ink)" }}
                stroke="var(--rule)"
                tickLine={false}
              />
              <YAxis
                type="number"
                dataKey="co2_kg"
                name="CO2"
                tickFormatter={(v) => `${v.toFixed(0)}kg`}
                tick={{ fontSize: 11, fill: "var(--ink)" }}
                stroke="var(--rule)"
                tickLine={false}
                width={48}
              />
              <ZAxis type="number" dataKey="n_suppliers_active" range={[60, 260]} name="Suppliers active" />
              <Tooltip
                cursor={{ stroke: "var(--steel)", strokeWidth: 1 }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null;
                  const p = payload[0].payload as ParetoPoint;
                  const label = labelFor(p.id);
                  return (
                    <div className="rounded border border-rule bg-surface px-2 py-1.5 text-11">
                      {label && <div className="mb-0.5 font-medium text-steel">{label}</div>}
                      <div className="tabular">Cost ₹{formatINR(p.cost)}</div>
                      <div className="tabular">CO2 {formatKg(p.co2_kg, 2)}</div>
                      <div className="tabular">Lead time {formatDays(p.lead_time_days)}</div>
                      <div className="tabular">Reliability {formatPct(p.reliability, 1)}</div>
                    </div>
                  );
                }}
              />
              <Scatter data={points} onClick={(d) => onSelect(d as unknown as ParetoPoint)} isAnimationActive>
                {points.map((p) => {
                  const label = labelFor(p.id);
                  const selected = p.id === selectedId;
                  return (
                    <Cell
                      key={p.id}
                      cursor="pointer"
                      fill={reliabilityColor(p.reliability, relBounds.min, relBounds.max)}
                      stroke={selected ? "var(--steel)" : label ? "var(--ink)" : "transparent"}
                      strokeWidth={selected ? 2.5 : label ? 1.5 : 0}
                    />
                  );
                })}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        ) : (
          <ParallelCoordinates points={points} selectedId={selectedId} onSelect={onSelect} labelFor={labelFor} />
        )}
      </div>
    </section>
  );
}

const PARALLEL_DIMS: { key: keyof ParetoPoint; label: string; invert?: boolean }[] = [
  { key: "cost", label: "Cost" },
  { key: "co2_kg", label: "CO2" },
  { key: "lead_time_days", label: "Lead time" },
  { key: "reliability", label: "Reliability", invert: true },
  { key: "n_suppliers_active", label: "Suppliers", invert: true },
];

function ParallelCoordinates({
  points,
  selectedId,
  onSelect,
  labelFor,
}: {
  points: ParetoPoint[];
  selectedId: string | null;
  onSelect: (p: ParetoPoint) => void;
  labelFor: (id: string) => string | null;
}) {
  const width = 640;
  const height = 260;
  const padX = 48;
  const padY = 20;
  const axisGap = (width - padX * 2) / (PARALLEL_DIMS.length - 1);

  const bounds = PARALLEL_DIMS.map((d) => {
    const vals = points.map((p) => Number(p[d.key]));
    return { min: Math.min(...vals), max: Math.max(...vals) };
  });

  function yFor(dimIdx: number, value: number) {
    const { min, max } = bounds[dimIdx];
    const span = max - min || 1;
    let t = (value - min) / span;
    if (PARALLEL_DIMS[dimIdx].invert) t = 1 - t;
    return padY + t * (height - padY * 2);
  }

  return (
    <div className="flex h-full items-center justify-center overflow-x-auto">
      <svg width={width} height={height + 24} role="img" aria-label="Parallel coordinates of Pareto objectives">
        {PARALLEL_DIMS.map((d, i) => (
          <g key={d.key}>
            <line
              x1={padX + i * axisGap}
              x2={padX + i * axisGap}
              y1={padY}
              y2={height - padY}
              stroke="var(--rule)"
            />
            <text
              x={padX + i * axisGap}
              y={height - padY + 16}
              textAnchor="middle"
              fontSize={11}
              fill="var(--ink)"
            >
              {d.label}
            </text>
          </g>
        ))}
        {points.map((p) => {
          const label = labelFor(p.id);
          const selected = p.id === selectedId;
          const path = PARALLEL_DIMS.map((d, i) => `${padX + i * axisGap},${yFor(i, Number(p[d.key]))}`).join(" ");
          return (
            <polyline
              key={p.id}
              points={path}
              fill="none"
              stroke={selected ? "var(--steel)" : label ? "var(--ink)" : "var(--rule)"}
              strokeWidth={selected ? 2.5 : label ? 1.5 : 1}
              opacity={selected || label ? 1 : 0.5}
              className="cursor-pointer"
              onClick={() => onSelect(p)}
            />
          );
        })}
      </svg>
    </div>
  );
}
