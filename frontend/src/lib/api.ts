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

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Default per-request timeout. Heavy solves get a longer one explicitly. */
const DEFAULT_TIMEOUT_MS = 120_000;

export type ApiErrorKind = "network" | "timeout" | "http";

/**
 * A fetch failure and an HTTP error are different problems with different fixes,
 * and `fetch` reports both as thrown exceptions. Distinguishing them is the
 * difference between "your backend is not running" and "your request was invalid".
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly path: string;
  readonly url: string;

  constructor(kind: ApiErrorKind, path: string, message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.path = path;
    this.status = status;
    this.url = `${API_URL}${path}`;
  }

  /** Message intended for a user, not a console. */
  get userMessage(): string {
    switch (this.kind) {
      case "network":
        return `Cannot reach the backend at ${API_URL}. Is it running? Start it with: uvicorn dispatch.api:app --reload --port 8000`;
      case "timeout":
        return `The backend did not respond within ${DEFAULT_TIMEOUT_MS / 1000}s on ${this.path}. It may still be warming its solver caches.`;
      default:
        return `${this.path} failed with HTTP ${this.status}. ${this.message}`;
    }
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const timeoutMs = init?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      signal: init?.signal ?? controller.signal,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch (err) {
    // AbortError here means our own timeout fired, not a user cancellation.
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("timeout", path, "request aborted after timeout");
    }
    // A TypeError from fetch is the browser's way of saying the request never
    // reached a server: wrong port, backend down, or blocked by CORS.
    throw new ApiError(
      "network",
      path,
      err instanceof Error ? err.message : "network request failed",
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError("http", path, body || res.statusText, res.status);
  }
  return res.json() as Promise<T>;
}

export interface HealthResponse {
  status: string;
  ready: boolean;
  warming: {
    started: boolean;
    done: boolean;
    completed: number;
    total: number;
    current: string | null;
    percent: number;
    error: string | null;
  };
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

  /** Short timeout: this is the reachability probe, it must fail fast. */
  health: () => request<HealthResponse>(`/health`, { timeoutMs: 4000 }),
};
