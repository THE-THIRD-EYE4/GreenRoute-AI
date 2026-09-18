"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { NodeCoordinate } from "@/lib/types";

export interface QuoteFormValue {
  productId: string;
  quantity: number;
  origin: string;
  destination: string;
  deadline: string;
}

const PRODUCTS = [
  { id: "P011", name: "Control Unit" },
  { id: "P012", name: "Sensor Module" },
  { id: "P013", name: "Industrial Gateway" },
  { id: "P014", name: "Motor Controller" },
  { id: "P015", name: "Smart Meter" },
  { id: "P001", name: "Copper Wire" },
  { id: "P002", name: "Aluminum Sheet" },
  { id: "P003", name: "Polymer Resin" },
];

export function QuoteForm({
  nodes,
  value,
  onChange,
  onSubmit,
  loading,
}: {
  nodes: NodeCoordinate[];
  value: QuoteFormValue;
  onChange: (v: QuoteFormValue) => void;
  onSubmit: () => void;
  loading: boolean;
}) {
  const [local, setLocal] = useState(value);

  function update<K extends keyof QuoteFormValue>(key: K, v: QuoteFormValue[K]) {
    const next = { ...local, [key]: v };
    setLocal(next);
    onChange(next);
  }

  return (
    <div className="flex flex-col gap-3 border-b border-rule bg-surface p-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <div className="flex flex-col gap-1">
          <label className="text-11 text-ink/50">Product</label>
          <Select value={local.productId} onValueChange={(v) => update("productId", v)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PRODUCTS.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-11 text-ink/50">Quantity</label>
          <input
            type="number"
            min={1}
            value={local.quantity}
            onChange={(e) => update("quantity", Number(e.target.value))}
            className="h-8 rounded border border-rule bg-surface px-2 text-13 tabular text-ink"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-11 text-ink/50">Origin</label>
          <Select value={local.origin} onValueChange={(v) => update("origin", v)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {nodes.map((n) => (
                <SelectItem key={n.node_id} value={n.node_id}>
                  {n.node_id} &middot; {n.city}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-11 text-ink/50">Destination</label>
          <Select value={local.destination} onValueChange={(v) => update("destination", v)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {nodes.map((n) => (
                <SelectItem key={n.node_id} value={n.node_id}>
                  {n.node_id} &middot; {n.city}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-11 text-ink/50">Deadline</label>
          <input
            type="date"
            value={local.deadline}
            onChange={(e) => update("deadline", e.target.value)}
            className="h-8 rounded border border-rule bg-surface px-2 text-13 tabular text-ink"
          />
        </div>
      </div>

      <div>
        <Button onClick={onSubmit} disabled={loading}>
          {loading ? "Getting quote…" : "Get quote"}
        </Button>
      </div>
    </div>
  );
}
