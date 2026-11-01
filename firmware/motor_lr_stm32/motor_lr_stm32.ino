// MAZERAT - Left / Right motor test, forward and backward (STM32F411 + TB6612FNG)
// Every pin is defined below - change them to match your wiring.
//
// Test order (repeats):
//   LEFT  forward  2 s
//   LEFT  backward 2 s
//   RIGHT forward  2 s
//   RIGHT backward 2 s
//   BOTH  forward  2 s
//   BOTH  backward 2 s
// 1 s stop between each step.

// ===================== PIN SETTINGS =====================
// LEFT motor  (driver channel A: PWMA / AIN1 / AIN2 -> A01, A02)
const int LEFT_PWM  = PB6;
const int LEFT_IN1  = PB8;
const int LEFT_IN2  = PB9;

// RIGHT motor (driver channel B: PWMB / BIN1 / BIN2 -> B01, B02)
const int RIGHT_PWM = PB7;
const int RIGHT_IN1 = PB12;
const int RIGHT_IN2 = PB13;

// Driver standby pin (must be HIGH)
const int STBY = PB14;

// true  = speed control with PWM (analogWrite)
// false = PWM pin is just set HIGH (full speed) - use this to rule out PWM problems
const bool USE_PWM = true;
const int  SPEED   = 255;   // 0-255, only used when USE_PWM = true

// ===================== motor functions =====================
void setMotor(int pwmPin, int in1, int in2, int dir) {   // dir: 1 = forward, -1 = backward, 0 = stop
  digitalWrite(in1, dir ==  1 ? HIGH : LOW);
  digitalWrite(in2, dir == -1 ? HIGH : LOW);
  if (USE_PWM) analogWrite(pwmPin, dir == 0 ? 0 : SPEED);
  else         digitalWrite(pwmPin, dir == 0 ? LOW : HIGH);
}

void leftForward()   { setMotor(LEFT_PWM,  LEFT_IN1,  LEFT_IN2,   1); }
void leftBackward()  { setMotor(LEFT_PWM,  LEFT_IN1,  LEFT_IN2,  -1); }
void leftStop()      { setMotor(LEFT_PWM,  LEFT_IN1,  LEFT_IN2,   0); }

void rightForward()  { setMotor(RIGHT_PWM, RIGHT_IN1, RIGHT_IN2,  1); }
void rightBackward() { setMotor(RIGHT_PWM, RIGHT_IN1, RIGHT_IN2, -1); }
void rightStop()     { setMotor(RIGHT_PWM, RIGHT_IN1, RIGHT_IN2,  0); }

void stopAll() { leftStop(); rightStop(); delay(1000); }

void setup() {
  pinMode(LEFT_PWM,  OUTPUT); pinMode(LEFT_IN1,  OUTPUT); pinMode(LEFT_IN2,  OUTPUT);
  pinMode(RIGHT_PWM, OUTPUT); pinMode(RIGHT_IN1, OUTPUT); pinMode(RIGHT_IN2, OUTPUT);
  pinMode(STBY, OUTPUT);
  digitalWrite(STBY, HIGH);   // driver ON
}

void loop() {
  leftForward();   delay(2000); stopAll();
  leftBackward();  delay(2000); stopAll();
  rightForward();  delay(2000); stopAll();
  rightBackward(); delay(2000); stopAll();
  leftForward();  rightForward();  delay(2000); stopAll();
  leftBackward(); rightBackward(); delay(2000); stopAll();
}
