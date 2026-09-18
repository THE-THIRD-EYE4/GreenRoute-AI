"use client";

import { formatDays } from "@/lib/utils";
import type { VarianceRow } from "@/lib/types";

export function LaneDetail({ row }: { row: VarianceRow | null }) {
  if (!row) return null;
  return (
    <div className="border-b border-rule bg-paper px-3 py-2.5">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-13 font-medium text-ink">Lane {row.route_id}</h3>
        {row.breached && <span className="text-11 font-medium text-signal">Drift breached</span>}
      </div>
      <div className="flex gap-4 text-13">
        <div>
          <div className="text-11 text-ink/50">Planned</div>
          <div className="tabular text-ink">{formatDays(row.planned_transit_days)}</div>
        </div>
        <div>
          <div className="text-11 text-ink/50">Realized</div>
          <div className="tabular text-ink">{formatDays(row.realized_transit_days)}</div>
        </div>
        <div>
          <div className="text-11 text-ink/50">Drift</div>
          <div className="tabular text-ink">{row.drift_days > 0 ? "+" : ""}{formatDays(row.drift_days)}</div>
        </div>
      </div>
    </div>
  );
}
