"use client";

import { Badge } from "@/components/ui/badge";
import { formatKg } from "@/lib/utils";
import type { PhysicalsOut } from "@/lib/types";

export function PhysicalsSummary({ physicals }: { physicals: PhysicalsOut }) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-rule bg-paper px-4 py-3">
      <Stat label="Gross weight" value={formatKg(physicals.gross_kg)} />
      <Stat label="Volume" value={`${physicals.volume_m3.toFixed(3)} m³`} />
      <Stat label="Chargeable (air)" value={formatKg(physicals.chargeable_kg_air)} />
      <Stat label="Chargeable (road)" value={formatKg(physicals.chargeable_kg_road)} />
      {physicals.contains_hazmat && <Badge tone="amber">Contains hazmat</Badge>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-11 text-ink/50">{label}</span>
      <span className="tabular text-15 text-ink">{value}</span>
    </div>
  );
}
