"use client";

export function CarbonContrast({ multiple }: { multiple: number }) {
  return (
    <div className="flex items-center gap-3 rounded border border-signal bg-signal-12 px-4 py-3">
      <span className="text-28 font-semibold tabular text-signal">{multiple.toFixed(1)}&times;</span>
      <span className="text-13 text-ink">
        Air freight emits roughly <strong>{multiple.toFixed(1)} times</strong> more CO2 than rail per ton-km on this
        lane.
      </span>
    </div>
  );
}
