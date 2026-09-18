"use client";

import { Badge } from "@/components/ui/badge";
import { formatDays, formatINR, formatKg, formatPct } from "@/lib/utils";
import type { ParetoPoint } from "@/lib/types";

export function PlanDetail({
  plan,
  label,
}: {
  plan: ParetoPoint | null;
  label: string | null;
}) {
  if (!plan) {
    return (
      <div className="border-b border-rule px-3 py-4">
        <h2 className="mb-1 text-13 font-medium text-ink">Selected plan</h2>
        <p className="text-13 text-ink/50">No disruption active. Pick a scenario to see the plan react.</p>
      </div>
    );
  }

  const rows: { label: string; value: string }[] = [
    { label: "Cost", value: `₹${formatINR(plan.cost)}` },
    { label: "CO2", value: formatKg(plan.co2_kg, 2) },
    { label: "Lead time", value: formatDays(plan.lead_time_days) },
    { label: "Reliability", value: formatPct(plan.reliability, 1) },
    { label: "Suppliers active", value: String(plan.n_suppliers_active) },
  ];
  if (plan.co2_cap != null) rows.push({ label: "CO2 cap", value: formatKg(plan.co2_cap, 2) });
  if (plan.lead_time_cap != null) rows.push({ label: "Lead time cap", value: formatDays(plan.lead_time_cap) });

  return (
    <div className="border-b border-rule px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-13 font-medium text-ink">Selected plan</h2>
        {label && <Badge tone="steel">{label}</Badge>}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col">
            <dt className="text-11 text-ink/50">{r.label}</dt>
            <dd className="tabular text-13 text-ink">{r.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
