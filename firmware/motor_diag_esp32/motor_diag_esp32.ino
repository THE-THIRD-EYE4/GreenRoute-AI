// MAZERAT - Motor diagnostic (ESP32 + TB6612FNG)
// Runs each motor ALONE, both directions, so you can see which channel is dead.
//   1) Motor A forward  (2 s)   -> DDP1, driver pins A01/A02
//   2) Motor A reverse  (2 s)
//   3) Motor B forward  (2 s)   -> DDP2, driver pins B01/B02
//   4) Motor B reverse  (2 s)
//   1 s stop between each step, then repeat.
// No Serial: STBY is on the TX pin.
const int PWMA = 25, AIN1 = 26, AIN2 = 27;
const int PWMB = 14, BIN1 = 12, BIN2 = 13;
const int STBY = 1;

const int SPEED = 255;

void motorA(int s) { digitalWrite(AIN1, s > 0); digitalWrite(AIN2, s < 0); analogWrite(PWMA, abs(s)); }
void motorB(int s) { digitalWrite(BIN1, s > 0); digitalWrite(BIN2, s < 0); analogWrite(PWMB, abs(s)); }
void stopAll() { motorA(0); motorB(0); delay(1000); }

void setup() {
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT); pinMode(PWMA, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT); pinMode(PWMB, OUTPUT);
  pinMode(STBY, OUTPUT);
  digitalWrite(STBY, HIGH);
}

void loop() {
  motorA( SPEED); delay(2000); stopAll();
  motorA(-SPEED); delay(2000); stopAll();
  motorB( SPEED); delay(2000); stopAll();
  motorB(-SPEED); delay(2000); stopAll();
  delay(1000);
}
