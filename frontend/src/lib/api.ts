import type {
  FleetUtilisationRow,
  NodeCoordinate,
  OptimizeRequest,
  OptimizeResponse,
  ParetoResponse,
  QuoteRequest,
  QuoteResponse,
  RouteEdge,
  ScenarioRow,
  TwinEventResponse,
  TwinState,
  VarianceResponse,
  VRPResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  pareto: (scenarioId?: string) =>
    request<ParetoResponse>(`/pareto${scenarioId ? `?scenario_id=${encodeURIComponent(scenarioId)}` : ""}`),

  optimize: (body: OptimizeRequest) =>
    request<OptimizeResponse>(`/optimize`, { method: "POST", body: JSON.stringify(body) }),

  twinState: () => request<TwinState>(`/twin/state`),

  twinEvent: (scenarioId: string, nTicks = 1) =>
    request<TwinEventResponse>(`/twin/event`, {
      method: "POST",
      body: JSON.stringify({ scenario_id: scenarioId, n_ticks: nTicks }),
    }),

  twinVariance: () => request<VarianceResponse>(`/twin/variance`),

  twinReset: () => request<{ status: string; tick: number }>(`/twin/reset`, { method: "POST" }),

  routesVrp: (depot?: string) =>
    request<VRPResponse>(`/routes/vrp${depot ? `?depot=${encodeURIComponent(depot)}` : ""}`),

  fleetUtilisation: () => request<FleetUtilisationRow[]>(`/fleet/utilisation`),

  shipmentQuote: (body: QuoteRequest) =>
    request<QuoteResponse>(`/shipment/quote`, { method: "POST", body: JSON.stringify(body) }),

  networkNodes: () => request<NodeCoordinate[]>(`/network/nodes`),

  networkRoutes: () => request<RouteEdge[]>(`/network/routes`),

  scenarios: () => request<ScenarioRow[]>(`/scenarios`),
};
