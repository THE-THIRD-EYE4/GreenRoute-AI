/*
  SIM800L bring-up test - run this FIRST, before the main firmware.
  Wiring (schematic EVT-SCH-03):
    SIM800L VCC  <- 18650 cell (3.7-4.2 V) with 1000 uF + 100 nF at the module pins
    SIM800L GND  -> common GND
    SIM800L TXD  -> ESP32 GPIO26
    SIM800L RXD  <- ESP32 GPIO27 through 1k series, 3k3 to GND (2.5 V level)
    SIM800L RST  <- ESP32 GPIO4 (open-drain)
  Serial Monitor: 115200, line ending "Both NL & CR".
  Watch the SIM800L NET LED:
    fast blink (~1 s)  = searching      slow blink (~3 s) = registered
    very fast (~0.3 s) = GPRS data connected
*/

HardwareSerial SIM(1);
#define PIN_SIM_RX  26
#define PIN_SIM_TX  27
#define PIN_SIM_RST 4
#define SIM_BAUD    57600         // try 9600 if replies are garbled

const char* APN   = "airtelgprs.com";   // Vi: "www"  BSNL: "bsnlnet"
const char* PHONE = "+91XXXXXXXXXX";    // your phone, for the SMS test

String sendAT(const String& cmd, unsigned long waitMs = 2000) {
  while (SIM.available()) SIM.read();
  SIM.println(cmd);
  String r; unsigned long t = millis();
  while (millis() - t < waitMs) while (SIM.available()) r += (char)SIM.read();
  r.trim();
  Serial.printf(">> %s\n%s\n\n", cmd.c_str(), r.c_str());
  return r;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_SIM_RST, OUTPUT); digitalWrite(PIN_SIM_RST, LOW); delay(200);
  pinMode(PIN_SIM_RST, INPUT);                      // release reset
  SIM.begin(SIM_BAUD, SERIAL_8N1, PIN_SIM_RX, PIN_SIM_TX);
  delay(6000);                                      // module boot

  Serial.println("=== SIM800L checks ===");
  for (int i = 0; i < 5; i++) SIM.println("AT"), delay(200);   // auto-baud sync
  sendAT("AT");                 // OK
  sendAT("ATI");                // SIM800 R14.xx
  sendAT("AT+CBC");             // supply: +CBC: 0,xx,3700-4200 mV expected
  sendAT("AT+CPIN?");           // +CPIN: READY
  sendAT("AT+CSQ");             // 10-31 good, 99 = no signal
  sendAT("AT+CREG?");           // ,1 home or ,5 roaming
  sendAT("AT+COPS?");           // operator name
  sendAT("AT+CGATT?");          // +CGATT: 1 = attached to GPRS
  sendAT("AT+SAPBR=3,1,\"CONTYPE\",\"GPRS\"");
  sendAT(String("AT+SAPBR=3,1,\"APN\",\"") + APN + "\"");
  sendAT("AT+SAPBR=1,1", 10000);   // open data bearer
  sendAT("AT+SAPBR=2,1");          // +SAPBR: 1,1,"10.x.x.x" = data works
  sendAT("AT+SAPBR=0,1", 5000);    // close bearer
  Serial.println("=== done. Type 'sms' to send a test SMS, or any AT command ===");
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n'); line.trim();
    if (line == "sms") {
      sendAT("AT+CMGF=1");
      SIM.printf("AT+CMGS=\"%s\"\r", PHONE); delay(500);
      SIM.print("EV Tracker SIM800L test SMS"); SIM.write(26);   // Ctrl+Z
      Serial.println("SMS sent - wait for +CMGS: n");
    } else if (line.length()) {
      SIM.println(line);
    }
  }
  while (SIM.available()) Serial.write(SIM.read());
}
