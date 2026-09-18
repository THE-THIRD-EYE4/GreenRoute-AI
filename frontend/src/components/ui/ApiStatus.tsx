"use client";

import { useEffect, useState } from "react";
import { api, API_URL, ApiError, type HealthResponse } from "@/lib/api";

/**
 * Surfaces backend reachability and warm-up progress.
 *
 * This exists because every failure in the console page used to go to
 * console.error only, so a backend that was down -- or simply still warming its
 * 55 Pareto fronts -- rendered as a blank screen with no explanation. A blank
 * screen is indistinguishable from a broken app.
 */

type State =
  | { kind: "checking" }
  | { kind: "warming"; health: HealthResponse }
  | { kind: "ready" }
  | { kind: "unreachable"; message: string };

const POLL_WHILE_WARMING_MS = 2000;
const POLL_WHILE_DOWN_MS = 4000;

export function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "checking" });
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const health = await api.health();
        if (cancelled) return;
        if (health.ready) {
          setState({ kind: "ready" });
          return; // stop polling; nothing left to report
        }
        setState({ kind: "warming", health });
        timer = setTimeout(poll, POLL_WHILE_WARMING_MS);
      } catch (err) {
        if (cancelled) return;
        const message =
          err instanceof ApiError ? err.userMessage : "Backend unreachable.";
        setState({ kind: "unreachable", message });
        timer = setTimeout(poll, POLL_WHILE_DOWN_MS);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (state.kind === "ready" || dismissed) return null;

  if (state.kind === "checking") {
    return (
      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-rule bg-surface px-4 text-11 text-ink/60">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-steel" />
        Connecting to {API_URL}…
      </div>
    );
  }

  if (state.kind === "warming") {
    const { completed, total, percent, current, error } = state.health.warming;
    return (
      <div
        className="flex h-7 shrink-0 items-center gap-3 border-b border-rule px-4 text-11"
        style={{ backgroundColor: "var(--amber-12)", color: "var(--amber)" }}
        role="status"
      >
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber" />
        <span>
          Backend starting — solving Pareto fronts{" "}
          <span className="tabular">
            {completed}/{total} ({percent}%)
          </span>
          {current ? ` · ${current}` : ""}
        </span>
        <span className="text-ink/50">
          The app works now; uncached scenarios just solve on demand.
        </span>
        {error && <span className="text-signal">warm error: {error}</span>}
      </div>
    );
  }

  return (
    <div
      className="flex shrink-0 items-start gap-3 border-b border-rule px-4 py-2 text-11"
      style={{ backgroundColor: "var(--signal-12)", color: "var(--signal)" }}
      role="alert"
    >
      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-signal" />
      <div className="flex-1">
        <p className="font-medium">Backend not reachable</p>
        <p className="mt-0.5 break-words text-ink/70">{state.message}</p>
        <p className="mt-1 text-ink/50">
          Checked <span className="tabular">{API_URL}</span> · set{" "}
          <span className="tabular">NEXT_PUBLIC_API_URL</span> in{" "}
          <span className="tabular">frontend/.env.local</span> if your backend runs
          elsewhere, then restart <span className="tabular">npm run dev</span> —
          NEXT_PUBLIC_ variables are read at build time, not at runtime.
        </p>
      </div>
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 text-ink/50 hover:text-ink"
        aria-label="Dismiss"
      >
        ×
      </button>
    </div>
  );
}
