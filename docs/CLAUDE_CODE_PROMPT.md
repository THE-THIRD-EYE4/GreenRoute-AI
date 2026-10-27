# Claude Code prompt: EV Tracker local website (phase 1) and app (phase 2)

Paste everything below the line into Claude Code, opened at the root of this repository.

---

You are working on my **EV Vehicle Tracker prototype** (ESP32 + SIM800L 2G + NEO-8M GPS + INA226 + relay + buzzer). The project files are in this repo:

- `firmware/EV_Tracker_v3_SIM800L/EV_Tracker_v3_SIM800L.ino`: main firmware v3.0. **Read it completely before you change anything.**
- `firmware/SIM800L_AT_Test/SIM800L_AT_Test.ino`: modem bring-up test. Do not change it.
- `firmware/library_patch/CustomFirebaseFS.h`: one line, `#define TINY_GSM_MODEM_SIM800`, copied into the Firebase library `src/` folder.
- `docs/hardware/EV_Tracker_SIM800L_Prototype_Guide.pdf`: guide Rev 3.0 (pin map, BOM, build order, Firebase setup, bench tests T1 to T18, troubleshooting).
- `docs/hardware/EV_Tracker_SIM800L_Schematic_EVT-SCH-03.pdf`: schematic EVT-SCH-03 Rev 3.0.

I want **two deliverables, built in this order**:

1. **PHASE 1 (do this now): a real, working website served by the ESP32 itself.** The ESP32 joins my 2.4 GHz Wi-Fi router. It prints its IP address on the Serial Monitor. I type that IP (for example `http://192.168.1.45`) into a browser on my phone or laptop on the same Wi-Fi and get a live dashboard, the same way we did before. It must work with no internet, no Firebase, and no cloud.
2. **PHASE 2 (only after phase 1 works and I confirm it): a mobile and laptop app** built from the same UI. Do not start phase 2 until I say so.

Use real data only. Never show fake or demo values when the device is connected. If a sensor is missing, the UI must say so clearly, for example "INA226 not found" or "GPS: no fix, 3 sats".

---

## 1. Hardware facts (frozen; match the schematic exactly)

**Pin map (do NOT change):**

| ESP32 pin | Net | Connects to | Notes |
|---|---|---|---|
| GPIO16 (RX2) | GPS_TX | NEO-8M TX | UART2, 9600 baud NMEA |
| GPIO17 (TX2) | GPS_RX | NEO-8M RX | |
| GPIO26 (RX1) | SIM_TXD | SIM800L TXD | UART1, 57600 baud (9600 fallback) |
| GPIO27 (TX1) | SIM_RXD_3V3 | R5 1k to SIM RXD, R6 3k3 to GND | ~2.5 V level |
| GPIO4 | SIM_RST | SIM800L RST | Open-drain: pull LOW or release. Never drive HIGH. |
| GPIO21 / GPIO22 | I2C SDA / SCL | INA226 at 0x40 | INA226 powered from 3V3 |
| GPIO19 | INA_ALERT | INA226 ALERT | Optional, unused |
| GPIO34 | ENGINE_SENSE | AH3144 OUT (Option A) or PC817 collector (Option B) | Input only; external R1 10k pull-up to 3V3 |
| GPIO25 | RELAY_IN | K1 relay module | `RELAY_ACTIVE_LOW` |
| GPIO13 | BUZZER_DRV | R3 1k to BC547 base, R4 10k pull-down | Active 5 V buzzer |
| GPIO2 | on-board LED | | Solid = connected, blinking = not connected |

**Power and backup chain (section A of the schematic):**
BT1 vehicle battery (12 V bench, 48 V see note 4) → F1 3 A fuse → D1 SS54 reverse-polarity diode → D4 SMBJ15A TVS → INA226 shunt R100 (VBUS = VIN-) → U1 LM2596HV buck set to 5.4 V → D2 SS34 → +5 V rail (ESP32 VIN, relay K1, buzzer BZ1).
The backup path is U7 TP4056 + DW01 → 18650 cell BT2 (VBAT_LI) → U8 MT3608 boost at 5.0 V → D3 SS34 → +5 V rail (diode-OR).
**The SIM800L is powered directly from VBAT_LI (3.4 to 4.4 V, 2 A bursts) with C5 1000 µF and C6 100 nF at its pins. It is never powered from 5 V.**

**What this means for battery removal:** when BT1 is removed, the ESP32, the buzzer, Wi-Fi and the SIM800L all keep running on BT2. The website must therefore keep working and must show the alarm immediately.

**Engine sense:** `ENGINE_SENSE_MODE 0` uses the AH3144 Hall sensor (pulse counting, 2 s timeout, pulses/s shown). `ENGINE_SENSE_MODE 1` uses ignition sense through the PC817 (LOW = ON).

**Immobiliser (fail-safe):** the key line goes through relay COM to NC. De-energised (boot, power loss, fault) means NC is closed and the vehicle runs. Energised means NC opens and the ignition is cut. The relay is energised only when speed ≤ `IMMOBILISE_MAX_KMPH` (5 km/h). Otherwise the command is queued (`immobilisePending`) until the vehicle slows down.

**SIM800L:** 2G only (Airtel, Vi, BSNL; **Jio will not work**). APN is `airtelgprs.com`, `www` for Vi, `bsnlnet` for BSNL. SMS goes to `OWNER_PHONE`, at most one alert SMS every 5 minutes. CSQ 10 to 31 is good; 99 means no signal. NET LED: blinking about every 1 s = searching, about every 3 s = registered, fast = GPRS.

**Battery thresholds (12 V bench defaults):** `BATT_PRESENT_MIN_V` 6.0, `BATT_EMPTY_V` 10.5, `BATT_FULL_V` 12.7, `BATT_DIVIDER_RATIO` 1.0, debounce 3 samples taken every 100 ms. Battery percent is a linear estimate.

**Toolchain:** Arduino IDE 2.x, **esp32 core 2.0.17** (the Firebase library breaks on 3.x), board "ESP32 Dev Module", partition scheme **"Huge APP (3MB No OTA/1MB SPIFFS)"**, Serial at 115200. Libraries: TinyGPSPlus, TinyGSM, Firebase Arduino Client (Mobizt 4.4.x), INA226 (Rob Tillaart 0.5.x+).

---

## 2. PHASE 1: firmware changes (v3.1, local web)

Create `firmware/EV_Tracker_v3_1_Web/EV_Tracker_v3_1_Web.ino`, plus any `.h` files it needs, as a copy of v3.0 with these additions. **Keep v3.0 untouched.**

1. **Wi-Fi is independent of `NET_MODE`.** Add `LOCAL_WEB_ENABLED true`. Whenever it is set, the ESP32 joins `WIFI_SSID` / `WIFI_PASSWORD` (2.4 GHz) in STA mode, even when `NET_MODE == 1` and Firebase data goes over GPRS. Use `WiFi.setSleep(false)` for responsiveness.
   - Print a clear banner on Serial: `[OK] Dashboard: http://<ip>/` (also `http://evtracker.local/` through mDNS). Reprint it in the `s` status output and after every reconnect.
   - **Fallback:** if STA fails for 30 s at boot, start a SoftAP `EV-Tracker-XXXX` (password set in config) at `192.168.4.1` so I can still open the dashboard, and keep retrying STA in the background.
   - Add `FIREBASE_ENABLED` (default `false` for phase 1) so the device works with no Firebase project at all. When it is `true`, all existing Firebase/GPRS behaviour stays exactly as it is.
2. **Async web server.** Use ESPAsyncWebServer + AsyncTCP, picking versions that compile on esp32 core 2.0.17, and pin those versions in the README. Modem calls block for seconds (`waitForNetwork` 60 s, SMS, GPRS uploads), so the web server must keep answering during them. Never call modem functions from a web handler. Handlers only set flags or queue commands, and `loop()` executes them, so there is a single place that touches hardware.
3. **Serve the website from flash with no filesystem upload step.** Use a build script (`tools/build_web.py`, Python standard library only) that minifies and gzips `web/` into `firmware/.../web_assets.h` (PROGMEM byte arrays, `Content-Encoding: gzip`, cache headers with an ETag). One USB upload must flash both firmware and website. Keep the total gzipped size under ~250 KB.
4. **Live push:** a WebSocket at `/ws` (or SSE at `/events`) sends the full status JSON every 1 s. It also sends an immediate `alert` event the moment battery removal is detected; do not wait for the 1 s tick. Support up to 4 clients and clean up stale clients.
5. **REST API** (JSON; same field names as the Firebase tree so phase 2 can reuse them):
   - `GET /api/status`: `gps{fix,sats,lat,lng,speedKmph,ageMs,hdop}`, `engine{status,pulsesPerSec,mode,immobilised,immobilisePending}`, `battery{voltage,current_mA,percent,present,alarm}`, `device{net,csq,operator,modemOk,gprs,inaOk,wifiRssi,ip,uptimeS,freeHeap,fw:"3.1"}`, `sms{enabled,lastSentAgoS,pending}`.
   - `GET /api/alerts`: last 50 events (BATTERY_REMOVED, BATTERY_RESTORED, IMMOBILISED, RELEASED, IMMOBILISE_QUEUED, ALARM_CLEARED, MODEM_RESET, GPS_FIX_LOST/GAINED, WIFI_RECONNECT). Each event has type, voltage, lat, lng and time. Keep them in a RAM ring buffer and persist them to NVS (`Preferences`) so they survive reboots. Use GPS UTC time when there is a fix; otherwise uptime.
   - `POST /api/command` with `{cmd:"OFF"|"ON"|"CLEAR"}` goes through **the same code path** as `pollCommand()`, including the speed guard. Refactor the logic into `applyCommand(String)`. **Fix the conflict:** a stale Firebase `engineControl` value must not undo a local command. When `FIREBASE_ENABLED`, write the local command back to `commands/engineControl` from the loop.
   - `POST /api/action` with `{a:"beep"|"testSms"|"upload"|"i2cScan"|"modemInfo"|"pollNow"}` mirrors the serial menu (b, t, u, i, m, k). Results come back asynchronously over the WebSocket. **Never turn the blocking `delay(1000)` beep into a web action;** make it non-blocking.
   - `GET/POST /api/settings`: runtime-editable values stored in NVS: owner phone, SMS enabled, dashboard PIN. Thresholds, `ENGINE_SENSE_MODE` and `RELAY_ACTIVE_LOW` are **read-only** and only displayed, because they must match the hardware.
6. **Security (LAN only, still required):** every `POST` needs a 4 to 6 digit dashboard PIN (stored in NVS, default set in config, session token kept in memory). Reading status needs no PIN. Immobilise needs the PIN plus a confirmation in the UI. Add basic rate limiting on wrong PINs.
7. **Do not break anything that exists:** the serial test menu, SMS logic, modem watchdog and hard reset, GPS feeding (`serviceGPS()` during slow calls), the 100 ms battery check, relay off at boot, and the LED behaviour. Battery removal must still trigger the buzzer within ~0.3 s, independent of the website.

---

## 3. PHASE 1: the website (`web/`)

Plain HTML, CSS and vanilla JS: **no framework, no CDN, no external fonts.** It must load entirely from the ESP32 with no internet. It is one responsive single-page app that works well on a phone (360 px wide, thumb-reachable controls) and on a laptop (multi-column layout).

### Pages and sections (driven by the components and the process)

1. **Overview / Dashboard:** connection state (Live / Reconnecting / Offline, with last update age), a big status row (Engine ON/OFF, Vehicle RUN/IMMOBILISED/QUEUED, Battery %, GPS fix), speed, and the last known position.
2. **Battery-removal alarm (most important feature):** when `battery.present` becomes false, show a full-width, unmissable alert banner: "Vehicle battery removed", with the time, last voltage and a location link. It should play a repeating alert tone through the Web Audio API (after the first user interaction, because browsers require that) and vibrate on phones (`navigator.vibrate`). Show a browser notification only where the browser allows it on plain HTTP, and degrade gracefully otherwise. Also show "Running on backup cell (BT2)", the SMS status (queued / sent / failed, plus the 5-minute rate-limit timer) and a **Silence alarm (CLEAR)** button. When the battery comes back, show "Battery reconnected; alarm stays on until cleared", exactly as the firmware behaves.
3. **Location:** latitude/longitude, satellites, HDOP, speed, fix age, an "Open in Google Maps" link, and a copy-coordinates button. Show a map **only if the phone or laptop has internet** (lazy-load Leaflet + OSM tiles from the network and hide the map when that fails). The page must never break offline. Show a simple trail of the last ~100 points kept in browser memory.
4. **Engine:** ON/OFF, pulses/s (Hall mode) or ignition (PC817 mode), the active sense mode, and a small 60 s sparkline.
5. **Immobiliser control:** Cut ignition (OFF) and Release (ON), each with a PIN and a confirm dialog. Explain the speed guard ("Queued: vehicle is moving at 23 km/h; will cut below 5 km/h"). Show that the relay is fail-safe (NC closed when unpowered).
6. **Battery and power:** voltage, current (mA), percent (labelled as a linear estimate), present/removed, a 10-minute voltage chart, and the thresholds. Include a simple power-path diagram (inline SVG): BT1 → fuse → INA226 → buck 5.4 V → +5 V rail, and TP4056 → 18650 → boost → +5 V, with the active path highlighted.
7. **GSM / SIM800L:** modem OK, operator, CSQ shown as signal bars with a 10 to 31 good range, network registered, GPRS up/down, a "Send test SMS" button, and SMS enabled/owner phone. Include a 2G-only note (no Jio).
8. **Alerts log:** a filterable list from `/api/alerts` with export to CSV.
9. **Hardware health:** one card per component (U0 ESP32, U2 INA226, U3 NEO-8M, U4 SIM800L, U5/U6 engine sense, K1 relay, BZ1 buzzer, BT2 backup), each with live status, the pin it uses and a one-line purpose. Include an I2C scan button.
10. **Bench test checklist T1 to T18** from the guide (test, how, pass criteria). For each test I can mark Pass/Fail/Note, stored in browser localStorage, with export. Where the device can verify a test live, show it, for example T8 INA226 found, T7 GPS fix, T6 upload OK, T14 alert received.
11. **Troubleshooting:** the symptom → cause → fix table from the guide.
12. **Settings:** dashboard PIN, owner phone, SMS on/off, the read-only firmware config, device info (firmware version, IP, RSSI, uptime, free heap), and theme (light/dark/auto).

### Visual design (important)

It must look like a well-made instrument panel, **not an AI-generated template.** Use a three-colour palette with deliberate proportions:

- **Beige, ~60% (surfaces):** page `#F2EBDD`, cards `#FAF6EE`, borders `#DCCFB6`.
- **Prussian blue, ~30% (structure, text, headers, navigation):** `#003153`, with a darker `#00243D` and a muted `#2D5573`. Body text is `#1D2A33`.
- **Dark leaf green, ~10% (positive state and primary action only):** `#2F5233`, hover `#244027`, tint `#E3EBDF`.
- Status colours, muted and never neon: alert brick `#9E2B25` on `#F6E3DF`, warning ochre `#A86B12` on `#F5EAD3`.
- **Dark mode:** background `#0E1E2B`, cards `#16293A`, text `#EDE4D3` (beige), green lifted to `#6E9A6F`.

Rules:

- **No** neon, glow, glassmorphism, purple gradients, emoji icons or "AI sparkle" visuals.
- Use flat surfaces, 1 px borders, small radii (6 to 8 px) and restrained shadows.
- Icons are simple inline SVG line icons.
- Use the system font stack, with tabular numbers (`font-variant-numeric: tabular-nums`) for every reading and a monospace font for coordinates and IDs.
- Keep a clear typographic hierarchy: big numbers for key readings, small caps labels.
- The layout uses a top bar with the vehicle ID (`EV_TRACKER_01`) and connection pill. On a phone, use a bottom tab bar (Overview, Location, Control, Alerts, More). On a laptop, use a left sidebar.
- Meet WCAG AA contrast and visible focus states. Respect `prefers-reduced-motion`.

### Behaviour

- WebSocket with auto-reconnect (backoff) and a REST polling fallback every 2 s.
- Show "stale" when the last update is older than 5 s.
- Every button shows pending, success and failure states.
- Commands are disabled while offline.

---

## 4. Phase 1 deliverables and verification

- `firmware/EV_Tracker_v3_1_Web/` (compiles on esp32 core 2.0.17 with the listed libraries), `web/`, `tools/build_web.py`.
- `tools/mock_device.py`: a Python standard-library mock of the REST API and WebSocket (or SSE) that serves `web/` and simulates the sensors, including a battery-removal event, so the UI can be tested without hardware. The mock is **only for development**; the real UI never invents data.
- **Verify before you finish:** run the build script, compile the firmware with `arduino-cli` if it can be installed (report honestly if it can't), run the mock, and use Playwright (already installed) to screenshot the dashboard at 375 px and 1366 px in light and dark mode, plus the battery-alarm state. Show me the screenshots.
- `README.md` with these sections:
  - Wiring and pin summary
  - Library and board versions
  - The CustomFirebaseFS.h step
  - Config fields to edit
  - How to flash
  - How to find the IP (Serial Monitor, router device list, `evtracker.local`, SoftAP fallback)
  - Why the phone must be on the same 2.4 GHz network
  - Bench test steps to confirm the website: T14 battery removal, T12 remote command, the speed guard
- Commit in small logical commits and push to the current branch.

## 5. PHASE 2 (wait for my go-ahead): mobile and laptop app

Reuse the same `web/` UI and API.

- **Laptop:** make the site an installable PWA (manifest, icons in the palette, service worker caching the app shell). Note that PWA install needs HTTPS or localhost. Because the ESP32 serves plain HTTP on the LAN, also provide a small desktop wrapper (Tauri or Electron) that asks for the device IP, remembers it and discovers `evtracker.local`.
- **Mobile:** an Android app with Capacitor wrapping the same UI. It should have an IP entry/scan screen, remember the device, allow cleartext HTTP to the local subnet, and use native notifications plus a foreground service that keeps the battery-removal alarm sounding while the app is in the background. Optionally add a Firebase remote mode (owner login with the existing RTDB tree and rules) for when the phone is away from the Wi-Fi and the tracker is on GPRS.
- Build the APK. Document iOS as possible, but not built.

## Constraints

- Do not change the pin map, relay fail-safe logic, speed guard or SIM800L power and reset rules.
- Do not put Wi-Fi or Firebase secrets in the repo. Use a `secrets.h` (git-ignored) with a `secrets.example.h`.
- Ask me before adding any paid service or anything that needs a public server.
- If a requirement conflicts with the hardware (for example, an HTTPS-only browser API), tell me plainly and choose the safe fallback.
