/*
  =====================================================================
  EV VEHICLE TRACKER - Prototype Firmware v3.0  (SIM800L / 2G GPRS)
  GPS Tracking | Engine ON/OFF Monitor | Battery Removal Alert | Immobiliser
  MCU     : ESP32 DevKit V1 (Board: "ESP32 Dev Module")
  Network : SIM800L GPRS (default) or Wi-Fi for bench tests (NET_MODE)
  Alerts  : Firebase Realtime Database + SMS to owner (SIM800L)
  =====================================================================

  PIN MAP (matches schematic EVT-SCH-03)
    GPS  UART2   : GPIO16 (RX2) <- GPS TX,  GPIO17 (TX2) -> GPS RX
    SIM800L UART1: GPIO26 (RX1) <- SIM TXD, GPIO27 (TX1) -> 1k/3k3 divider -> SIM RXD
    SIM800L RST  : GPIO4  (open-drain: driven LOW to reset, otherwise input)
    Relay        : GPIO25
    Engine sense : GPIO34 (input only, EXTERNAL 10k pull-up to 3.3V)
    I2C INA226   : GPIO21 SDA, GPIO22 SCL  (INA226 powered from 3.3V)
    Buzzer       : GPIO13 -> 1k -> BC547 base
    Status LED   : GPIO2  (on-board)

  SIM800L FACTS
    - 2G only (GSM/GPRS). Works with Airtel, Vi, BSNL 2G. NOT Jio.
    - Supply 3.4-4.4 V with 2 A bursts. Powered straight from the 18650 cell.
    - Micro-SIM. Disable the SIM PIN in a phone first.

  LIBRARIES (Arduino Library Manager)
    1. TinyGPSPlus                  by Mikal Hart
    2. TinyGSM                      by Volodymyr Shymanskyy
    3. Firebase Arduino Client Library for ESP8266 and ESP32  by Mobizt (v4.4.x)
    4. INA226                       by Rob Tillaart (v0.5.x or newer)

  !! ONE-TIME LIBRARY STEP (required for GPRS) !!
    Create a file named  CustomFirebaseFS.h  inside the Firebase library's
    src folder (Documents/Arduino/libraries/Firebase_Arduino_Client_Library_for_ESP8266_and_ESP32/src/)
    containing exactly one line:
        #define TINY_GSM_MODEM_SIM800
    Without it the Firebase library compiles without GSM support.
=====================================================================
*/

// ---------------- TinyGSM must be configured before any include ----------------
#define TINY_GSM_MODEM_SIM800
#define TINY_GSM_RX_BUFFER 1024

#include <WiFi.h>
#include <Wire.h>
#include <TinyGPSPlus.h>
#include <TinyGsmClient.h>
#include <Firebase_ESP_Client.h>
#include <addons/TokenHelper.h>
#include <addons/RTDBHelper.h>
#include <INA226.h>

// =====================================================================
// USER CONFIG  -- edit these before uploading
// =====================================================================
// Data path: 1 = SIM800L GPRS (on the vehicle), 0 = Wi-Fi (bench tests)
#define NET_MODE             1

#define GPRS_APN         "airtelgprs.com"   // Airtel. Vi: "www"  BSNL: "bsnlnet"
#define GPRS_USER        ""
#define GPRS_PASS        ""
#define SIM_PIN          ""                  // leave empty: disable PIN in a phone

#define OWNER_PHONE      "+91XXXXXXXXXX"     // SMS alerts go here
#define SMS_ENABLED          true

#define WIFI_SSID        "YOUR_WIFI_SSID"    // only used when NET_MODE 0
#define WIFI_PASSWORD    "YOUR_WIFI_PASSWORD"

#define API_KEY          "YOUR_FIREBASE_WEB_API_KEY"
#define DATABASE_URL     "YOUR_PROJECT-default-rtdb.asia-southeast1.firebasedatabase.app"
#define USER_EMAIL       "tracker01@yourproject.com"   // Firebase Auth user for the DEVICE
#define USER_PASSWORD    "device_password"

#define VEHICLE_ID       "EV_TRACKER_01"

// Engine sense: 0 = Hall sensor pulses, 1 = ignition sense via PC817 (LOW = ON)
#define ENGINE_SENSE_MODE    0

// Most blue 1-ch relay modules are ACTIVE LOW. Confirm with test T9.
#define RELAY_ACTIVE_LOW     true

// Battery thresholds (volts at INA226 VBUS). Defaults: 12 V bench test.
const float BATT_DIVIDER_RATIO   = 1.0;
const float BATT_PRESENT_MIN_V   = 6.0;
const float BATT_EMPTY_V         = 10.5;
const float BATT_FULL_V          = 12.7;

const float INA_SHUNT_OHM        = 0.1;
const float INA_MAX_CURRENT_A    = 0.8;

const float IMMOBILISE_MAX_KMPH  = 5.0;

// =====================================================================
// PIN MAP (do not change without updating the schematic)
// =====================================================================
#define PIN_GPS_RX       16
#define PIN_GPS_TX       17
#define PIN_SIM_RX       26
#define PIN_SIM_TX       27
#define PIN_SIM_RST      4
#define PIN_RELAY        25
#define PIN_ENGINE       34
#define PIN_SDA          21
#define PIN_SCL          22
#define PIN_BUZZER       13
#define PIN_LED          2

#define SIM_BAUD         57600   // drop to 9600 if AT replies are garbled

// =====================================================================
// TIMING  (GPRS is slow: keep traffic low)
// =====================================================================
#if NET_MODE == 1
const unsigned long UPLOAD_INTERVAL_MS   = 30000;
const unsigned long COMMAND_POLL_MS      = 15000;
#else
const unsigned long UPLOAD_INTERVAL_MS   = 5000;
const unsigned long COMMAND_POLL_MS      = 5000;
#endif
const unsigned long BATTERY_CHECK_MS     = 100;
const unsigned long STATUS_PRINT_MS      = 2000;
const unsigned long ENGINE_TIMEOUT_MS    = 2000;
const unsigned long WIFI_RETRY_MS        = 10000;
const unsigned long MODEM_CHECK_MS       = 30000;
const unsigned long SMS_MIN_GAP_MS       = 300000;  // max one alert SMS per 5 min
const uint8_t       BATT_DEBOUNCE_COUNT  = 3;

// =====================================================================
// OBJECTS
// =====================================================================
TinyGPSPlus     gps;
HardwareSerial  GPSSerial(2);
HardwareSerial  SerialAT(1);
TinyGsm         modem(SerialAT);
TinyGsmClient   gsmClient(modem, 0);
INA226          ina(0x40);

FirebaseData    fbdo;
FirebaseAuth    auth;
FirebaseConfig  config;

// =====================================================================
// STATE
// =====================================================================
volatile uint32_t hallPulseCount = 0;
volatile unsigned long lastPulseMs = 0;

bool  inaOk = false, modemOk = false;
bool  engineOn = false, batteryPresent = true, alarmActive = false;
bool  immobilised = false, immobilisePending = false;
bool  pendingBattAlert = false, pendingSms = false, pendingRestoreSms = false;
float battVoltage = 0, battCurrent_mA = 0, pulsesPerSec = 0;
int   csq = 99;
uint8_t battLowCount = 0, battOkCount = 0;
String lastCommand = "";

unsigned long lastUpload = 0, lastPoll = 0, lastBattCheck = 0, lastPrint = 0;
unsigned long lastWifiTry = 0, lastModemCheck = 0, lastSmsMs = 0;
unsigned long lastRateCalc = 0; uint32_t lastPulseSnapshot = 0;

// =====================================================================
// ISR + HELPERS
// =====================================================================
void IRAM_ATTR onHallPulse() { hallPulseCount++; lastPulseMs = millis(); }

void setRelay(bool energise) {
  bool level = RELAY_ACTIVE_LOW ? !energise : energise;
  digitalWrite(PIN_RELAY, level ? HIGH : LOW);
  immobilised = energise;              // energised => NC opens => ignition cut
}

void setBuzzer(bool on) { digitalWrite(PIN_BUZZER, on ? HIGH : LOW); }

float gpsSpeed() {
  return (gps.speed.isValid() && gps.speed.age() < 3000) ? gps.speed.kmph() : 0.0;
}

// Feed GPS while waiting on slow modem operations
void serviceGPS() { while (GPSSerial.available()) gps.encode(GPSSerial.read()); }

void modemHardReset() {
  pinMode(PIN_SIM_RST, OUTPUT);
  digitalWrite(PIN_SIM_RST, LOW);       // RST is active LOW
  delay(200);
  pinMode(PIN_SIM_RST, INPUT);          // release: never drive 3.3 V into the 2.8 V pin
  delay(3000);
}

String mapsLink() {
  if (!gps.location.isValid()) return "GPS no fix";
  return "https://maps.google.com/?q=" + String(gps.location.lat(), 6) + "," + String(gps.location.lng(), 6);
}

// =====================================================================
// SETUP
// =====================================================================
void setup() {
  pinMode(PIN_RELAY, OUTPUT);  setRelay(false);   // never cut during boot
  pinMode(PIN_BUZZER, OUTPUT); setBuzzer(false);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_SIM_RST, INPUT);

  Serial.begin(115200);
  delay(300);
  Serial.println(F("\n=== EV Tracker v3.0 (SIM800L) ==="));

  GPSSerial.setRxBufferSize(2048);
  GPSSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);

  pinMode(PIN_ENGINE, INPUT);
#if ENGINE_SENSE_MODE == 0
  attachInterrupt(digitalPinToInterrupt(PIN_ENGINE), onHallPulse, FALLING);
#endif

  Wire.begin(PIN_SDA, PIN_SCL);
  inaOk = ina.begin();
  if (inaOk) {
    ina.setMaxCurrentShunt(INA_MAX_CURRENT_A, INA_SHUNT_OHM);
    ina.setAverage(INA226_16_SAMPLES);
    Serial.println(F("[OK] INA226 found at 0x40"));
  } else {
    Serial.println(F("[ERR] INA226 not found - check SDA/SCL/3V3"));
  }

  startModem();

#if NET_MODE == 0
  connectWiFi();
#endif
  connectFirebase();
  printHelp();
}

// =====================================================================
// LOOP
// =====================================================================
void loop() {
  serviceGPS();
  updateEngine();

  if (millis() - lastBattCheck >= BATTERY_CHECK_MS) { lastBattCheck = millis(); updateBattery(); }

#if NET_MODE == 0
  maintainWiFi();
#endif
  maintainModem();

  sendPendingSms();
  sendPendingAlert();
  handleImmobilisePending();

  if (millis() - lastPoll >= COMMAND_POLL_MS) { lastPoll = millis(); pollCommand(); }
  if (millis() - lastUpload >= UPLOAD_INTERVAL_MS) { lastUpload = millis(); uploadStatus(); }
  if (millis() - lastPrint >= STATUS_PRINT_MS) { lastPrint = millis(); printStatus(); }

  handleSerialMenu();
  digitalWrite(PIN_LED, Firebase.ready() ? HIGH : (millis() / 250) % 2);
}

// =====================================================================
// SIM800L
// =====================================================================
void startModem() {
  Serial.println(F("[..] SIM800L reset + init"));
  modemHardReset();
  SerialAT.begin(SIM_BAUD, SERIAL_8N1, PIN_SIM_RX, PIN_SIM_TX);
  delay(500);

  modemOk = modem.init(strlen(SIM_PIN) ? SIM_PIN : nullptr);
  if (!modemOk) { Serial.println(F("[ERR] SIM800L not answering (check power 3.7-4.2 V, TX/RX, baud)")); return; }
  Serial.printf("[OK] Modem: %s\n", modem.getModemInfo().c_str());
  Serial.printf("     SIM status: %d (1 = ready)\n", (int)modem.getSimStatus());

  Serial.print(F("[..] Waiting for 2G network"));
  if (modem.waitForNetwork(60000L)) {
    csq = modem.getSignalQuality();
    Serial.printf("\n[OK] Network: %s  CSQ %d\n", modem.getOperator().c_str(), csq);
  } else {
    Serial.println(F("\n[WARN] No network yet (2G coverage? Jio SIM? antenna?)"));
  }

#if NET_MODE == 1
  if (modem.isNetworkConnected() && modem.gprsConnect(GPRS_APN, GPRS_USER, GPRS_PASS))
    Serial.printf("[OK] GPRS up, IP %s\n", modem.getLocalIP().c_str());
  else
    Serial.println(F("[WARN] GPRS not connected yet, Firebase will retry"));
#endif
}

void maintainModem() {
  if (millis() - lastModemCheck < MODEM_CHECK_MS) return;
  lastModemCheck = millis();
  if (!modemOk) {                       // modem never came up: try again
    startModem();
    return;
  }
  csq = modem.getSignalQuality();
  if (!modem.testAT(2000)) {            // modem hung (often a brown-out)
    Serial.println(F("[WARN] SIM800L not responding, hard reset"));
    modemOk = false;
    startModem();
  }
}

bool sendSms(const String& text) {
  if (!SMS_ENABLED || !modemOk) return false;
  bool ok = modem.sendSMS(OWNER_PHONE, text);
  Serial.printf("[%s] SMS to owner\n", ok ? "OK" : "ERR");
  return ok;
}

void sendPendingSms() {
  if (pendingSms && (lastSmsMs == 0 || millis() - lastSmsMs > SMS_MIN_GAP_MS)) {
    String msg = "EV TRACKER ALERT: battery removed! V=" + String(battVoltage, 1) + "V. " + mapsLink();
    if (sendSms(msg)) { pendingSms = false; lastSmsMs = millis(); }
  }
  if (pendingRestoreSms && !pendingSms) {
    if (sendSms("EV TRACKER: battery reconnected. " + mapsLink())) pendingRestoreSms = false;
  }
}

// =====================================================================
// ENGINE
// =====================================================================
void updateEngine() {
#if ENGINE_SENSE_MODE == 0
  unsigned long last;
  noInterrupts(); last = lastPulseMs; interrupts();
  engineOn = (last != 0) && (millis() - last < ENGINE_TIMEOUT_MS);
  if (millis() - lastRateCalc >= 1000) {
    uint32_t c; noInterrupts(); c = hallPulseCount; interrupts();
    pulsesPerSec = (c - lastPulseSnapshot) * 1000.0 / (millis() - lastRateCalc);
    lastPulseSnapshot = c; lastRateCalc = millis();
  }
#else
  engineOn = (digitalRead(PIN_ENGINE) == LOW);
#endif
}

// =====================================================================
// BATTERY
// =====================================================================
void updateBattery() {
  if (!inaOk) return;
  battVoltage    = ina.getBusVoltage() * BATT_DIVIDER_RATIO;
  battCurrent_mA = ina.getCurrent_mA();

  if (battVoltage < BATT_PRESENT_MIN_V) { battLowCount++; battOkCount = 0; }
  else                                  { battOkCount++;  battLowCount = 0; }

  if (batteryPresent && battLowCount >= BATT_DEBOUNCE_COUNT) {
    batteryPresent = false; alarmActive = true;
    pendingBattAlert = true; pendingSms = true;
    setBuzzer(true);
    Serial.println(F("!!! ALERT: BATTERY REMOVED !!!"));
  }
  if (!batteryPresent && battOkCount >= BATT_DEBOUNCE_COUNT) {
    batteryPresent = true; pendingRestoreSms = true;
    Serial.println(F("Battery restored (alarm stays on until CLEAR or 'c')"));
  }
}

int batteryPercent() {
  float p = (battVoltage - BATT_EMPTY_V) * 100.0 / (BATT_FULL_V - BATT_EMPTY_V);
  return constrain((int)p, 0, 100);
}

// =====================================================================
// WIFI (bench mode only)
// =====================================================================
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print(F("Wi-Fi connecting"));
  for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print('.'); }
  Serial.println(WiFi.status() == WL_CONNECTED ? F("\n[OK] Wi-Fi") : F("\n[WARN] Wi-Fi not connected"));
  lastWifiTry = millis();
}

void maintainWiFi() {
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > WIFI_RETRY_MS) {
    lastWifiTry = millis();
    WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
}

// =====================================================================
// FIREBASE
// =====================================================================
void connectFirebase() {
  config.api_key      = API_KEY;
  config.database_url = DATABASE_URL;
  auth.user.email     = USER_EMAIL;
  auth.user.password  = USER_PASSWORD;
  config.token_status_callback = tokenStatusCallback;
  config.timeout.serverResponse = 20 * 1000;   // GPRS is slow

#if NET_MODE == 1
  fbdo.setGSMClient(&gsmClient, &modem, SIM_PIN, GPRS_APN, GPRS_USER, GPRS_PASS);
#endif
  Firebase.reconnectNetwork(true);
  fbdo.setBSSLBufferSize(4096, 1024);
  fbdo.setResponseSize(2048);
  Firebase.begin(&config, &auth);
}

String basePath() { return String("/") + VEHICLE_ID; }

// Command values written by the app:
//   "OFF" immobilise (only when speed <= limit), "ON" release, "CLEAR" silence alarm
void pollCommand() {
  if (!Firebase.ready()) return;
  if (!Firebase.RTDB.getString(&fbdo, basePath() + "/commands/engineControl")) {
    Serial.printf("[ERR] Command read: %s\n", fbdo.errorReason().c_str());
    return;
  }
  serviceGPS();
  String cmd = fbdo.stringData(); cmd.trim(); cmd.toUpperCase();
  if (cmd == lastCommand) return;      // only act on changes
  lastCommand = cmd;
  Serial.printf(">> Command received: %s\n", cmd.c_str());

  if (cmd == "OFF") {
    if (gpsSpeed() > IMMOBILISE_MAX_KMPH) {
      immobilisePending = true;
      Serial.println(F("   Vehicle moving - immobilise queued until speed is low"));
    } else setRelay(true);
  } else if (cmd == "ON") {
    immobilisePending = false; setRelay(false);
  } else if (cmd == "CLEAR") {
    alarmActive = false; setBuzzer(false);
  }
}

void handleImmobilisePending() {
  if (immobilisePending && gpsSpeed() <= IMMOBILISE_MAX_KMPH) {
    immobilisePending = false; setRelay(true);
    Serial.println(F("   Speed low - immobiliser engaged"));
  }
}

void sendPendingAlert() {
  if (!pendingBattAlert || !Firebase.ready()) return;
  FirebaseJson a;
  a.set("type", "BATTERY_REMOVED");
  a.set("voltage", battVoltage);
  if (gps.location.isValid()) { a.set("lat", gps.location.lat()); a.set("lng", gps.location.lng()); }
  a.set("time/.sv", "timestamp");
  bool ok1 = Firebase.RTDB.setBool(&fbdo, basePath() + "/battery/present", false);
  bool ok2 = Firebase.RTDB.pushJSON(&fbdo, basePath() + "/alerts", &a);
  serviceGPS();
  if (ok1 && ok2) { pendingBattAlert = false; Serial.println(F("[OK] Alert sent to Firebase")); }
}

void uploadStatus() {
  if (!Firebase.ready()) return;
  FirebaseJson j;
  j.set("gps/fix", gps.location.isValid());
  j.set("gps/sats", (int)gps.satellites.value());
  if (gps.location.isValid()) {
    j.set("gps/lat", gps.location.lat());
    j.set("gps/lng", gps.location.lng());
    j.set("gps/speedKmph", gpsSpeed());
  }
  j.set("engine/status", engineOn);
  j.set("engine/immobilised", immobilised);
  j.set("engine/immobilisePending", immobilisePending);
  j.set("battery/voltage", battVoltage);
  j.set("battery/current_mA", battCurrent_mA);
  j.set("battery/percent", batteryPercent());
  j.set("battery/present", batteryPresent);
  j.set("battery/alarm", alarmActive);
  j.set("device/net", NET_MODE == 1 ? "GPRS" : "WIFI");
  j.set("device/csq", csq);
  j.set("device/uptimeS", (int)(millis() / 1000));
  j.set("lastUpdated/.sv", "timestamp");

  unsigned long t = millis();
  bool ok = Firebase.RTDB.updateNode(&fbdo, basePath(), &j);
  serviceGPS();
  if (ok) Serial.printf("[OK] Uploaded (%lu ms)\n", millis() - t);
  else    Serial.printf("[ERR] Upload: %s\n", fbdo.errorReason().c_str());
}

// =====================================================================
// SERIAL TEST MENU
// =====================================================================
void printHelp() {
  Serial.println(F("\n--- Test menu (Serial Monitor 115200, line ending: Newline) ---"));
  Serial.println(F(" h help            s status now      i I2C scan"));
  Serial.println(F(" r toggle relay    b buzzer beep     c clear alarm"));
  Serial.println(F(" g raw GPS 5 s     u force upload    k poll command now"));
  Serial.println(F(" m modem info      t send test SMS"));
  Serial.println(F(" p AT pass-through to SIM800L (type ~ to exit)"));
}

void printStatus() {
  Serial.printf("GPS fix:%s sats:%u lat:%.6f lng:%.6f spd:%.1f | ",
    gps.location.isValid() ? "Y" : "N", (unsigned)gps.satellites.value(),
    gps.location.lat(), gps.location.lng(), gpsSpeed());
#if ENGINE_SENSE_MODE == 0
  Serial.printf("ENG:%s (%.1f p/s) | ", engineOn ? "ON" : "OFF", pulsesPerSec);
#else
  Serial.printf("ENG:%s | ", engineOn ? "ON" : "OFF");
#endif
  Serial.printf("BATT:%.2fV %.0fmA %d%% %s | RELAY:%s | CSQ:%d FB:%s\n",
    battVoltage, battCurrent_mA, batteryPercent(), batteryPresent ? "OK" : "REMOVED",
    immobilised ? "IMMOBILISED" : "RUN", csq, Firebase.ready() ? "Y" : "N");
}

void modemInfo() {
  if (!modemOk) { Serial.println(F("Modem not initialised")); return; }
  Serial.printf("Modem   : %s\n", modem.getModemInfo().c_str());
  Serial.printf("SIM     : %d (1 = ready)\n", (int)modem.getSimStatus());
  Serial.printf("Operator: %s\n", modem.getOperator().c_str());
  Serial.printf("CSQ     : %d (10-31 good, 99 = none)\n", modem.getSignalQuality());
  Serial.printf("Network : %s\n", modem.isNetworkConnected() ? "registered" : "NOT registered");
  Serial.printf("GPRS    : %s  IP %s\n", modem.isGprsConnected() ? "up" : "down", modem.getLocalIP().c_str());
}

void atPassThrough() {
  Serial.println(F("AT pass-through. Type AT commands, ~ to exit."));
  while (true) {
    while (SerialAT.available()) Serial.write(SerialAT.read());
    if (Serial.available()) {
      char ch = Serial.read();
      if (ch == '~') break;
      SerialAT.write(ch);
    }
    serviceGPS();
  }
  Serial.println(F("Pass-through closed."));
}

void handleSerialMenu() {
  if (!Serial.available()) return;
  char c = Serial.read();
  switch (c) {
    case 'h': printHelp(); break;
    case 's': printStatus(); break;
    case 'r': setRelay(!immobilised);
              Serial.printf("Relay -> %s\n", immobilised ? "IMMOBILISED (energised)" : "RUN (released)"); break;
    case 'b': setBuzzer(true); delay(1000); setBuzzer(alarmActive); break;
    case 'c': alarmActive = false; setBuzzer(false); Serial.println(F("Alarm cleared")); break;
    case 'u': uploadStatus(); break;
    case 'k': lastCommand = ""; pollCommand(); break;
    case 'm': modemInfo(); break;
    case 't': sendSms("EV TRACKER test SMS. " + mapsLink()); break;
    case 'p': atPassThrough(); break;
    case 'g': {
      unsigned long t = millis();
      while (millis() - t < 5000) if (GPSSerial.available()) Serial.write(GPSSerial.read());
      Serial.println(); break;
    }
    case 'i':
      Serial.println(F("I2C scan:"));
      for (uint8_t a = 1; a < 127; a++) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() == 0) Serial.printf("  found 0x%02X\n", a);
      }
      break;
    default: break;
  }
}
