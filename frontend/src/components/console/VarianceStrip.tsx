"use client";

import { cn, formatDays } from "@/lib/utils";
import type { VarianceRow } from "@/lib/types";

export function VarianceStrip({
  rows,
  selectedRouteId,
  onSelectRoute,
}: {
  rows: VarianceRow[];
  selectedRouteId: string | null;
  onSelectRoute: (routeId: string) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-rule px-3 py-2">
        <h2 className="text-13 font-medium text-ink">Twin vs plan variance</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <p className="px-3 py-6 text-13 text-ink/50">
            No lane activity yet. Run a scenario to see realized transit drift.
          </p>
        ) : (
          <table className="w-full border-collapse text-13">
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-rule text-11 text-ink/50">
                <th className="px-3 py-1 text-left font-normal">Lane</th>
                <th className="px-2 py-1 text-right font-normal">Planned</th>
                <th className="px-2 py-1 text-right font-normal">Actual</th>
                <th className="px-3 py-1 text-right font-normal">Drift</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.route_id}
                  onClick={() => onSelectRoute(r.route_id)}
                  className={cn(
                    "h-row cursor-pointer border-b border-rule transition-colors hover:bg-paper",
                    r.breached && "border-l-[3px] border-l-signal",
                    r.route_id === selectedRouteId && "bg-steel-10",
                  )}
                >
                  <td className="px-3 py-1.5 tabular text-ink">{r.route_id}</td>
                  <td className="px-2 py-1.5 text-right tabular text-ink/70">{formatDays(r.planned_transit_days)}</td>
                  <td className="px-2 py-1.5 text-right tabular text-ink/70">{formatDays(r.realized_transit_days)}</td>
                  <td
                    className={cn(
                      "px-3 py-1.5 text-right tabular font-medium",
                      r.drift_days > 0 ? "text-signal" : r.drift_days < 0 ? "text-moss" : "text-ink/50",
                    )}
                  >
                    {r.drift_days > 0 ? "+" : ""}
                    {formatDays(r.drift_days)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
