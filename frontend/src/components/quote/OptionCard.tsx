"use client";

import { cn, formatINR, formatKg } from "@/lib/utils";
import type { ModeOptionOut } from "@/lib/types";

const MODE_LABEL: Record<string, string> = {
  Truck_Diesel: "Truck (diesel)",
  Truck_Electric: "Truck (electric)",
  Rail: "Rail",
  Air: "Air",
};

function formatEtaDay(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + Math.round(days));
  return d.toLocaleDateString("en-IN", { weekday: "short" });
}

export function OptionCard({
  title,
  option,
  highlight,
}: {
  title: "Cheapest" | "Greenest" | "Fastest";
  option: ModeOptionOut;
  highlight?: boolean;
}) {
  const onTimePct = Math.round(option.on_time_probability * 100);

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded border p-4",
        highlight ? "border-steel bg-steel-10" : "border-rule bg-surface",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="text-13 font-semibold text-ink">{title}</span>
        <span className="text-11 text-ink/50">{MODE_LABEL[option.mode] ?? option.mode}</span>
      </div>

      <div className="flex items-baseline gap-1">
        <span className="text-28 font-semibold tabular text-ink">&#8377;{formatINR(option.cost)}</span>
      </div>

      <dl className="grid grid-cols-2 gap-y-1.5 text-13">
        <dt className="text-ink/50">CO2</dt>
        <dd className="text-right tabular text-ink">{formatKg(option.co2_kg, 2)}</dd>
        <dt className="text-ink/50">Distance</dt>
        <dd className="text-right tabular text-ink">{option.distance_km.toFixed(0)} km</dd>
      </dl>

      <div className="rounded border border-rule bg-paper px-2.5 py-2">
        <div className="mb-0.5 text-11 text-ink/50">Arrival</div>
        <div className="text-13 text-ink">
          {formatEtaDay(option.eta_p50_days)}, {onTimePct}% on time, P90 {formatEtaDay(option.eta_p90_days)}
        </div>
      </div>

      {option.mode === "Air" && option.flights.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="text-11 font-medium text-ink/60">Flights</div>
          {option.flights.map((f) => (
            <div key={f.flight_id} className="rounded border border-rule px-2 py-1.5 text-11">
              <div className="flex justify-between text-ink">
                <span className="font-medium">{f.flight_id}</span>
                <span>{f.carrier_name}</span>
              </div>
              <div className="mt-0.5 flex justify-between text-ink/60">
                <span>Cutoff {new Date(f.cutoff_utc).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
                <span className="tabular">{f.remaining_uld_kg.toFixed(0)} kg free</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {(option.mode === "Truck_Diesel" || option.mode === "Truck_Electric") && option.road_stop_sequence && (
        <div className="flex flex-col gap-1 text-11">
          <div className="font-medium text-ink/60">Route</div>
          <div className="text-ink">
            {option.road_stop_sequence.join(" → ")} &middot; vehicle {option.road_vehicle_id} at{" "}
            {option.road_vehicle_utilisation_pct?.toFixed(1)}% utilisation
          </div>
        </div>
      )}
    </div>
  );
}
