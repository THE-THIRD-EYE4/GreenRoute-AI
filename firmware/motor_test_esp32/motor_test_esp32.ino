// MAZERAT - Both motors run forward continuously (ESP32 + TB6612FNG)
// PWMA=IO25 AIN1=IO26 AIN2=IO27 | PWMB=IO14 BIN1=IO12 BIN2=IO13 | STBY=TX(GPIO1)
// No Serial: STBY is on the TX pin.

const int PWMA = 25, AIN1 = 26, AIN2 = 27;
const int PWMB = 14, BIN1 = 12, BIN2 = 13;
const int STBY = 1;

const int SPEED = 255;   // 0-255

void setup() {
  pinMode(AIN1, OUTPUT); pinMode(AIN2, OUTPUT); pinMode(PWMA, OUTPUT);
  pinMode(BIN1, OUTPUT); pinMode(BIN2, OUTPUT); pinMode(PWMB, OUTPUT);
  pinMode(STBY, OUTPUT);

  digitalWrite(STBY, HIGH);   // enable driver

  digitalWrite(AIN1, HIGH); digitalWrite(AIN2, LOW);   // Motor A forward
  analogWrite(PWMA, SPEED);

  digitalWrite(BIN1, HIGH); digitalWrite(BIN2, LOW);   // Motor B forward
  analogWrite(PWMB, SPEED);
}

void loop() {
  // motors keep running
}
