// MAZERAT - Motor test only (STM32F411CEU6 "Black Pill" + TB6612FNG + 2x N20)
// Arduino IDE with STM32duino core (board: Generic STM32F4 -> BlackPill F411CE).
// Sequence: FORWARD -> stop -> BACKWARD -> stop -> LEFT -> stop -> RIGHT -> stop, repeat.
// No sensors used.
//
// Pin map from schematic (U6 STM32F411CEU6 -> U2 TB6612FNG):
//   PWMA = PB6, AIN1 = PB8, AIN2 = PB9   -> Motor A (A01/A02) = DDP1
//   PWMB = PB7, BIN1 = PB12, BIN2 = PB13 -> Motor B (B01/B02) = DDP2
//   STBY = PB14

const int PWMA = PB6;
const int AIN1 = PB8;
const int AIN2 = PB9;
const int PWMB = PB7;
const int BIN1 = PB12;
const int BIN2 = PB13;
const int STBY = PB14;

// ---- Adjust these after the first run ----
const bool LEFT_IS_A = true;   // true = Motor A (DDP1) is the left wheel
const bool INVERT_A  = false;  // flip if that wheel runs backward on FORWARD
const bool INVERT_B  = false;

const int SPEED   = 180;   // 0-255
const int RUN_MS  = 1500;
const int STOP_MS = 1000;

void motorA(int speed) {
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
void left()     { drive(-SPEED,  SPEED); }
void right()    { drive( SPEED, -SPEED); }
void stopAll()  { drive(0, 0); }

void setup() {
  Serial.begin(115200);
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT); pinMode(PWMA, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT); pinMode(PWMB, OUTPUT);
  pinMode(STBY, OUTPUT);
  stopAll();
  digitalWrite(STBY, HIGH);
  delay(3000);
}

void loop() {
  Serial.println("FORWARD");  forward();  delay(RUN_MS); stopAll(); delay(STOP_MS);
  Serial.println("BACKWARD"); backward(); delay(RUN_MS); stopAll(); delay(STOP_MS);
  Serial.println("LEFT");     left();     delay(RUN_MS); stopAll(); delay(STOP_MS);
  Serial.println("RIGHT");    right();    delay(RUN_MS); stopAll(); delay(STOP_MS * 2);
}
