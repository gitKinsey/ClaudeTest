/*
  CoreBringup.ino - STEP 1: prove the board, the USB cable and the PC link work.   (Waveshare ESP32-S3-Zero)

  No libraries needed (no TFT_eSPI / ArduinoJson / ...), so nothing here can fail because of a library version.
  It does exactly four things:
    1. Lights the onboard RGB LED (GPIO21) in a boot sequence and then blinks a heartbeat.
    2. Opens the USB serial port and answers the companion app's JSON commands:
         hello, ping, info, led, gpio, inputs, events, echo, run (keyboard / media test), reboot
    3. Reports the 5 keys + encoder (pins from the project) so you can check the wiring from the app's Dev tab.
    4. If the USB mode is "USB-OTG (TinyUSB)" it also enumerates a keyboard + media keys for the HID test.

  Arduino IDE settings:
    Board "Waveshare ESP32-S3-Zero" (or "ESP32S3 Dev Module"), USB CDC On Boot = Enabled,
    USB Mode = "USB-OTG (TinyUSB)"  (works with "Hardware CDC and JTAG" too - only the HID test is then off),
    Flash Size 4MB, PSRAM = Disabled.
  Then start companion_app.py -> "Dev" tab.  When this works, flash DeskCompanion/DeskCompanion.ino (step 2).

  LED colours while booting: purple = running, blue = USB serial up, then a short R/G/B/white test, then a dim green
  heartbeat (blink every 3 s) = alive.  Solid red = something is badly wrong.
*/
#ifndef DC_RGB_PIN
#define DC_RGB_PIN 21
#endif

#if !defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT
#error "Arduino IDE: Tools > USB CDC On Boot must be 'Enabled' (otherwise Serial is the UART, not the USB port)."
#endif
#if defined(ARDUINO_USB_MODE) && (ARDUINO_USB_MODE == 0)
#define DC_HAS_HID 1
#else
#define DC_HAS_HID 0
#endif
#ifdef ARDUINO_USB_MODE
#define DC_USB_MODE ARDUINO_USB_MODE
#else
#define DC_USB_MODE -1
#endif

#include <Arduino.h>
#include "esp_system.h"
#include "esp_attr.h"
#include "soc/rtc_cntl_reg.h"
#if DC_HAS_HID
#include "USB.h"
#include "USBHIDKeyboard.h"
#include "USBHIDConsumerControl.h"
USBHIDKeyboard Keyboard;
USBHIDConsumerControl ConsumerControl;
#endif

static const char* FW = "1.1.0-core";
static const uint8_t PIN_KEY[5] = {1, 2, 4, 5, 6};
static const uint8_t PIN_ENC_A = 13, PIN_ENC_B = 14, PIN_ENC_SW = 15;

// ---- explicit prototypes (the Arduino IDE's automatic prototype generator can misparse sketches with #if blocks)
static void ledPixel(uint8_t r, uint8_t g, uint8_t b);
static void hsv2rgb(uint16_t h, uint8_t* r, uint8_t* g, uint8_t* b);
static void ledBoot(uint8_t r, uint8_t g, uint8_t b);
static void ledService();
static bool hostActive();
static void txLine(String s);
static String reply(bool ok, const char* evt, const String& extra = "");
static void nack(const char* err);
static int jpos(const String& s, const char* key);
static long jint(const String& s, const char* key, long def);
static String jstr(const String& s, const char* key);
static String esc(const String& s);
static void runCmd(const String& line);
static void pinUseJson(int p, String& out);
static bool readable(int p);
static bool drivable(int p);
static String usbModeJson();
static void handle(const String& line);
static void pollInputs();
#if DC_HAS_HID
static bool keyByName(const String& n, uint8_t& code);
static void runHid(const String& line);
#endif
void setup();
void loop();

// ---- state
static uint32_t lastRxMs = 0, ledNextAt = 0;
static bool eventsOn = false, ready = false;
static int32_t reqId = -1;
static uint8_t ledMode = 0;            // 0 auto, 1 off, 2 solid, 3 blink, 4 rainbow
static uint8_t ledR = 0, ledG = 0, ledB = 0, ledBootR = 24, ledBootG = 0, ledBootB = 24;
static uint8_t keyState[5] = {1, 1, 1, 1, 1}, encSw = 1;
static uint32_t keyChangeAt[5] = {0}, encSwChangeAt = 0;
static String rx;

#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 1, 0)
static void ledPixel(uint8_t r, uint8_t g, uint8_t b) { rgbLedWrite(DC_RGB_PIN, r, g, b); }
#else
static void ledPixel(uint8_t r, uint8_t g, uint8_t b) { neopixelWrite(DC_RGB_PIN, r, g, b); }
#endif
static void hsv2rgb(uint16_t h, uint8_t* r, uint8_t* g, uint8_t* b) {
  uint8_t region = h / 60, rem = (h % 60) * 255 / 60, q = 255 - rem, t = rem;
  switch (region) {
    case 0: *r = 255; *g = t; *b = 0; break;
    case 1: *r = q; *g = 255; *b = 0; break;
    case 2: *r = 0; *g = 255; *b = t; break;
    case 3: *r = 0; *g = q; *b = 255; break;
    case 4: *r = t; *g = 0; *b = 255; break;
    default: *r = 255; *g = 0; *b = q; break;
  }
}
static void ledBoot(uint8_t r, uint8_t g, uint8_t b) { ledBootR = r; ledBootG = g; ledBootB = b; if (ledMode == 0) ledPixel(r, g, b); }
static void ledService() {
  uint32_t now = millis();
  if ((int32_t)(now - ledNextAt) < 0) return;
  switch (ledMode) {
    case 1: ledPixel(0, 0, 0); ledNextAt = now + 500; break;
    case 2: ledPixel(ledR, ledG, ledB); ledNextAt = now + 500; break;
    case 3: { bool on = (now / 400) & 1; ledPixel(on ? ledR : 0, on ? ledG : 0, on ? ledB : 0); ledNextAt = now + 40; break; }
    case 4: { uint8_t r, g, b; hsv2rgb((now / 8) % 360, &r, &g, &b); ledPixel(r / 4, g / 4, b / 4); ledNextAt = now + 20; break; }
    default:
      if (!ready) ledPixel(ledBootR, ledBootG, ledBootB);
      else ledPixel(0, (now % 3000) < 120 ? 24 : 0, 0);
      ledNextAt = now + 30;
  }
}

// ---- bounded serial TX (never blocks if the host stops reading)
static bool hostActive() { return lastRxMs && (uint32_t)(millis() - lastRxMs) < 8000; }
static void txLine(String s) {
  s += '\n';
  const uint8_t* p = (const uint8_t*)s.c_str();
  size_t n = s.length(), off = 0;
  uint32_t t0 = millis();
  while (off < n && (uint32_t)(millis() - t0) < 250) {
    int room = Serial.availableForWrite();
    if (room <= 0) { delay(1); continue; }
    size_t k = n - off; if (k > (size_t)room) k = room;
    size_t w = Serial.write(p + off, k);
    if (w == 0) { delay(1); continue; }
    off += w;
  }
}
static String reply(bool ok, const char* evt, const String& extra) {   // {"ok":..,"evt":"..",<extra>[,"id":n]}
  String s = String("{\"ok\":") + (ok ? "true" : "false") + ",\"evt\":\"" + evt + "\"";
  if (extra.length()) { s += ','; s += extra; }
  if (reqId >= 0) { s += ",\"id\":"; s += reqId; }
  s += '}';
  return s;
}
static void nack(const char* err) {
  String s = String("{\"ok\":false,\"err\":\"") + err + "\"";
  if (reqId >= 0) { s += ",\"id\":"; s += reqId; }
  s += '}';
  txLine(s);
}

// ---- tiny JSON field extraction (flat objects only - enough for the app's commands)
static int jpos(const String& s, const char* key) {
  String k = String("\"") + key + "\"";
  int i = s.indexOf(k);
  while (i >= 0) {
    int j = i + k.length();
    while (j < (int)s.length() && s[j] == ' ') j++;
    if (j < (int)s.length() && s[j] == ':') { j++; while (j < (int)s.length() && s[j] == ' ') j++; return j; }
    i = s.indexOf(k, i + 1);
  }
  return -1;
}
static long jint(const String& s, const char* key, long def) {
  int p = jpos(s, key); if (p < 0) return def;
  if (s.startsWith("true", p)) return 1;
  if (s.startsWith("false", p)) return 0;
  return s.substring(p, p + 12).toInt();
}
static String jstr(const String& s, const char* key) {
  int p = jpos(s, key);
  if (p < 0 || s[p] != '"') return String();
  String out;
  for (int i = p + 1; i < (int)s.length(); i++) {
    char c = s[i];
    if (c == '"') return out;
    if (c == '\\' && i + 1 < (int)s.length()) {
      char n = s[++i];
      out += n == 'n' ? '\n' : n == 't' ? '\t' : n == 'r' ? '\r' : n;
    } else out += c;
  }
  return out;
}
static String esc(const String& s) {
  String o;
  for (size_t i = 0; i < s.length(); i++) { char c = s[i]; if (c == '"' || c == '\\') { o += '\\'; o += c; } else if ((uint8_t)c >= 32) o += c; }
  return o;
}

// ---- HID test
#if DC_HAS_HID
static bool keyByName(const String& n, uint8_t& code) {
  String u = n; u.toUpperCase();
  if (u == "CTRL" || u == "CONTROL") code = KEY_LEFT_CTRL; else if (u == "SHIFT") code = KEY_LEFT_SHIFT;
  else if (u == "ALT") code = KEY_LEFT_ALT; else if (u == "GUI" || u == "WIN" || u == "CMD") code = KEY_LEFT_GUI;
  else if (u == "ENTER") code = 0xB0; else if (u == "ESC") code = 0xB1; else if (u == "TAB") code = 0xB3; else if (u == "SPACE") code = ' ';
  else if (n.length() == 1) code = (uint8_t)tolower(n[0]);
  else return false;
  return true;
}
static void runHid(const String& line) {
  String type = jstr(line, "type");
  if (type == "text") {
    String t = jstr(line, "val");
    for (size_t i = 0; i < t.length(); i++) { Keyboard.write((uint8_t)t[i]); delay(5); }
  } else if (type == "media") {
    String m = jstr(line, "val"); m.toUpperCase();
    uint16_t c = m == "MUTE" ? 0xE2 : m == "VOL_UP" ? 0xE9 : m == "VOL_DOWN" ? 0xEA : m == "PLAY_PAUSE" ? 0xCD : m == "NEXT" ? 0xB5 : m == "PREV" ? 0xB6 : 0;
    if (!c) { nack("media"); return; }
    ConsumerControl.press(c); delay(10); ConsumerControl.release();
  } else if (type == "combo") {
    int p = jpos(line, "val");
    if (p < 0 || line[p] != '[') { nack("spec"); return; }
    int e = line.indexOf(']', p);
    String body = line.substring(p + 1, e);
    uint8_t keys[6]; int n = 0;
    int i = 0;
    while (i < (int)body.length() && n < 6) {
      int a = body.indexOf('"', i); if (a < 0) break;
      int b = body.indexOf('"', a + 1); if (b < 0) break;
      String name = body.substring(a + 1, b);
      if (name == "PRIMARY") name = "CTRL";
      if (!keyByName(name, keys[n])) { nack("spec"); return; }
      n++; i = b + 1;
    }
    for (int k = 0; k < n; k++) Keyboard.press(keys[k]);
    delay(15); Keyboard.releaseAll();
  } else { nack("spec"); return; }
  txLine(reply(true, "run"));
}
static void runCmd(const String& line) { runHid(line); }
#else
static void runCmd(const String& line) { (void)line; nack("no_hid"); }
#endif

// ---- commands
static void pinUseJson(int p, String& out) {
  const char* u = "";
  for (int i = 0; i < 5; i++) if (PIN_KEY[i] == p) { static const char* n[5] = {"K1", "K2", "K3", "K4", "K5"}; u = n[i]; }
  if (p == PIN_ENC_A) u = "ENC A"; if (p == PIN_ENC_B) u = "ENC B"; if (p == PIN_ENC_SW) u = "ENC SW";
  if (p == DC_RGB_PIN) u = "RGB LED"; if (p == 0) u = "BOOT button"; if (p == 19 || p == 20) u = "USB";
  out = u;
}
static bool readable(int p) { return (p >= 0 && p <= 18) || p == 21 || (p >= 33 && p <= 48); }
static bool drivable(int p) { return (p >= 1 && p <= 18) || (p >= 33 && p <= 42); }

static String usbModeJson() { return String(",\"usb_mode\":") + DC_USB_MODE; }
static void handle(const String& line) {
  reqId = jint(line, "id", -1);
  String cmd = jstr(line, "cmd");
  if (cmd == "stats") return;
  if (cmd == "hello") {
    txLine(reply(true, "hello", String("\"dev\":\"desk-companion\",\"fw\":\"") + FW + "\",\"core_only\":true,\"mode\":1,\"bright\":200,\"os\":\"win\",\"gif\":false,"
          "\"fs_free\":0,\"fs_total\":0,\"synced\":false,\"layout\":\"en_US\",\"hid\":" + (DC_HAS_HID ? "true" : "false") +
          ",\"disp\":false,\"fs\":false,\"safe\":false,\"led_pin\":" + DC_RGB_PIN));
  } else if (cmd == "ping") {
    txLine(reply(true, "pong", String("\"up\":") + millis() + (jpos(line, "t") >= 0 ? String(",\"t\":") + jint(line, "t", 0) : String())));
  } else if (cmd == "echo") {
    txLine(reply(true, "echo", String("\"data\":\"") + esc(jstr(line, "data")) + "\""));
  } else if (cmd == "info") {
    float t = temperatureRead();
    String x = String("\"fw\":\"") + FW + "\",\"core_only\":true,\"chip\":\"" + ESP.getChipModel() + "\",\"rev\":" + ESP.getChipRevision() +
               ",\"cpu_mhz\":" + ESP.getCpuFreqMHz() + ",\"flash\":" + ESP.getFlashChipSize() +
               ",\"heap\":" + ESP.getFreeHeap() + ",\"heap_min\":" + ESP.getMinFreeHeap() + ",\"psram\":" + ESP.getPsramSize() +
               ",\"temp\":" + (isnan(t) ? 0 : (int)t) + ",\"up_ms\":" + millis() + ",\"core\":\"" + ESP_ARDUINO_VERSION_MAJOR + "." +
               ESP_ARDUINO_VERSION_MINOR + "." + ESP_ARDUINO_VERSION_PATCH + "\"" +
               usbModeJson() +
               ",\"cdc_boot\":" + ARDUINO_USB_CDC_ON_BOOT + ",\"hid\":" + (DC_HAS_HID ? "true" : "false") + ",\"led_pin\":" + DC_RGB_PIN +
               ",\"led_mode\":" + ledMode + ",\"events\":" + (eventsOn ? "true" : "false") + ",\"build\":\"" + __DATE__ + " " + __TIME__ + "\"";
    txLine(reply(true, "info", x));
  } else if (cmd == "led") {
    String m = jstr(line, "mode");
    if (jpos(line, "hex") >= 0) { unsigned long v = strtoul(jstr(line, "hex").c_str() + (jstr(line, "hex")[0] == '#' ? 1 : 0), nullptr, 16); ledR = v >> 16; ledG = v >> 8; ledB = v; ledMode = 2; }
    if (jpos(line, "r") >= 0 || jpos(line, "g") >= 0 || jpos(line, "b") >= 0) {
      ledR = constrain(jint(line, "r", 0), 0, 255); ledG = constrain(jint(line, "g", 0), 0, 255); ledB = constrain(jint(line, "b", 0), 0, 255); ledMode = 2;
    }
    if (m == "auto") ledMode = 0; else if (m == "off") ledMode = 1; else if (m == "solid") ledMode = 2; else if (m == "blink") ledMode = 3;
    else if (m == "rainbow") ledMode = 4; else if (m.length()) { nack("led_mode"); return; }
    ledNextAt = 0;
    txLine(reply(true, "led", String("\"mode\":") + ledMode + ",\"pin\":" + DC_RGB_PIN + ",\"r\":" + ledR + ",\"g\":" + ledG + ",\"b\":" + ledB));
  } else if (cmd == "gpio") {
    String op = jstr(line, "op");
    if (!op.length()) op = "read";
    if (op == "scan") {
      String a = "\"pins\":[";
      bool first = true;
      for (int p = 0; p <= 48; p++) if (readable(p)) { if (!first) a += ','; first = false; a += "["; a += p; a += ","; a += digitalRead(p); a += "]"; }
      txLine(reply(true, "gpio_scan", a + "]"));
      return;
    }
    int p = jint(line, "pin", -1);
    if (!readable(p)) { nack("pin"); return; }
    if (op != "read") {
      if (!drivable(p)) { nack("pin_protected"); return; }
      if (op == "high") { pinMode(p, OUTPUT); digitalWrite(p, HIGH); }
      else if (op == "low") { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
      else if (op == "pullup") pinMode(p, INPUT_PULLUP);
      else if (op == "input") pinMode(p, INPUT);
      else { nack("op"); return; }
    }
    String use; pinUseJson(p, use);
    txLine(reply(true, "gpio", String("\"pin\":") + p + ",\"val\":" + digitalRead(p) + ",\"use\":\"" + use + "\""));
  } else if (cmd == "inputs") {
    String k = "\"keys\":[";
    for (int i = 0; i < 5; i++) { if (i) k += ','; k += digitalRead(PIN_KEY[i]) == LOW ? 1 : 0; }
    txLine(reply(true, "inputs", k + "],\"enc_sw\":" + (digitalRead(PIN_ENC_SW) == LOW ? 1 : 0) + ",\"enc_a\":" + digitalRead(PIN_ENC_A) +
                                  ",\"enc_b\":" + digitalRead(PIN_ENC_B) + ",\"enc_pos\":0"));
  } else if (cmd == "events") {
    eventsOn = jint(line, "val", 1) != 0;
    txLine(reply(true, "events"));
  } else if (cmd == "run") {
    runCmd(line);
  } else if (cmd == "reboot") {
    txLine(reply(true, "reboot"));
    delay(150);
    if (jstr(line, "mode") == "download") REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    esp_restart();
  } else if (cmd == "display" || cmd == "snapshot") {
    nack("no_display");
  } else {
    nack("unknown_cmd");
  }
  reqId = -1;
}

static void pollInputs() {
  uint32_t now = millis();
  for (int i = 0; i < 5; i++) {
    uint8_t v = digitalRead(PIN_KEY[i]);
    if (v != keyState[i] && now - keyChangeAt[i] > 10) {
      keyState[i] = v; keyChangeAt[i] = now;
      if (eventsOn && hostActive()) txLine(String("{\"evt\":\"key\",\"k\":") + (i + 1) + ",\"v\":" + (v == LOW ? 1 : 0) + "}");
    }
  }
  uint8_t sw = digitalRead(PIN_ENC_SW);
  if (sw != encSw && now - encSwChangeAt > 10) {
    encSw = sw; encSwChangeAt = now;
    if (eventsOn && hostActive()) txLine(String("{\"evt\":\"encsw\",\"v\":") + (sw == LOW ? 1 : 0) + "}");
  }
}

void setup() {
  ledBoot(24, 0, 24);                                    // purple: running
  for (int i = 0; i < 5; i++) pinMode(PIN_KEY[i], INPUT_PULLUP);
  pinMode(PIN_ENC_A, INPUT_PULLUP); pinMode(PIN_ENC_B, INPUT_PULLUP); pinMode(PIN_ENC_SW, INPUT_PULLUP);
  Serial.begin(115200);
#if DC_HAS_HID
  USB.productName("DeskCompanion core");
  Keyboard.begin();
  ConsumerControl.begin();
  USB.begin();
#endif
  rx.reserve(512);
  ledBoot(0, 0, 40);                                     // blue: USB serial is up
  delay(250);
  const uint8_t t[4][3] = {{60, 0, 0}, {0, 60, 0}, {0, 0, 60}, {40, 40, 40}};   // LED self-test: R, G, B, white
  for (int i = 0; i < 4; i++) { ledPixel(t[i][0], t[i][1], t[i][2]); delay(250); }
  ready = true;
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    lastRxMs = millis();
    if (c == '\n') { if (rx.length()) handle(rx); rx = ""; }
    else if (c != '\r' && rx.length() < 480) rx += c;
  }
  pollInputs();
  ledService();
  delay(1);
}
