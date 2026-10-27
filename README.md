# EV Vehicle Tracker: local Wi-Fi dashboard (v4.0)

The ESP32 tracker serves its own dashboard over 2.4 GHz Wi-Fi. You open it on a phone or laptop by typing the tracker's IP address into the browser. No cloud and no internet are needed; only the street-map tiles use the internet.

The dashboard has:

- Live location, engine, battery and immobiliser state.
- A loud **battery-removal warning**, made up of a warning band, a pop-up, an alarm tone and vibration.
- Ignition cut and allow, protected by a PIN and the 5 km/h speed guard.
- An alert log and Wi-Fi/SIM800L status.
- A "How it works" section that explains the components and the process.

```
firmware/EV_Tracker_v4_WebDash/EV_Tracker_v4_WebDash.ino   main firmware (this version)
firmware/EV_Tracker_v4_WebDash/web_index.h                 generated from web/index.html: do not hand-edit
web/index.html                                             the dashboard (also works standalone + demo)
tools/embed.py                                             index.html -> web_index.h (gzip)
tools/mock_api.py                                          fake tracker for testing the page without hardware
firmware/EV_Tracker_v3_SIM800L/                            previous SIM800L + Firebase firmware (unchanged)
firmware/SIM800L_AT_Test/                                  modem bring-up test (run before the main firmware)
docs/hardware/                                             schematic EVT-SCH-03 + prototype guide Rev 3.0
```

## Wiring summary (schematic EVT-SCH-03, frozen)

| ESP32 pin | Connects to | Notes |
|---|---|---|
| GPIO16 RX2 / GPIO17 TX2 | NEO-8M TX / RX | 9600 baud |
| GPIO26 RX1 / GPIO27 TX1 | SIM800L TXD / RXD (via 1k + 3k3 divider) | 57600 baud |
| GPIO4 | SIM800L RST | Open-drain, never driven HIGH |
| GPIO21 / GPIO22 | INA226 SDA / SCL | 0x40, powered from 3V3 |
| GPIO34 | AH3144 OUT or PC817 collector | 10k pull-up to 3V3 required |
| GPIO25 | Relay IN | COM–NC in the key line (fail-safe) |
| GPIO13 | BC547 base via 1k | Active 5 V buzzer |
| GPIO2 | On-board LED | Solid = Wi-Fi connected |

**Power:** the SIM800L is powered from the 18650 cell (VBAT_LI, 3.7–4.2 V), never from 5 V. The 18650 also backs up the +5 V rail, so the ESP32, buzzer, Wi-Fi and SIM800L keep working when the vehicle battery is removed.

## 1. Install

- Arduino IDE 2.x. Under Boards Manager, install **esp32 by Espressif Systems** and select board **ESP32 Dev Module**. The default partition scheme is fine; "Huge APP" also works.
- Library Manager:
  - **TinyGPSPlus** (Mikal Hart)
  - **INA226** (Rob Tillaart, 0.5.x+)
  - **TinyGSM** (Volodymyr Shymanskyy). Only needed when `USE_SIM800L` is `true`.
- WiFi, WebServer, ESPmDNS, Preferences and Wire come with the ESP32 core. v4 does not need Firebase or `CustomFirebaseFS.h`.

## 2. Edit the config block

At the top of `EV_Tracker_v4_WebDash.ino`:

| Setting | What to put |
|---|---|
| `WIFI_SSID`, `WIFI_PASSWORD` | Your **2.4 GHz** router or phone hotspot (the ESP32 cannot use 5 GHz) |
| `AP_SSID`, `AP_PASSWORD` | Backup hotspot, default `EV-Tracker` / `evtracker123` |
| `CONTROL_PIN` | PIN for every control button (default `1234`: change it) |
| `USE_SIM800L` | `false` if no modem is fitted |
| `OWNER_PHONE`, `SMS_ENABLED` | SMS target, e.g. `+9198xxxxxxxx` |
| `ENGINE_SENSE_MODE` | `0` Hall sensor, `1` PC817 ignition sense |
| `RELAY_ACTIVE_LOW` | `true` for most blue relay modules |
| Battery thresholds | 12 V bench defaults; see guide note 4 for 48 V |

## 3. Flash and find the IP

1. Upload with the 12 V supply off.
2. Open the Serial Monitor at 115200. After boot it prints:
   ```
   Open http://192.168.1.50 (or http://evtracker.local) on a phone/laptop on the same Wi-Fi
   Or join hotspot "EV-Tracker" (password evtracker123) and open http://192.168.4.1
   ```
   Type `s` at any time to print it again.
3. Use the dashboard in any of these ways:
   - **Same Wi-Fi:** put your phone or laptop on the same 2.4 GHz Wi-Fi and open `http://<IP>`. `http://evtracker.local` works on most laptops and iPhones.
   - **No router:** join the `EV-Tracker` hotspot and open `http://192.168.4.1`. The map switches to the offline track plot because there is no internet.
   - **App-like icon on the phone:** use the browser menu → **Add to Home screen**.
   - **Saved file on the laptop:** open `web/index.html` as a file and type the tracker's IP into "Tracker address". With no tracker it runs a **demo** (PIN `1234`) that includes "Simulate battery removal".

Press **Sound alerts on** once on each phone or laptop. Browsers block alarm sounds until you tap something.

## 4. Changing the page

Edit `web/index.html`, then run:

```
python tools/embed.py      # regenerates firmware/EV_Tracker_v4_WebDash/web_index.h
```

Re-upload the firmware afterwards. To try the page without hardware:

```
python tools/mock_api.py   # http://localhost:8080, PIN 1234
# simulate events from another terminal:
curl -X POST "localhost:8080/mock/battery?present=0"   # pull the battery
curl -X POST "localhost:8080/mock/speed?kmph=25"       # drive (speed guard)
curl -X POST "localhost:8080/mock/offline?s=10"        # tracker stops answering
```

## API

| Request | Result |
|---|---|
| `GET /api/status` | All live values plus the last 20 alerts (JSON) |
| `POST /api/cmd?c=OFF\|ON\|CLEAR\|BEEP\|SMS&pin=1234` | `{"ok":true,"msg":"Ignition cut"}`; a wrong PIN returns HTTP 403 |

The API sends CORS headers, so a saved copy of the page can talk to the tracker.

## Behaviour notes

- **Battery removed:** voltage below 6 V for 3 readings (0.3 s) sounds the buzzer, logs an alert and queues an SMS (at most one every 5 min). The alarm stays on after the battery is reconnected until someone presses **Silence alarm**.
- **Ignition cut:** above 5 km/h the cut is queued and engages when the vehicle stops. The cut is saved in flash. After a reboot the relay starts released (vehicle can run), then re-cuts after 10 s once the speed check passes.
- **Alert log:** saved in flash (last 20) and survives reboots. Times come from GPS (shown in IST). Before the first GPS fix, times are estimated from uptime.
- Serial test menu: `h s r b c g i m t p`, as in the guide (`u` and `k` were Firebase-only and are gone).

## Troubleshooting

| Problem | Fix |
|---|---|
| Page won't open | Phone on a different network, or on 5 GHz; or the router has "AP/client isolation" turned on. Try the `EV-Tracker` hotspot. |
| `evtracker.local` fails (common on Android) | Use the IP from the Serial Monitor or your router's device list |
| Hotspot drops for a moment when the tracker joins Wi-Fi | Normal: the hotspot moves to the router's channel |
| Map is blank or shows a grid | No internet. The offline track plot is shown instead; position and the Google Maps link still work |
| No alarm sound | Tap **Sound alerts on**; check that the phone isn't on silent |
| "Wrong PIN" | Must match `CONTROL_PIN` in the firmware |
| Relay works backwards | Flip `RELAY_ACTIVE_LOW` |
| Battery tile says "INA226 not found" | Check SDA 21 / SCL 22 / 3V3; type `i` in the Serial Monitor for an I2C scan |
| "SIM800L has no 2G network" | Airtel/Vi/BSNL SIM (not Jio), antenna fitted, run `SIM800L_AT_Test` first |
| Page slow while an SMS is sending | Expected: the ESP32 waits for the SIM800L for a few seconds |

Bench tests T1–T18 in `docs/hardware/EV_Tracker_SIM800L_Prototype_Guide.pdf` still apply. Check T12 (remote command), T14 (battery removal) and the speed guard from the dashboard.
