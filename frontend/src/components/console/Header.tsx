"use client";

import { cn } from "@/lib/utils";

type PlanFreshness = "current" | "drifted" | "reoptimizing";

export function Header({
  tick,
  freshness,
  driftSince,
}: {
  tick: number;
  freshness: PlanFreshness;
  driftSince: string | null;
}) {
  const label =
    freshness === "current"
      ? "Plan current"
      : freshness === "reoptimizing"
        ? "Re-optimizing"
        : `Drift detected${driftSince ? ` ${driftSince}` : ""}`;

  const dotTone =
    freshness === "current" ? "bg-moss" : freshness === "reoptimizing" ? "bg-steel" : "bg-amber";

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-rule bg-surface px-4">
      <div className="flex items-center gap-3">
        <span className="text-15 font-semibold tracking-tight text-ink">SU-02 Dispatch</span>
        <span className="text-13 text-ink/50">Sustainable supply network</span>
      </div>
      <div className="flex items-center gap-4 text-13">
        <div className="tabular text-ink/70">
          Twin day <span className="font-medium text-ink">{tick}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={cn("h-1.5 w-1.5 rounded-full", dotTone, freshness === "reoptimizing" && "animate-pulse")} />
          <span className="text-ink/80">{label}</span>
        </div>
      </div>
    </header>
  );
}
