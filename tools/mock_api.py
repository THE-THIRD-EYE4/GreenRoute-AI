#!/usr/bin/env python3
"""Mock EV Tracker for testing the dashboard without hardware.

    python tools/mock_api.py            # http://localhost:8080
    python tools/mock_api.py --port 9000

Serves web/index.html at / and answers the same API as the firmware:
    GET  /api/status
    POST /api/cmd?c=OFF|ON|CLEAR|BEEP|SMS&pin=1234
Test-only helpers (not in the firmware):
    POST /mock/battery?present=0|1      simulate pulling / reconnecting the battery
    POST /mock/speed?kmph=25            simulate driving (speed guard)
    POST /mock/offline?s=10             stop answering for s seconds
Development tool only. Standard library only.
"""
import argparse
import json
import math
import pathlib
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"
PIN = "1234"
START = time.time()
lock = threading.Lock()

S = {
    "present": True, "alarm": False, "immob": False, "pending": False,
    "speed": 0.0, "low": 0, "ok": 0, "alerts": [], "offline_until": 0.0,
}


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def uptime():
    return int(time.time() - START)


def pos():
    t = time.time() - START
    return 12.9249 + 0.0008 * math.sin(t / 60), 80.1000 + 0.0008 * math.cos(t / 60)


def volts():
    return round(random.uniform(0.1, 0.3) if not S["present"] else 12.45 + random.uniform(-0.04, 0.04), 2)


def log(kind):
    lat, lng = pos()
    S["alerts"].insert(0, {"up": uptime(), "boot": 1, "utc": utc(), "type": kind,
                           "v": volts(), "lat": round(lat, 6), "lng": round(lng, 6)})
    del S["alerts"][20:]


def status():
    v = volts()
    if S["pending"] and S["speed"] <= 5:
        S["pending"] = False
        S["immob"] = True
        log("IGNITION_CUT")
    lat, lng = pos()
    return {
        "id": "EV_TRACKER_01", "fw": "4.0", "uptime": uptime(), "boot": 1,
        "gps": {"fix": True, "lat": round(lat, 6), "lng": round(lng, 6), "speed": S["speed"],
                "sats": 8, "hdop": 1.1, "utc": utc()},
        "engine": {"on": S["speed"] > 0 and not S["immob"], "pps": round(S["speed"] * 0.9, 1), "mode": "hall"},
        "battery": {"ok": True, "v": v, "mA": 0 if not S["present"] else random.randint(175, 190),
                    "pct": 0 if not S["present"] else max(0, min(100, int((v - 10.5) * 100 / 2.2))),
                    "present": S["present"], "alarm": S["alarm"], "minV": 6.0},
        "relay": {"immobilised": S["immob"], "pending": S["pending"], "maxKmph": 5.0},
        "net": {"ip": "127.0.0.1", "ssid": "MockWiFi", "sta": True, "rssi": -61, "ap": "EV-Tracker",
                "apIp": "192.168.4.1", "mdns": "evtracker.local"},
        "gsm": {"enabled": True, "ok": True, "reg": True, "csq": 17, "op": "airtel", "sms": True,
                "smsPending": not S["present"]},
        "alerts": S["alerts"],
    }


def command(c):
    if c == "OFF":
        if S["immob"]:
            return 200, True, "Ignition is already cut"
        if S["speed"] > 5:
            if not S["pending"]:
                S["pending"] = True
                log("CUT_QUEUED")
            return 200, True, f"Vehicle moving at {S['speed']:.0f} km/h: cut queued until it stops"
        S["immob"] = True
        log("IGNITION_CUT")
        return 200, True, "Ignition cut"
    if c == "ON":
        changed = S["immob"] or S["pending"]
        S["immob"] = S["pending"] = False
        if changed:
            log("IGNITION_ALLOWED")
        return 200, True, "Engine allowed" if changed else "Engine was already allowed"
    if c == "CLEAR":
        was = S["alarm"]
        S["alarm"] = False
        if was:
            log("ALARM_CLEARED")
        return 200, True, "Alarm silenced" if was else "No alarm to silence"
    if c == "BEEP":
        return 200, True, "Buzzer test: 1 second beep"
    if c == "SMS":
        return 200, True, "Test SMS sent to +91XXXXXXXXXX (mock)"
    return 409, False, "Unknown command"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def offline(self):
        return time.time() < S["offline_until"]

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.offline():
            time.sleep(3)
            return
        if u.path == "/api/status":
            with lock:
                self.reply(200, status())
            return
        self.reply(404, {"ok": False, "msg": "Not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        with lock:
            if u.path == "/api/cmd":
                if q.get("pin") != PIN:
                    return self.reply(403, {"ok": False, "msg": "Wrong PIN"})
                code, ok, msg = command(q.get("c", "").upper())
                return self.reply(code, {"ok": ok, "msg": msg})
            if u.path == "/mock/battery":
                present = q.get("present", "0") == "1"
                if S["present"] and not present:
                    S["present"], S["alarm"] = False, True
                    log("BATTERY_REMOVED")
                elif not S["present"] and present:
                    S["present"] = True
                    log("BATTERY_RESTORED")
                return self.reply(200, {"ok": True})
            if u.path == "/mock/speed":
                S["speed"] = float(q.get("kmph", "0"))
                return self.reply(200, {"ok": True})
            if u.path == "/mock/offline":
                S["offline_until"] = time.time() + float(q.get("s", "10"))
                return self.reply(200, {"ok": True})
        self.reply(404, {"ok": False, "msg": "Not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    port = ap.parse_args().port
    log("TRACKER_STARTED")
    print(f"Mock tracker on http://localhost:{port}  (PIN {PIN})")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
