// ToolLighthouse Nano protocol: MASK:0..511\n -> ACK:0..511\n.
const byte LED_PINS[9] = {2, 3, 4, 5, 6, 7, 8, 9, 10};
const unsigned long WATCHDOG_MS = 2500;
unsigned long lastValidMessage = 0;
String line;
bool overflow = false;

void applyMask(unsigned int mask) {
  for (byte i = 0; i < 9; ++i) digitalWrite(LED_PINS[i], (mask >> i) & 1 ? HIGH : LOW);
}

void setup() {
  for (byte i = 0; i < 9; ++i) { pinMode(LED_PINS[i], OUTPUT); digitalWrite(LED_PINS[i], LOW); }
  Serial.begin(115200);
  applyMask(0);
  lastValidMessage = millis();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (!overflow && line.startsWith("MASK:")) {
        String value = line.substring(5);
        bool digits = value.length() > 0;
        for (unsigned int i = 0; i < value.length(); ++i) if (value[i] < '0' || value[i] > '9') digits = false;
        long parsed = value.toInt();
        if (digits && parsed >= 0 && parsed <= 511) {
          applyMask((unsigned int)parsed);
          lastValidMessage = millis();
          Serial.print("ACK:"); Serial.println(parsed);
        }
      }
      line = "";
      overflow = false;
    } else if (c != '\r') {
      if (line.length() < 24 && !overflow) line += c;
      else overflow = true;
    }
  }
  if (millis() - lastValidMessage > WATCHDOG_MS) applyMask(0);
}
