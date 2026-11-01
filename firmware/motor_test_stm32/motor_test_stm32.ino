// MAZERAT - Both motors run forward continuously (STM32F411 + TB6612FNG)
// PWMA=PB6 AIN1=PB8 AIN2=PB9 | PWMB=PB7 BIN1=PB12 BIN2=PB13 | STBY=PB14

const int PWMA = PB6, AIN1 = PB8,  AIN2 = PB9;
const int PWMB = PB7, BIN1 = PB12, BIN2 = PB13;
const int STBY = PB14;

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
