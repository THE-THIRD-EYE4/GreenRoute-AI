// MAZERAT - Motor test only (ESP32-DevKitC + TB6612FNG + 2x N20)
// Sequence: FORWARD -> stop -> BACKWARD -> stop -> LEFT -> stop -> RIGHT -> stop, repeat.
// No sensors used.
//
// Pin map from schematic (U1 ESP32-DEVKITC -> U2 TB6612FNG):
//   PWMA = IO25, AIN1 = IO26, AIN2 = IO27   -> Motor A (A01/A02) = DDP1
//   PWMB = IO14, BIN1 = IO12, BIN2 = IO13   -> Motor B (B01/B02) = DDP2
//   STBY = TX (GPIO1)
//
// NOTE: STBY is wired to the TX pin (GPIO1), so Serial is NOT used here.
// Serial output would toggle STBY and disable the driver.
// Upload, then unplug USB / switch to battery and watch the wheels.

// ---- Pins ----
const int PWMA = 25;
const int AIN1 = 26;
const int AIN2 = 27;
const int PWMB = 14;
const int BIN1 = 12;
const int BIN2 = 13;
const int STBY = 1;   // TX pin

// ---- Adjust these after the first run ----
// Which driver channel is the LEFT wheel? true = Motor A (DDP1), false = Motor B (DDP2)
const bool LEFT_IS_A = true;
// If a wheel spins backward on FORWARD, flip its flag.
const bool INVERT_A = false;
const bool INVERT_B = false;

const int SPEED   = 180;   // 0-255
const int RUN_MS  = 1500;  // time per move
const int STOP_MS = 1000;  // pause between moves

void motorA(int speed) {           // speed: -255..255
  if (INVERT_A) speed = -speed;
  digitalWrite(AIN1, speed > 0 ? HIGH : LOW);
  digitalWrite(AIN2, speed < 0 ? HIGH : LOW);
  analogWrite(PWMA, abs(speed));
}

void motorB(int speed) {
  if (INVERT_B) speed = -speed;
  digitalWrite(BIN1, speed > 0 ? HIGH : LOW);
  digitalWrite(BIN2, speed < 0 ? HIGH : LOW);
  analogWrite(PWMB, abs(speed));
}

void drive(int left, int right) {
  if (LEFT_IS_A) { motorA(left);  motorB(right); }
  else           { motorB(left);  motorA(right); }
}

void forward()  { drive( SPEED,  SPEED); }
void backward() { drive(-SPEED, -SPEED); }
void left()     { drive(-SPEED,  SPEED); }   // spin left in place
void right()    { drive( SPEED, -SPEED); }   // spin right in place
void stopAll()  { drive(0, 0); }

void setup() {
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT); pinMode(PWMA, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT); pinMode(PWMB, OUTPUT);
  pinMode(STBY, OUTPUT);
  stopAll();
  digitalWrite(STBY, HIGH);   // enable TB6612FNG
  delay(3000);                // time to put the robot down
}

void loop() {
  forward();  delay(RUN_MS); stopAll(); delay(STOP_MS);
  backward(); delay(RUN_MS); stopAll(); delay(STOP_MS);
  left();     delay(RUN_MS); stopAll(); delay(STOP_MS);
  right();    delay(RUN_MS); stopAll(); delay(STOP_MS * 2);
}
