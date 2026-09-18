#!/usr/bin/env python3
"""Diagnose "the web app isn't fetching the API".

Run from the repo root:  python scripts/doctor.py

Checks the things that actually break, in the order they break, and prints the
fix for each. No dependencies beyond the standard library.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("API_URL", "http://localhost:8000")
ROOT = Path(__file__).resolve().parent.parent

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
problems: list[str] = []


def line(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}")


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def main() -> int:
    print(f"GreenRoute-AI doctor — checking {API}\n")

    # 1. Is anything listening?
    host_port = API.split("://", 1)[-1]
    host = host_port.split(":")[0]
    port = int(host_port.split(":")[1].split("/")[0]) if ":" in host_port else 80

    if port_open(host, port):
        line(OK, f"something is listening on {host}:{port}")
    else:
        line(FAIL, f"nothing is listening on {host}:{port}")
        problems.append(
            "Start the backend:\n"
            "    cd backend && source .venv/bin/activate\n"
            "    uvicorn dispatch.api:app --reload --port 8000"
        )
        print_summary()
        return 1

    # 2. Does /health answer, and is it warm?
    try:
        with urllib.request.urlopen(f"{API}/health", timeout=10) as r:
            health = json.loads(r.read())
        line(OK, "/health responded")
        warming = health.get("warming", {})
        if health.get("ready"):
            line(OK, "solver caches are warm")
        else:
            line(
                WARN,
                f"still warming: {warming.get('completed')}/{warming.get('total')} "
                f"({warming.get('percent')}%) — current {warming.get('current')}",
            )
            print("        The app works during warm-up; uncached scenarios solve on demand.")
        if warming.get("error"):
            line(FAIL, f"warm-up error: {warming['error']}")
            problems.append("A Pareto warm-up raised an exception — see the uvicorn log.")
    except urllib.error.HTTPError as e:
        line(FAIL, f"/health returned HTTP {e.code}")
        problems.append("The server is up but erroring. Check the uvicorn traceback.")
    except Exception as e:
        line(FAIL, f"/health unreachable: {e}")
        problems.append("Port is open but /health failed — is another app on this port?")

    # 3. CORS preflight from the dev origin
    try:
        req = urllib.request.Request(
            f"{API}/network/nodes",
            method="OPTIONS",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            allow = r.headers.get("access-control-allow-origin")
        if allow in ("*", "http://localhost:3000"):
            line(OK, f"CORS allows localhost:3000 (allow-origin: {allow})")
        else:
            line(FAIL, f"CORS allow-origin is {allow!r}")
            problems.append(
                "Widen allow_origins in backend/dispatch/api.py, or run the "
                "frontend on the allowed port."
            )
    except Exception as e:
        line(WARN, f"CORS preflight inconclusive: {e}")

    # 4. A real data endpoint
    try:
        with urllib.request.urlopen(f"{API}/network/nodes", timeout=30) as r:
            nodes = json.loads(r.read())
        line(OK, f"/network/nodes returned {len(nodes)} nodes")
    except Exception as e:
        line(FAIL, f"/network/nodes failed: {e}")
        problems.append("Data endpoint failing — check data/ CSVs are present.")

    # 5. Frontend env
    env_local = ROOT / "frontend" / ".env.local"
    if env_local.exists():
        text = env_local.read_text()
        if "NEXT_PUBLIC_API_URL" in text:
            configured = [
                l.split("=", 1)[1].strip()
                for l in text.splitlines()
                if l.strip().startswith("NEXT_PUBLIC_API_URL=")
            ]
            line(OK, f"frontend/.env.local sets NEXT_PUBLIC_API_URL={configured[0] if configured else '?'}")
            if configured and configured[0].rstrip("/") != API.rstrip("/"):
                line(WARN, f"that differs from the URL checked here ({API})")
        else:
            line(WARN, "frontend/.env.local exists but has no NEXT_PUBLIC_API_URL")
    else:
        line(
            WARN,
            "frontend/.env.local missing (gitignored, so absent after clone/unzip)",
        )
        print("        api.ts falls back to http://localhost:8000, which is usually fine.")
        print("        To set it explicitly: cp frontend/.env.example frontend/.env.local")

    # 6. Is the frontend itself up?
    if port_open("localhost", 3000, timeout=1.0):
        line(OK, "something is listening on localhost:3000 (frontend)")
    else:
        line(WARN, "nothing on localhost:3000 — start it with: cd frontend && npm run dev")

    print_summary()
    return 1 if problems else 0


def print_summary() -> None:
    print()
    if not problems:
        print("No blocking problems found.")
        print()
        print("If the browser still shows no data:")
        print("  1. Open DevTools > Network and look at the failing request.")
        print("  2. A 'Failed to fetch' with no status = backend down or wrong port.")
        print("  3. Remember NEXT_PUBLIC_* is compiled in at build time —")
        print("     restart `npm run dev` after changing .env.local.")
        return
    print("Problems found:\n")
    for i, p in enumerate(problems, 1):
        print(f"  {i}. {p}\n")


if __name__ == "__main__":
    sys.exit(main())
