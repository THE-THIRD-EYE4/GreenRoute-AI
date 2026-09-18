"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { ApiStatus } from "@/components/ui/ApiStatus";
import { QuoteForm, type QuoteFormValue } from "@/components/quote/QuoteForm";
import { PhysicalsSummary } from "@/components/quote/PhysicalsSummary";
import { OptionCard } from "@/components/quote/OptionCard";
import { CarbonContrast } from "@/components/quote/CarbonContrast";
import { QuoteRouteMap } from "@/components/quote/QuoteRouteMap";
import { Badge } from "@/components/ui/badge";
import type { NodeCoordinate, QuoteResponse } from "@/lib/types";

function defaultDeadline(daysAhead: number): string {
  const d = new Date();
  d.setDate(d.getDate() + daysAhead);
  return d.toISOString().slice(0, 10);
}

export default function QuotePage() {
  const [nodes, setNodes] = useState<NodeCoordinate[]>([]);
  const [form, setForm] = useState<QuoteFormValue>({
    productId: "P011",
    quantity: 50,
    origin: "F01",
    destination: "C003",
    deadline: defaultDeadline(7),
  });
  const [quote, setQuote] = useState<QuoteResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .networkNodes()
      .then(setNodes)
      .catch((err) => console.error("failed to load nodes", err));
  }, []);

  async function handleSubmit() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.shipmentQuote({
        items: [{ product_id: form.productId, quantity: form.quantity }],
        origin: form.origin,
        destination: form.destination,
        deadline: `${form.deadline}T00:00:00`,
      });
      setQuote(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not get a quote. Check the origin/destination and try again.");
      setQuote(null);
    } finally {
      setLoading(false);
    }
  }

  const roadOption = quote?.options.find((o) => o.feasible && o.road_stop_sequence);
  const airOption = quote?.options.find((o) => o.mode === "Air");

  return (
    <div className="flex h-screen flex-col overflow-y-auto bg-paper">
      <ApiStatus />
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-rule bg-surface px-4">
        <div className="flex items-center gap-3">
          <span className="text-15 font-semibold text-ink">SU-02 Dispatch</span>
          <span className="text-13 text-ink/50">Shipment quote</span>
        </div>
        <Link href="/" className="text-13 text-steel hover:underline">
          Back to console
        </Link>
      </header>

      <QuoteForm nodes={nodes} value={form} onChange={setForm} onSubmit={handleSubmit} loading={loading} />

      {error && (
        <div className="border-b border-rule bg-signal-12 px-4 py-3 text-13 text-signal">{error}</div>
      )}

      {!quote && !error && (
        <div className="flex flex-1 items-center justify-center text-13 text-ink/40">
          Fill in the shipment and get a quote to see feasible modes.
        </div>
      )}

      {quote && (
        <div className="flex flex-1 flex-col">
          <PhysicalsSummary physicals={quote.physicals} />

          <div className="flex flex-wrap gap-1.5 border-b border-rule px-4 py-2.5">
            <span className="mr-1 text-11 text-ink/50">Feasible modes</span>
            {quote.options.map((o) => (
              <Badge key={o.mode} tone={o.feasible ? "moss" : "neutral"}>
                {o.mode.replace("_", " ")}
                {!o.feasible && " — infeasible"}
              </Badge>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-3">
            {quote.cheapest && <OptionCard title="Cheapest" option={quote.cheapest} highlight />}
            {quote.greenest && <OptionCard title="Greenest" option={quote.greenest} />}
            {quote.fastest && <OptionCard title="Fastest" option={quote.fastest} />}
          </div>

          {airOption?.feasible && (
            <div className="px-4 pb-4">
              <CarbonContrast multiple={quote.air_vs_rail_co2_multiple} />
            </div>
          )}

          {roadOption?.road_stop_sequence && (
            <div className="flex flex-col gap-2 px-4 pb-4">
              <h3 className="text-13 font-medium text-ink">Road route</h3>
              <div className="h-64 overflow-hidden rounded border border-rule">
                <QuoteRouteMap nodes={nodes} stopSequence={roadOption.road_stop_sequence} />
              </div>
              {roadOption.road_cumulative_load_kg && (
                <div className="flex flex-col gap-1">
                  <div className="text-11 text-ink/50">Cumulative load</div>
                  <div className="flex h-2 overflow-hidden rounded bg-rule">
                    <div
                      className="h-full bg-steel"
                      style={{
                        width: `${Math.min(100, roadOption.road_vehicle_utilisation_pct ?? 0)}%`,
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {quote.options.every((o) => !o.feasible) && (
            <div className="px-4 pb-4 text-13 text-ink/60">
              No mode clears this deadline with the given quantity. Try a later deadline or a smaller order.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
