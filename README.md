# GreenRoute-AI

## Troubleshooting: the web app shows no data

Run the diagnostic first:

```bash
python scripts/doctor.py
```

The three real causes, in order of likelihood:

**1. The backend is still starting.** On boot it solves 55 Pareto fronts (baseline
+ 54 scenarios). This used to run *before* uvicorn bound the port, so every request
got connection-refused and the UI looked broken. Warming now happens on a background
thread: the API answers immediately, and `GET /health` reports progress
(`ready`, `warming.percent`). Requests for a not-yet-cached scenario solve on demand
— slower, not broken. The amber banner at the top of the app shows this live.

**2. `NEXT_PUBLIC_API_URL` is stale.** `NEXT_PUBLIC_*` variables are inlined at
**build** time, not read at runtime. After editing `frontend/.env.local` you must
restart `npm run dev`, or the old value stays compiled into the bundle.
`frontend/.env.local` is gitignored, so it is absent after a fresh clone or unzip —
`api.ts` falls back to `http://localhost:8000`, which is correct for local dev.

**3. Port mismatch.** If port 3000 was taken, Next.js silently starts on 3001.
CORS is currently `allow_origins=["*"]`, so that is fine, but confirm the backend is
on 8000 and nothing else has claimed it.

Failures are now visible in the UI rather than only in `console.error`: a red banner
names the failing endpoint, the URL that was tried, and the fix.
