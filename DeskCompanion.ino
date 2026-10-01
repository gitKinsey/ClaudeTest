/*
  DeskCompanion.ino - ESP32-S3 Zero macro pad firmware v1.0.0

  Hardware : ESP32-S3 Zero, GC9A01 1.28" round TFT (SPI), 5 keys, EC11 encoder
  USB      : native USB -> HID keyboard + HID consumer control + CDC serial (JSON lines)
  Works 100% standalone; the Python companion app is optional.

  Arduino IDE settings (see README.md):
    USB Mode = "USB-OTG (TinyUSB)", USB CDC On Boot = "Enabled",
    Partition Scheme = "Default 4MB with spiffs", ESP32 core >= 3.0.0
  Libraries: TFT_eSPI, AnimatedGIF, Bounce2, ArduinoJson (7.x)

  Key slots used by the "remap" command: 1..5 = K1..K5, 6 = encoder CW, 7 = encoder CCW
*/
#if !defined(ARDUINO_USB_MODE) || (ARDUINO_USB_MODE != 0)
#error "Arduino IDE: Tools > USB Mode must be 'USB-OTG (TinyUSB)'."
#endif
#if !ARDUINO_USB_CDC_ON_BOOT
#error "Arduino IDE: Tools > USB CDC On Boot must be 'Enabled'."
#endif

#include <Arduino.h>
#include <vector>
#include <time.h>
#include <sys/time.h>
#include <SPI.h>
#include <TFT_eSPI.h>
#include <Bounce2.h>
#include <ArduinoJson.h>
#include <AnimatedGIF.h>
#include <Preferences.h>
#include <LittleFS.h>
#include <WiFi.h>
#include "esp_sntp.h"
#include "USB.h"
#include "USBHIDKeyboard.h"
#include "USBHIDConsumerControl.h"

// ================================================================ types (kept above all functions)
enum StepType : uint8_t { ST_KEYS, ST_MEDIA, ST_TEXT, ST_DELAY };
struct Step { uint8_t t = 0; uint8_t n = 0; uint8_t keys[6] = {0, 0, 0, 0, 0, 0}; uint16_t val = 0; String text; };
struct KeyName { const char* name; uint8_t code; };
struct MediaName { const char* name; uint16_t code; };
struct FileOut { File f; void put(const uint8_t* b, size_t n) { f.write(b, n); } void tick() { yield(); } };
enum Mode : uint8_t { M_CLOCK = 1, M_POMO, M_MEDIA, M_TELEM, M_GIF };
enum PomoState : uint8_t { PS_IDLE, PS_RUN, PS_PAUSE, PS_DONE };

// ================================================================ pin map & tunables
static const uint8_t PIN_BLK = 7;
static const uint8_t PIN_KEY[5] = {1, 2, 4, 5, 6};            // K1..K5, active LOW
static const uint8_t PIN_ENC_A = 13, PIN_ENC_B = 14, PIN_ENC_SW = 15;

static const int      ENC_EDGES_PER_DETENT = 2;   // EC11 20-detent/20-pulse: 2 edges on A per click (use 4 for 40-edge types)
static const bool     ENC_INVERT = false;         // flip if clockwise feels like counter-clockwise
static const uint32_t ENC_HOLD_MS = 700;          // long press on encoder = next display mode
static const uint32_t MENU_TIMEOUT_MS = 4000;     // radial menu auto-close
static const int      VCC_SENSE_PIN = -1;         // optional ADC pin behind a divider (ESP32-S3 cannot measure its own VCC)
static const float    VCC_DIVIDER = 2.0f;
static const size_t   RX_MAX = 6000;              // longest accepted JSON line
static const char*    FW_VERSION = "1.0.0";
static const char*    MODE_NAME[6] = {"", "CLOCK", "FOCUS", "MEDIA", "SYSTEM", "GIF"};

static constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) { return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3); }
static const uint16_t C_BG = 0x0000, C_TXT = 0xFFFF;
static const uint16_t C_DIM = rgb(38, 42, 52), C_DIM2 = rgb(70, 76, 92), C_GRAY = rgb(140, 146, 160);
static const uint16_t C_ACC = rgb(0, 210, 255), C_ACC2 = rgb(255, 70, 170), C_OK = rgb(70, 220, 110);
static const uint16_t C_WARN = rgb(255, 176, 0), C_RED = rgb(255, 72, 72);

// ================================================================ globals
TFT_eSPI tft;
TFT_eSprite spr(&tft);
AnimatedGIF gif;
Preferences prefs;
USBHIDKeyboard Keyboard;
USBHIDConsumerControl ConsumerControl;
Bounce keyBtn[5];
Bounce encBtn;

uint8_t  mode = M_CLOCK, brightness = 200, osMac = 0, pomoMinutes = 25;
bool     needRedraw = true;
uint32_t lastRender = 0;
int      vol = 50;                       // local estimate (HID gives no read-back); host may correct it via {"cmd":"media"}
bool     muted = false, playing = false;
uint8_t  hostCpu = 0, hostRam = 0;
uint32_t lastStatsMs = 0;
int32_t  tzOff = 0;
bool     timeSynced = false;
String   wifiSsid, wifiPass;
bool     wifiBusy = false, ntpDone = false, ntpStarted = false;
uint32_t wifiStart = 0, wifiNextAt = 0, lastEpochSave = 0;

bool     menuOpen = false;
uint8_t  menuSel = 0, menuEdit = 0, menuMode = 1;
uint32_t menuTouched = 0;
uint8_t  savedBright = 200;

uint8_t  pomoState = PS_IDLE;
uint32_t pomoRemain = 25UL * 60000UL, pomoLast = 0, pomoDoneAt = 0;

bool     gifOpen = false, gifRestart = true, gifFailed = false;
uint32_t gifNextAt = 0;
int      gifOffX = 0, gifOffY = 0;
File     gifFile;

bool     uploading = false;
File     upFile;
uint32_t upSize = 0, upRx = 0, upCrc = 0, upExpect = 0, upLast = 0;
int      upSeq = 0;
uint8_t  chunkBuf[1100];
uint16_t upChunk = 768;                  // raw bytes per upload chunk the host may send (set in setup)

std::vector<Step> macro;
bool     macroRun = false;
size_t   macroIdx = 0, textPos = 0;
uint32_t macroWake = 0;

String   rxLine;
bool     rxOverflow = false;

volatile int32_t encAccum = 0;
volatile uint32_t encLastUs = 0;
portMUX_TYPE encMux = portMUX_INITIALIZER_UNLOCKED;
int32_t  encRem = 0;
uint32_t encDownAt = 0;
bool     encHeld = false, encLongDone = false;
time_t   lastClockSec = 0;

// ================================================================ backlight (PWM on GPIO7)
static void backlightInit() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_BLK, 5000, 8);
#else
  ledcSetup(0, 5000, 8);
  ledcAttachPin(PIN_BLK, 0);
#endif
}
static void backlightSet(uint8_t v) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_BLK, v);
#else
  ledcWrite(0, v);
#endif
}

// ================================================================ small utilities
static uint32_t crc32u(uint32_t crc, const uint8_t* p, size_t n) {   // zlib-compatible, chainable
  crc = ~crc;
  while (n--) { crc ^= *p++; for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u))); }
  return ~crc;
}
static int b64dec(const char* in, size_t n, uint8_t* out) {
  static int8_t T[256];
  static bool init = false;
  if (!init) {
    memset(T, -1, sizeof T);
    const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (int i = 0; i < 64; i++) T[(uint8_t)A[i]] = i;
    init = true;
  }
  int o = 0; uint32_t acc = 0; int bits = 0;
  for (size_t i = 0; i < n; i++) {
    if (in[i] == '=') break;
    int v = T[(uint8_t)in[i]];
    if (v < 0) return -1;
    acc = (acc << 6) | (uint32_t)v; bits += 6;
    if (bits >= 8) { bits -= 8; out[o++] = (acc >> bits) & 0xFF; }
  }
  return o;
}
static void localTm(struct tm& t) { time_t n = time(nullptr) + tzOff; gmtime_r(&n, &t); }
static void uiTouch() { menuTouched = millis(); }
static uint32_t gifFileSize() { File f = LittleFS.open("/anim.gif", "r"); if (!f) return 0; uint32_t s = f.size(); f.close(); return s; }
static uint32_t fsFreeBytes() { return (uint32_t)(LittleFS.totalBytes() - LittleFS.usedBytes()) + gifFileSize(); }

// ================================================================ JSON replies
static void sendDoc(JsonDocument& d) { serializeJson(d, Serial); Serial.write('\n'); }
static void ack(const char* evt) { JsonDocument d; d["ok"] = true; d["evt"] = evt; sendDoc(d); }
static void nack(const char* err) { JsonDocument d; d["ok"] = false; d["err"] = err; sendDoc(d); }

// ================================================================ HID: key names, media, macro engine
static const KeyName KEY_NAMES[] = {
  {"CTRL", 0x80}, {"CONTROL", 0x80}, {"SHIFT", 0x81}, {"ALT", 0x82}, {"OPT", 0x82}, {"OPTION", 0x82},
  {"GUI", 0x83}, {"WIN", 0x83}, {"CMD", 0x83}, {"META", 0x83}, {"SUPER", 0x83},
  {"ENTER", 176}, {"RETURN", 176}, {"ESC", 177}, {"ESCAPE", 177}, {"BACKSPACE", 178}, {"TAB", 179},
  {"SPACE", ' '}, {"CAPSLOCK", 193}, {"PRTSC", 206}, {"PRINTSCREEN", 206}, {"PAUSE", 208}, {"INSERT", 209},
  {"HOME", 210}, {"PGUP", 211}, {"PAGEUP", 211}, {"DELETE", 212}, {"DEL", 212}, {"END", 213},
  {"PGDN", 214}, {"PAGEDOWN", 214}, {"RIGHT", 215}, {"LEFT", 216}, {"DOWN", 217}, {"UP", 218}, {"MENU", 254}};
static const MediaName MEDIA_NAMES[] = {
  {"PLAY_PAUSE", 0xCD}, {"NEXT", 0xB5}, {"PREV", 0xB6}, {"STOP", 0xB7}, {"MUTE", 0xE2},
  {"VOL_UP", 0xE9}, {"VOL_DOWN", 0xEA}, {"FF", 0xB3}, {"REWIND", 0xB4}};

static bool resolveKey(const char* nm, uint8_t& code) {
  if (!nm || !*nm) return false;
  if (nm[1] == 0) {                                   // single printable character
    char c = nm[0];
    if (c >= 'A' && c <= 'Z') c += 32;
    code = (uint8_t)c;
    return c > 0 && c < 127;
  }
  String n(nm); n.toUpperCase();
  if (n == "PRIMARY" || n == "MOD") { code = osMac ? 0x83 : 0x80; return true; }   // Cmd on macOS, Ctrl elsewhere
  for (const KeyName& k : KEY_NAMES) if (n == k.name) { code = k.code; return true; }
  if (n[0] == 'F' && n.length() <= 3) {
    int f = n.substring(1).toInt();
    if (f >= 1 && f <= 12) { code = 193 + f; return true; }
    if (f >= 13 && f <= 24) { code = 240 + f - 13; return true; }
  }
  return false;
}
static bool resolveMedia(const char* nm, uint16_t& code) {
  if (!nm) return false;
  String n(nm); n.toUpperCase();
  for (const MediaName& m : MEDIA_NAMES) if (n == m.name) { code = m.code; return true; }
  return false;
}
static void sendMedia(uint16_t code) {
  ConsumerControl.press(code);
  delay(6);
  ConsumerControl.release();
  switch (code) {                                     // keep the local media/volume model in sync
    case 0xE9: vol = vol + 2 > 100 ? 100 : vol + 2; muted = false; break;
    case 0xEA: vol = vol - 2 < 0 ? 0 : vol - 2; muted = false; break;
    case 0xE2: muted = !muted; break;
    case 0xCD: playing = !playing; break;
    case 0xB7: playing = false; break;
  }
  needRedraw = true;
}

static bool parseKeys(JsonVariantConst arr, Step& s) {
  if (!arr.is<JsonArrayConst>()) return false;
  s.t = ST_KEYS; s.n = 0;
  for (JsonVariantConst v : arr.as<JsonArrayConst>()) {
    uint8_t c;
    if (s.n >= 6 || !resolveKey(v.as<const char*>(), c)) return false;
    s.keys[s.n++] = c;
  }
  return s.n > 0;
}
// spec = {"type":"combo|media|text|macro|none","val":...}
static bool parseSpec(JsonVariantConst spec, std::vector<Step>& out) {
  const char* type = spec["type"] | "none";
  JsonVariantConst val = spec["val"];
  if (!strcmp(type, "none")) return true;
  if (!strcmp(type, "combo")) { Step s; if (!parseKeys(val, s)) return false; out.push_back(s); return true; }
  if (!strcmp(type, "media")) {
    Step s; s.t = ST_MEDIA;
    if (!resolveMedia(val.as<const char*>(), s.val)) return false;
    out.push_back(s); return true;
  }
  if (!strcmp(type, "text")) { Step s; s.t = ST_TEXT; s.text = val.as<const char*>() ? val.as<const char*>() : ""; out.push_back(s); return true; }
  if (!strcmp(type, "macro")) {
    if (!val.is<JsonArrayConst>()) return false;
    for (JsonVariantConst o : val.as<JsonArrayConst>()) {
      if (out.size() >= 64) return false;
      Step s;
      if (!o["combo"].isNull()) { if (!parseKeys(o["combo"], s)) return false; }
      else if (!o["text"].isNull()) { s.t = ST_TEXT; s.text = o["text"].as<String>(); }
      else if (!o["delay"].isNull()) { s.t = ST_DELAY; s.val = (uint16_t)constrain(o["delay"].as<int>(), 0, 60000); }
      else if (!o["media"].isNull()) { s.t = ST_MEDIA; if (!resolveMedia(o["media"].as<const char*>(), s.val)) return false; }
      else return false;
      out.push_back(s);
    }
    return true;
  }
  return false;
}
static void macroStart(std::vector<Step>& steps) {
  if (steps.empty()) return;
  if (macroRun) {                                    // queue behind the running macro so fast encoder turns keep every step
    for (const Step& s : steps) if (s.t == ST_TEXT) return;       // text snippets are never stacked up
    if (macro.size() - macroIdx + steps.size() > 96) return;      // bounded backlog
    for (const Step& s : steps) macro.push_back(s);
    return;
  }
  macro.swap(steps); macroIdx = 0; textPos = 0; macroWake = millis(); macroRun = true;
}
static void macroTick() {
  while (macroRun) {
    if ((int32_t)(millis() - macroWake) < 0) return;
    if (macroIdx >= macro.size()) { macroRun = false; macro.clear(); return; }
    const Step& s = macro[macroIdx];
    switch (s.t) {
      case ST_KEYS:
        for (uint8_t i = 0; i < s.n; i++) Keyboard.press(s.keys[i]);
        delay(12);
        Keyboard.releaseAll();
        macroIdx++; macroWake = millis() + 8; break;
      case ST_MEDIA: sendMedia(s.val); macroIdx++; macroWake = millis() + 5; break;
      case ST_DELAY: macroIdx++; macroWake = millis() + s.val; break;
      case ST_TEXT:
        if (textPos < s.text.length()) {
          uint8_t c = (uint8_t)s.text[textPos++];
          if (c < 128 && c != '\r') Keyboard.write(c);
          macroWake = millis() + 4;
        }
        if (textPos >= s.text.length()) { textPos = 0; macroIdx++; }
        break;
    }
    if (macroIdx >= macro.size()) { macroRun = false; macro.clear(); }
  }
}

static const char* const DEFAULT_SLOT[7] = {
  R"({"type":"combo","val":["PRIMARY","c"]})",       // K1 copy
  R"({"type":"combo","val":["PRIMARY","v"]})",       // K2 paste
  R"({"type":"combo","val":["PRIMARY","z"]})",       // K3 undo
  R"({"type":"media","val":"PLAY_PAUSE"})",          // K4
  R"({"type":"media","val":"MUTE"})",                // K5
  R"({"type":"media","val":"VOL_UP"})",              // encoder CW
  R"({"type":"media","val":"VOL_DOWN"})"};           // encoder CCW

static void slotKey(uint8_t i, char* out) { snprintf(out, 4, "s%u", i); }
static void runSlot(uint8_t i) {
  char k[4]; slotKey(i, k);
  String js = prefs.getString(k, String(DEFAULT_SLOT[i]));
  JsonDocument d;
  if (deserializeJson(d, js)) deserializeJson(d, DEFAULT_SLOT[i]);
  std::vector<Step> steps;
  if (parseSpec(d.as<JsonVariantConst>(), steps)) macroStart(steps);
}

// ================================================================ drawing helpers (all draw into the 240x240 sprite)
static void arcBand(int cx, int cy, float ro, float ri, float a0, float a1, uint16_t col) {   // 0 deg = 12 o'clock, clockwise
  if (a1 <= a0) return;
  int n = (int)ceilf((a1 - a0) / 4.0f); if (n < 1) n = 1;
  float da = (a1 - a0) / n;
  for (int i = 0; i < n; i++) {
    float s = (a0 + i * da) * DEG_TO_RAD, e = (a0 + (i + 1) * da + 0.6f) * DEG_TO_RAD;
    float ss = sinf(s), cs = cosf(s), se = sinf(e), ce = cosf(e);
    int x0 = lroundf(cx + ro * ss), y0 = lroundf(cy - ro * cs), x1 = lroundf(cx + ro * se), y1 = lroundf(cy - ro * ce);
    int x2 = lroundf(cx + ri * se), y2 = lroundf(cy - ri * ce), x3 = lroundf(cx + ri * ss), y3 = lroundf(cy - ri * cs);
    spr.fillTriangle(x0, y0, x1, y1, x2, y2, col);
    spr.fillTriangle(x0, y0, x2, y2, x3, y3, col);
  }
}
static void gauge(int cx, int cy, float ro, float ri, float frac, uint16_t col) {   // 270 degree gauge, gap at the bottom
  arcBand(cx, cy, ro, ri, 225, 495, C_DIM);
  frac = constrain(frac, 0.0f, 1.0f);
  if (frac > 0.005f) arcBand(cx, cy, ro, ri, 225, 225 + 270 * frac, col);
}
static void thickLine(float x0, float y0, float x1, float y1, float w, uint16_t col) {
  float dx = x1 - x0, dy = y1 - y0, l = sqrtf(dx * dx + dy * dy);
  if (l < 0.5f) return;
  float nx = -dy / l * w * 0.5f, ny = dx / l * w * 0.5f;
  spr.fillTriangle(lroundf(x0 + nx), lroundf(y0 + ny), lroundf(x0 - nx), lroundf(y0 - ny), lroundf(x1 + nx), lroundf(y1 + ny), col);
  spr.fillTriangle(lroundf(x0 - nx), lroundf(y0 - ny), lroundf(x1 - nx), lroundf(y1 - ny), lroundf(x1 + nx), lroundf(y1 + ny), col);
}
static void hand(float deg, float len, float w, uint16_t col) {
  float a = deg * DEG_TO_RAD;
  thickLine(120 - len * 0.15f * sinf(a), 120 + len * 0.15f * cosf(a), 120 + len * sinf(a), 120 - len * cosf(a), w, col);
}
static void dimSprite() {                              // halve brightness of every pixel (sprite stores byte-swapped RGB565)
  uint16_t* p = (uint16_t*)spr.getPointer();
  if (!p) return;
  for (int i = 0; i < 240 * 240; i++) { uint16_t c = __builtin_bswap16(p[i]); c = (c >> 1) & 0x7BEF; p[i] = __builtin_bswap16(c); }
}

// ================================================================ scenes
static const char* const DOW[7] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
static const char* const MON[12] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};

static void sceneClock() {
  struct tm t; localTm(t);
  spr.fillSprite(C_BG);
  spr.drawCircle(120, 120, 119, C_DIM2);
  spr.drawCircle(120, 120, 118, C_DIM);
  for (int i = 0; i < 60; i++) {
    float a = i * 6 * DEG_TO_RAD; bool big = (i % 5 == 0);
    float r0 = big ? 98 : 107, r1 = 114;
    if (big) thickLine(120 + r0 * sinf(a), 120 - r0 * cosf(a), 120 + r1 * sinf(a), 120 - r1 * cosf(a), 3, C_ACC);
    else spr.drawLine(120 + r0 * sinf(a), 120 - r0 * cosf(a), 120 + r1 * sinf(a), 120 - r1 * cosf(a), C_DIM2);
  }
  char b[24];
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(C_GRAY);
  snprintf(b, sizeof b, "%s %02d %s", DOW[t.tm_wday], t.tm_mday, MON[t.tm_mon]);
  spr.drawString(b, 120, 78, 2);
  spr.fillCircle(120, 60, 3, timeSynced ? C_OK : C_WARN);      // green = synced (host/NTP), amber = free-running
  spr.setTextColor(C_TXT);
  snprintf(b, sizeof b, "%02d:%02d", t.tm_hour, t.tm_min);
  spr.drawString(b, 120, 166, 4);
  hand(((t.tm_hour % 12) + t.tm_min / 60.0f) * 30.0f, 52, 6, C_TXT);
  hand((t.tm_min + t.tm_sec / 60.0f) * 6.0f, 82, 4, C_ACC);
  hand(t.tm_sec * 6.0f, 94, 2, C_RED);
  spr.fillCircle(120, 120, 6, C_TXT);
  spr.fillCircle(120, 120, 3, C_RED);
}

static void scenePomo() {
  uint32_t total = pomoMinutes * 60000UL;
  uint32_t rem = (pomoState == PS_IDLE) ? total : pomoRemain;
  bool blink = (pomoState == PS_DONE) && ((millis() / 400) & 1);
  uint16_t col = pomoState == PS_RUN ? C_ACC : pomoState == PS_PAUSE ? C_WARN : pomoState == PS_DONE ? C_RED : C_OK;
  const char* label = pomoState == PS_RUN ? "FOCUS" : pomoState == PS_PAUSE ? "PAUSED" : pomoState == PS_DONE ? "DONE!" : "READY";
  spr.fillSprite(blink ? rgb(90, 0, 0) : C_BG);
  arcBand(120, 120, 116, 102, 0, 360, C_DIM);
  float f = total ? (float)rem / total : 0;
  if (f > 0.003f) arcBand(120, 120, 116, 102, 0, 360 * f, col);
  uint32_t s = (rem + 999) / 1000; char b[16];
  snprintf(b, sizeof b, "%02lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(col); spr.drawString(label, 120, 64, 2);
  spr.setTextColor(C_TXT); spr.drawString(b, 120, 112, 7);
  spr.setTextColor(C_GRAY);
  spr.drawString("K1 start/pause", 120, 158, 2);
  spr.drawString("K2 reset", 120, 176, 2);
  if (pomoState == PS_IDLE) spr.drawString("turn dial: minutes", 120, 196, 2);
}

static void sceneMedia() {
  spr.fillSprite(C_BG);
  gauge(120, 120, 116, 102, muted ? 0 : vol / 100.0f, C_ACC);
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(C_GRAY); spr.drawString("MEDIA", 120, 50, 2);
  if (playing) { spr.fillRect(97, 72, 16, 46, C_TXT); spr.fillRect(127, 72, 16, 46, C_TXT); }
  else spr.fillTriangle(100, 70, 100, 120, 146, 95, C_TXT);
  for (int b = 0; b < 7; b++) {                        // equalizer bars: animate while "playing"
    int h = playing ? 6 + (int)(16 * (0.5f + 0.5f * sinf(millis() / 140.0f + b * 1.3f))) : 4;
    spr.fillRect(78 + b * 12, 154 - h, 8, h, playing ? C_ACC2 : C_DIM2);
  }
  char b[16];
  if (muted) snprintf(b, sizeof b, "MUTED"); else snprintf(b, sizeof b, "%d%%", vol);
  spr.setTextColor(muted ? C_RED : C_TXT); spr.drawString(b, 120, 180, 4);
  spr.setTextColor(C_GRAY); spr.drawString("VOLUME", 120, 204, 2);
}

static void sceneTelemetry() {
  bool live = lastStatsMs && (millis() - lastStatsMs < 3500);
  float vcc = VCC_SENSE_PIN >= 0 ? analogReadMilliVolts(VCC_SENSE_PIN) * VCC_DIVIDER / 1000.0f : 0;
  float temp = temperatureRead();
  uint32_t heapTot = ESP.getHeapSize(), heapFree = ESP.getFreeHeap();
  spr.fillSprite(C_BG);
  gauge(120, 120, 116, 103, live ? hostCpu / 100.0f : 1.0f - (float)heapFree / heapTot, C_ACC);
  gauge(120, 120, 98, 85, live ? hostRam / 100.0f : constrain(temp / 80.0f, 0.0f, 1.0f), C_ACC2);
  char b[24];
  spr.setTextDatum(MC_DATUM);
  if (live) {
    spr.setTextColor(C_OK); spr.drawString("HOST LIVE", 120, 62, 2);
    spr.setTextColor(C_ACC); snprintf(b, sizeof b, "CPU %d%%", hostCpu); spr.drawString(b, 120, 98, 4);
    spr.setTextColor(C_ACC2); snprintf(b, sizeof b, "RAM %d%%", hostRam); spr.drawString(b, 120, 134, 4);
    spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%.0f C", temp); spr.drawString(b, 120, 170, 2);
  } else {
    uint32_t up = millis() / 1000;
    spr.setTextColor(C_GRAY); spr.drawString("STANDALONE", 120, 62, 2);
    snprintf(b, sizeof b, "%02lu:%02lu:%02lu", (unsigned long)(up / 3600), (unsigned long)((up / 60) % 60), (unsigned long)(up % 60));
    spr.setTextColor(C_TXT); spr.drawString(b, 120, 96, 4);
    spr.setTextColor(C_GRAY); spr.drawString("UPTIME", 120, 116, 2);
    snprintf(b, sizeof b, "%.1f C", temp); spr.setTextColor(C_ACC2); spr.drawString(b, 120, 142, 4);
    if (VCC_SENSE_PIN >= 0) snprintf(b, sizeof b, "VCC %.2f V", vcc); else snprintf(b, sizeof b, "HEAP %luK", (unsigned long)(heapFree / 1024));
    spr.setTextColor(C_GRAY); spr.drawString(b, 120, 172, 2);
  }
}

static void sceneGifMsg() {
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_WARN);
  spr.drawString(gifFailed ? "GIF ERROR" : "GIF", 120, 104, 4);
  spr.setTextColor(C_GRAY);
  spr.drawString("upload a .gif with", 120, 136, 2);
  spr.drawString("the companion app", 120, 154, 2);
}

static void sceneUpload() {
  spr.fillSprite(C_BG);
  float f = upSize ? (float)upRx / upSize : 0;
  gauge(120, 120, 116, 102, f, C_ACC2);
  char b[24];
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(C_GRAY); spr.drawString("UPLOADING GIF", 120, 84, 2);
  spr.setTextColor(C_TXT); snprintf(b, sizeof b, "%d%%", (int)(f * 100)); spr.drawString(b, 120, 120, 4);
  spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%lu KB", (unsigned long)(upRx / 1024)); spr.drawString(b, 120, 152, 2);
}

static void sceneMenu() {                              // radial overlay: BRIGHT (top) / VOL (right) / MODE (bottom) / EXIT (left)
  static const char* const LBL[4] = {"BRIGHT", "VOL", "MODE", "EXIT"};
  static const char* const FULL[4] = {"BRIGHTNESS", "VOLUME", "DISPLAY MODE", "EXIT"};
  dimSprite();
  spr.setTextDatum(MC_DATUM);
  for (int i = 0; i < 4; i++) {
    float c = i * 90.0f; bool sel = (i == menuSel);
    arcBand(120, 120, 118, 72, c - 42, c + 42, sel ? (menuEdit ? C_WARN : C_ACC) : C_DIM2);
    float a = c * DEG_TO_RAD;
    spr.setTextColor(sel ? C_BG : C_TXT);
    spr.drawString(LBL[i], 120 + lroundf(95 * sinf(a)), 120 - lroundf(95 * cosf(a)), 2);
  }
  spr.fillCircle(120, 120, 66, C_BG);
  spr.drawCircle(120, 120, 66, C_DIM2);
  char b[20];
  spr.setTextColor(C_GRAY); spr.drawString(FULL[menuSel], 120, 82, 2);
  spr.setTextColor(menuEdit ? C_WARN : C_TXT);
  switch (menuSel) {
    case 0: snprintf(b, sizeof b, "%d%%", brightness * 100 / 255); spr.drawString(b, 120, 118, 4); break;
    case 1: if (muted) snprintf(b, sizeof b, "MUTE"); else snprintf(b, sizeof b, "%d%%", vol); spr.drawString(b, 120, 118, 4); break;
    case 2: {
      uint8_t m = menuEdit ? menuMode : mode;
      snprintf(b, sizeof b, "%d / 5", m); spr.drawString(b, 120, 112, 4);
      spr.setTextColor(C_ACC); spr.drawString(MODE_NAME[m], 120, 136, 2);
      break;
    }
    default: spr.drawString("CLOSE", 120, 118, 4); break;
  }
  spr.setTextColor(C_GRAY);
  spr.drawString(menuEdit ? "CLICK = OK" : "CLICK = ENTER", 120, 158, 1);
}

static void renderScene() {
  switch (mode) {
    case M_CLOCK: sceneClock(); break;
    case M_POMO:  scenePomo(); break;
    case M_MEDIA: sceneMedia(); break;
    case M_TELEM: sceneTelemetry(); break;
    default:      sceneGifMsg(); break;
  }
}
static void renderFrame() {
  if (uploading) sceneUpload();
  else {
    if (menuOpen && mode == M_GIF) spr.fillSprite(C_BG); else renderScene();
    if (menuOpen) sceneMenu();
  }
  spr.pushSprite(0, 0);
}
static uint32_t renderInterval() {
  if (uploading || menuOpen) return 100;
  switch (mode) {
    case M_POMO:  return (pomoState == PS_RUN || pomoState == PS_DONE) ? 250 : 100000;
    case M_MEDIA: return playing ? 90 : 100000;
    case M_TELEM: return 500;
    case M_GIF:   return 1000;
    default:      return 100000;                       // clock redraws on second change
  }
}

// ---- DEMO-GIF-BEGIN ---- (writes a 16-colour "radar" GIF using uncompressed LZW; no encoder library needed)
static FileOut* gOut; static uint8_t gBlk[255]; static uint8_t gBl; static uint32_t gAcc; static uint8_t gNb;
static void lzwByte(uint8_t b) { gBlk[gBl++] = b; if (gBl == 255) { uint8_t l = 255; gOut->put(&l, 1); gOut->put(gBlk, 255); gBl = 0; } }
static void lzwEmit(uint16_t code) { gAcc |= (uint32_t)code << gNb; gNb += 5; while (gNb >= 8) { lzwByte(gAcc & 255); gAcc >>= 8; gNb -= 8; } }
static void lzwFlush() {
  if (gNb > 0) { lzwByte(gAcc & 255); gAcc = 0; gNb = 0; }
  if (gBl) { gOut->put(&gBl, 1); gOut->put(gBlk, gBl); gBl = 0; }
}
static uint8_t demoPix(int x, int y, int f, int F, int S) {
  const float c = (S - 1) * 0.5f, R = S * 0.5f;
  float dx = x - c, dy = y - c, r = sqrtf(dx * dx + dy * dy);
  if (r > R - 0.5f) return 0;
  if (r < 4.0f) return 13;
  float sw = 6.2831853f * f / F;
  float bx = c + R * 0.55f * sinf(sw * 2.0f), by = c - R * 0.55f * cosf(sw * 2.0f);
  if ((x - bx) * (x - bx) + (y - by) * (y - by) < 20.0f) return 14;
  if (fabsf(r - (R - 2)) < 1.1f || fabsf(r - R * 0.66f) < 0.8f || fabsf(r - R * 0.33f) < 0.8f) return 12;
  float ang = atan2f(dx, -dy); if (ang < 0) ang += 6.2831853f;
  float d = sw - ang; if (d < 0) d += 6.2831853f;
  if (d < 2.4f) { int lv = 11 - (int)(d * (10.0f / 2.4f)); return lv < 1 ? 1 : lv; }
  return 0;
}
static void demoGif(FileOut& o, int S, int F) {
  gOut = &o; gBl = 0; gAcc = 0; gNb = 0;
  uint8_t h[13] = {'G', 'I', 'F', '8', '9', 'a', (uint8_t)(S & 255), (uint8_t)(S >> 8), (uint8_t)(S & 255), (uint8_t)(S >> 8), 0xF3, 0, 0};
  o.put(h, 13);
  uint8_t pal[48] = {0};
  for (int i = 1; i <= 11; i++) { pal[i * 3] = 0; pal[i * 3 + 1] = 30 + i * 18; pal[i * 3 + 2] = 50 + i * 18; }
  pal[36] = 60; pal[37] = 66; pal[38] = 80;             // 12 ring grey
  pal[39] = 255; pal[40] = 255; pal[41] = 255;          // 13 white
  pal[42] = 255; pal[43] = 70; pal[44] = 170;           // 14 magenta blip
  o.put(pal, 48);
  static const uint8_t ns[19] = {0x21, 0xFF, 0x0B, 'N', 'E', 'T', 'S', 'C', 'A', 'P', 'E', '2', '.', '0', 0x03, 0x01, 0x00, 0x00, 0x00};
  o.put(ns, 19);
  for (int f = 0; f < F; f++) {
    uint8_t gce[8] = {0x21, 0xF9, 0x04, 0x04, 10, 0, 0, 0};           // 100 ms, disposal 1
    o.put(gce, 8);
    uint8_t id[11] = {0x2C, 0, 0, 0, 0, (uint8_t)(S & 255), (uint8_t)(S >> 8), (uint8_t)(S & 255), (uint8_t)(S >> 8), 0x00, 4};   // last byte: LZW min code size
    o.put(id, 11);
    lzwEmit(16);
    int run = 0;
    for (int y = 0; y < S; y++) {
      o.tick();
      for (int x = 0; x < S; x++) {
        lzwEmit(demoPix(x, y, f, F, S));
        if (++run == 12) { lzwEmit(16); run = 0; }                    // clear code keeps the code width at 5 bits
      }
    }
    lzwEmit(17);
    lzwFlush();
    uint8_t z = 0; o.put(&z, 1);
  }
  uint8_t tr = 0x3B; o.put(&tr, 1);
}
// ---- DEMO-GIF-END ----

static bool makeDemoGif() {
  FileOut o;
  o.f = LittleFS.open("/anim.tmp", "w");
  if (!o.f) return false;
  demoGif(o, 160, 10);
  o.f.close();
  LittleFS.remove("/anim.gif");
  return LittleFS.rename("/anim.tmp", "/anim.gif");
}

// ================================================================ GIF playback (AnimatedGIF + LittleFS, drawn straight to the panel)
static void* GIFOpenFile(const char* fname, int32_t* pSize) {
  gifFile = LittleFS.open(fname, "r");
  if (gifFile) { *pSize = gifFile.size(); return (void*)&gifFile; }
  return NULL;
}
static void GIFCloseFile(void* pHandle) { File* f = static_cast<File*>(pHandle); if (f) f->close(); }
static int32_t GIFReadFile(GIFFILE* pFile, uint8_t* pBuf, int32_t iLen) {
  int32_t n = iLen;
  File* f = static_cast<File*>(pFile->fHandle);
  if ((pFile->iSize - pFile->iPos) < iLen) n = pFile->iSize - pFile->iPos - 1;
  if (n <= 0) return 0;
  n = (int32_t)f->read(pBuf, n);
  pFile->iPos = f->position();
  return n;
}
static int32_t GIFSeekFile(GIFFILE* pFile, int32_t iPosition) {
  File* f = static_cast<File*>(pFile->fHandle);
  f->seek(iPosition);
  pFile->iPos = (int32_t)f->position();
  return pFile->iPos;
}
static void GIFDraw(GIFDRAW* pDraw) {
  static uint16_t line[320];
  int y = pDraw->iY + pDraw->y + gifOffY;
  int x0 = pDraw->iX + gifOffX, w = pDraw->iWidth;
  if (y < 0 || y >= 240 || x0 >= 240 || w < 1) return;
  if (x0 + w > 240) w = 240 - x0;
  uint8_t* s = pDraw->pPixels;
  uint16_t* pal = pDraw->pPalette;
  if (pDraw->ucDisposalMethod == 2) {                  // restore-to-background: opaque background instead of "transparent"
    for (int x = 0; x < w; x++) if (s[x] == pDraw->ucTransparent) s[x] = pDraw->ucBackground;
    pDraw->ucHasTransparency = 0;
  }
  if (pDraw->ucHasTransparency) {                      // draw runs of opaque pixels, skip transparent ones
    uint8_t tr = pDraw->ucTransparent;
    int x = 0;
    while (x < w) {
      int n = 0;
      while (x + n < w && s[x + n] != tr) { line[n] = pal[s[x + n]]; n++; }
      if (n) { tft.setAddrWindow(x0 + x, y, n, 1); tft.pushPixels(line, n); x += n; }
      while (x < w && s[x] == tr) x++;
    }
  } else {
    for (int x = 0; x < w; x++) line[x] = pal[s[x]];
    tft.setAddrWindow(x0, y, w, 1);
    tft.pushPixels(line, w);
  }
}
static void gifClose() { if (gifOpen) { gif.close(); gifOpen = false; } }
static void gifBegin() {
  gifFailed = false;
  if (!LittleFS.exists("/anim.gif")) {
    spr.fillSprite(C_BG); spr.setTextDatum(MC_DATUM); spr.setTextColor(C_TXT);
    spr.drawString("Preparing demo", 120, 110, 4); spr.drawString("animation...", 120, 140, 4);
    spr.pushSprite(0, 0);
    makeDemoGif();
  }
  tft.fillScreen(TFT_BLACK);
  if (gif.open("/anim.gif", GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw)) {
    gifOpen = true;
    gifOffX = (240 - gif.getCanvasWidth()) / 2; gifOffY = (240 - gif.getCanvasHeight()) / 2;
    if (gifOffX < 0) gifOffX = 0;
    if (gifOffY < 0) gifOffY = 0;
    gifNextAt = 0;
  } else { gifFailed = true; needRedraw = true; }
}
static void gifService() {
  if (gifRestart) { gifClose(); gifRestart = false; gifBegin(); }
  if (!gifOpen || (int32_t)(millis() - gifNextAt) < 0) return;
  int d = 0;
  tft.startWrite();
  int r = gif.playFrame(false, &d);
  tft.endWrite();
  if (r == 0) gif.reset();                              // finished the last frame: loop
  else if (r < 0) { gifClose(); gifFailed = true; needRedraw = true; return; }
  gifNextAt = millis() + (d < 10 ? 10 : d);
}

// ================================================================ modes, menu, pomodoro
static void setMode(uint8_t m) {
  if (m < 1 || m > 5) return;
  if (m != mode) prefs.putUChar("mode", m);
  mode = m; needRedraw = true;
  if (mode == M_GIF) gifRestart = true; else gifClose();
}
static void pomoToggle() {
  if (pomoState == PS_IDLE || pomoState == PS_DONE) { pomoRemain = pomoMinutes * 60000UL; pomoState = PS_RUN; prefs.putUChar("pomo", pomoMinutes); }
  else if (pomoState == PS_RUN) pomoState = PS_PAUSE;
  else pomoState = PS_RUN;
  pomoLast = millis(); needRedraw = true;
}
static void pomoReset() { pomoState = PS_IDLE; pomoRemain = pomoMinutes * 60000UL; needRedraw = true; }
static void pomoTick() {
  uint32_t now = millis();
  if (pomoState == PS_RUN) {
    uint32_t dt = now - pomoLast; pomoLast = now;
    if (dt >= pomoRemain) { pomoRemain = 0; pomoState = PS_DONE; pomoDoneAt = now; needRedraw = true; }
    else pomoRemain -= dt;
  } else if (pomoState == PS_DONE && now - pomoDoneAt > 15000) pomoReset();
}
static void menuCloseNow() {
  if (brightness != savedBright) { prefs.putUChar("bright", brightness); savedBright = brightness; }
  menuOpen = false; menuEdit = 0; needRedraw = true;
  if (mode == M_GIF) gifRestart = true;
}
static void menuTurn(int steps) {
  uiTouch();
  int n = steps < 0 ? -steps : steps, dir = steps > 0 ? 1 : -1;
  if (!menuEdit) { for (int i = 0; i < n; i++) menuSel = (menuSel + (dir > 0 ? 1 : 3)) % 4; }
  else if (menuEdit == 1) { int b = (int)brightness + steps * 8; brightness = (uint8_t)constrain(b, 5, 255); backlightSet(brightness); }
  else if (menuEdit == 2) { for (int i = 0; i < n; i++) sendMedia(dir > 0 ? 0xE9 : 0xEA); }
  else if (menuEdit == 3) { menuMode = (uint8_t)((((int)menuMode - 1 + steps) % 5 + 5) % 5 + 1); }
  needRedraw = true;
}
static void menuClick() {
  uiTouch();
  if (!menuEdit) {
    if (menuSel == 3) { menuCloseNow(); return; }
    menuEdit = menuSel + 1;
    if (menuEdit == 3) menuMode = mode;
  } else {
    if (menuEdit == 3 && menuMode != mode) setMode(menuMode);
    menuEdit = 0;
  }
  needRedraw = true;
}

// ================================================================ input handling
static void onKey(int i) {
  if (menuOpen) uiTouch();
  if (mode == M_POMO && i < 2) { if (i == 0) pomoToggle(); else pomoReset(); return; }   // K1/K2 = timer controls in focus mode
  runSlot(i);
}
static void onEncSteps(int steps) {
  if (menuOpen) { menuTurn(steps); return; }
  if (mode == M_POMO && pomoState == PS_IDLE) {
    pomoMinutes = (uint8_t)constrain((int)pomoMinutes + steps, 1, 90);
    pomoRemain = pomoMinutes * 60000UL; needRedraw = true; return;
  }
  int n = steps < 0 ? -steps : steps;
  for (int i = 0; i < n; i++) runSlot(steps > 0 ? 5 : 6);
}
static void onEncClick() {
  if (menuOpen) { menuClick(); return; }
  menuOpen = true; menuSel = 0; menuEdit = 0; menuMode = mode; savedBright = brightness; uiTouch(); needRedraw = true;
}
static void onEncLong() {
  if (menuOpen) { menuCloseNow(); return; }
  setMode(mode % 5 + 1);
}
static void IRAM_ATTR encISR() {
  uint32_t now = micros();
  if (now - encLastUs < 400) return;                    // contact-bounce guard
  encLastUs = now;
  encAccum += (digitalRead(PIN_ENC_A) != digitalRead(PIN_ENC_B)) ? 1 : -1;
}
static void inputsService() {
  for (int i = 0; i < 5; i++) { keyBtn[i].update(); if (keyBtn[i].fell()) onKey(i); }
  encBtn.update();
  if (encBtn.fell()) { encDownAt = millis(); encHeld = true; encLongDone = false; }
  if (encHeld && !encLongDone && millis() - encDownAt >= ENC_HOLD_MS) { encLongDone = true; onEncLong(); }
  if (encBtn.rose()) { if (encHeld && !encLongDone) onEncClick(); encHeld = false; }
  int32_t d;
  portENTER_CRITICAL(&encMux); d = encAccum; encAccum = 0; portEXIT_CRITICAL(&encMux);
  encRem += d;
  int steps = encRem / ENC_EDGES_PER_DETENT;
  encRem -= steps * ENC_EDGES_PER_DETENT;
  if (ENC_INVERT) steps = -steps;
  if (steps) onEncSteps(steps);
}

// ================================================================ Wi-Fi / NTP (only if credentials were sent via {"cmd":"wifi"})
static void onNtp(struct timeval*) { ntpDone = true; }
static void netService() {
  if (wifiSsid.isEmpty()) return;
  uint32_t now = millis();
  if (!wifiBusy) {
    if ((int32_t)(now - wifiNextAt) < 0) return;
    ntpDone = false; ntpStarted = false;
    WiFi.mode(WIFI_STA); WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
    wifiBusy = true; wifiStart = now; return;
  }
  if (WiFi.status() == WL_CONNECTED && !ntpStarted) {
    sntp_set_time_sync_notification_cb(onNtp);
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    ntpStarted = true;
  }
  if (ntpDone) { timeSynced = true; wifiBusy = false; WiFi.disconnect(true); WiFi.mode(WIFI_OFF); wifiNextAt = now + 6UL * 3600000UL; needRedraw = true; }
  else if (now - wifiStart > 20000) { wifiBusy = false; WiFi.disconnect(true); WiFi.mode(WIFI_OFF); wifiNextAt = now + 600000UL; }
}

// ================================================================ serial JSON protocol
static void uploadAbort() {
  if (!uploading) return;
  upFile.close(); LittleFS.remove("/anim.tmp"); uploading = false; gifRestart = true; needRedraw = true;
}
// ---- host keyboard layout: Keyboard.press('z') sends the key that is labelled 'z' on the chosen layout
struct LayoutDef { const char* name; const uint8_t* map; };
static const LayoutDef LAYOUTS[] = {
  {"en_US", KeyboardLayout_en_US}, {"de_DE", KeyboardLayout_de_DE}, {"fr_FR", KeyboardLayout_fr_FR},
  {"fr_CH", KeyboardLayout_fr_CH}, {"es_ES", KeyboardLayout_es_ES}, {"it_IT", KeyboardLayout_it_IT},
  {"pt_PT", KeyboardLayout_pt_PT}, {"pt_BR", KeyboardLayout_pt_BR}, {"sv_SE", KeyboardLayout_sv_SE},
  {"da_DK", KeyboardLayout_da_DK}, {"hu_HU", KeyboardLayout_hu_HU}, {"ja_JP", KeyboardLayout_ja_JP}};
static String kbLayout = "en_US";
static const uint8_t* layoutByName(const char* n) {
  for (const LayoutDef& l : LAYOUTS) if (!strcmp(l.name, n)) return l.map;
  return nullptr;
}

static void cmdHello() {
  JsonDocument d;
  d["ok"] = true; d["evt"] = "hello"; d["dev"] = "desk-companion"; d["fw"] = FW_VERSION;
  d["mode"] = mode; d["bright"] = brightness; d["os"] = osMac ? "mac" : "win";
  d["gif"] = LittleFS.exists("/anim.gif"); d["fs_free"] = fsFreeBytes(); d["fs_total"] = (uint32_t)LittleFS.totalBytes();
  d["synced"] = timeSynced; d["layout"] = kbLayout;
  sendDoc(d);
}
static void handleLine(const String& line) {
  JsonDocument doc;
  if (deserializeJson(doc, line)) { nack("json"); return; }
  const char* cmd = doc["cmd"] | "";

  if (!strcmp(cmd, "stats")) {                                  // no reply (1 Hz telemetry)
    hostCpu = (uint8_t)constrain(doc["cpu"].as<int>(), 0, 100);
    hostRam = (uint8_t)constrain(doc["ram"].as<int>(), 0, 100);
    lastStatsMs = millis();
    if (mode == M_TELEM) needRedraw = true;
  }
  else if (!strcmp(cmd, "hello")) cmdHello();
  else if (!strcmp(cmd, "remap")) {
    int key = doc["key"] | 0;
    if (key < 1 || key > 7) { nack("key"); return; }
    JsonDocument spec;
    spec["type"] = doc["type"]; spec["val"] = doc["val"];
    std::vector<Step> tmp;
    if (!parseSpec(spec.as<JsonVariantConst>(), tmp)) { nack("spec"); return; }
    String s; serializeJson(spec, s);
    if (s.length() > 3800) { nack("too_long"); return; }
    char k[4]; slotKey(key - 1, k);
    prefs.putString(k, s);
    ack("remap");
  }
  else if (!strcmp(cmd, "reset_keys")) { for (uint8_t i = 0; i < 7; i++) { char k[4]; slotKey(i, k); prefs.remove(k); } ack("reset_keys"); }
  else if (!strcmp(cmd, "brightness")) {
    brightness = (uint8_t)constrain(doc["val"].as<int>(), 5, 255); backlightSet(brightness);
    if (brightness != savedBright) { prefs.putUChar("bright", brightness); savedBright = brightness; }
    ack("brightness");
  }
  else if (!strcmp(cmd, "mode")) { setMode((uint8_t)doc["val"].as<int>()); ack("mode"); }
  else if (!strcmp(cmd, "os")) {
    osMac = !strcmp(doc["val"] | "win", "mac") ? 1 : 0; prefs.putUChar("os", osMac); ack("os");
  }
  else if (!strcmp(cmd, "layout")) {
    const char* n = doc["val"] | "en_US";
    const uint8_t* map = layoutByName(n);
    if (!map) { nack("layout"); return; }
    kbLayout = n; prefs.putString("kbl", kbLayout);
    Keyboard.releaseAll(); Keyboard.begin(map);
    ack("layout");
  }
  else if (!strcmp(cmd, "time")) {
    struct timeval tv = {(time_t)doc["epoch"].as<uint32_t>(), 0};
    settimeofday(&tv, nullptr);
    int32_t tz = doc["tz"] | 0;
    if (tz != tzOff) { tzOff = tz; prefs.putInt("tz", tzOff); }
    timeSynced = true; needRedraw = true; ack("time");
  }
  else if (!strcmp(cmd, "wifi")) {
    wifiSsid = doc["ssid"] | ""; wifiPass = doc["pass"] | "";
    prefs.putString("ssid", wifiSsid); prefs.putString("wpass", wifiPass);
    wifiNextAt = 0; ack("wifi");
  }
  else if (!strcmp(cmd, "media")) {                             // optional: host corrects the local volume/play model
    if (!doc["vol"].isNull()) vol = constrain(doc["vol"].as<int>(), 0, 100);
    if (!doc["playing"].isNull()) playing = doc["playing"].as<bool>();
    if (!doc["muted"].isNull()) muted = doc["muted"].as<bool>();
    needRedraw = true; ack("media");
  }
  else if (!strcmp(cmd, "gif_begin")) {
    uint32_t size = doc["size"] | 0u;
    uploadAbort();
    gifClose();                                                   // release the file handle before deleting the file
    LittleFS.remove("/anim.gif");
    uint32_t freeB = (uint32_t)(LittleFS.totalBytes() - LittleFS.usedBytes());
    if (size == 0 || size + 8192 > freeB) { gifRestart = true; nack("no_space"); return; }
    upFile = LittleFS.open("/anim.tmp", "w");
    if (!upFile) { nack("fs"); return; }
    upSize = size; upRx = 0; upCrc = 0; upExpect = doc["crc"] | 0u; upSeq = 0; uploading = true; upLast = millis(); needRedraw = true;
    JsonDocument r; r["ok"] = true; r["evt"] = "gif_ready"; r["chunk"] = upChunk; sendDoc(r);
  }
  else if (!strcmp(cmd, "gif_chunk")) {
    if (!uploading) { nack("no_upload"); return; }
    int seq = doc["seq"] | -1;
    const char* b = doc["data"] | "";
    size_t bl = strlen(b);
    if (seq != upSeq) { nack("seq"); return; }
    if (bl > 1400) { uploadAbort(); nack("chunk"); return; }
    int n = b64dec(b, bl, chunkBuf);
    if (n < 0) { uploadAbort(); nack("b64"); return; }
    if (upRx + n > upSize) { uploadAbort(); nack("overflow"); return; }
    if (upFile.write(chunkBuf, n) != (size_t)n) { uploadAbort(); nack("fs_write"); return; }
    upCrc = crc32u(upCrc, chunkBuf, n); upRx += n; upSeq++; upLast = millis();
    JsonDocument r; r["ok"] = true; r["evt"] = "gif_ack"; r["seq"] = seq; r["rx"] = upRx; sendDoc(r);
  }
  else if (!strcmp(cmd, "gif_end")) {
    if (!uploading) { nack("no_upload"); return; }
    upFile.close(); uploading = false; needRedraw = true;
    if (upRx != upSize || upCrc != upExpect) { LittleFS.remove("/anim.tmp"); gifRestart = true; nack("crc"); return; }
    LittleFS.remove("/anim.gif");
    LittleFS.rename("/anim.tmp", "/anim.gif");
    setMode(M_GIF); gifRestart = true; ack("gif_done");
  }
  else if (!strcmp(cmd, "gif_abort")) { uploadAbort(); ack("gif_abort"); }
  else if (!strcmp(cmd, "gif_delete")) { LittleFS.remove("/anim.gif"); gifRestart = true; ack("gif_delete"); }
  else nack("unknown_cmd");
}
static void serialService() {
  int guard = 0;
  while (Serial.available() && guard++ < 4096) {
    char c = (char)Serial.read();
    if (c == '\n') { if (!rxOverflow && rxLine.length()) handleLine(rxLine); rxLine = ""; rxOverflow = false; }
    else if (c != '\r') { if (rxLine.length() < RX_MAX) rxLine += c; else rxOverflow = true; }
  }
}

// ================================================================ setup / loop
static void loadSettings() {
  brightness = prefs.getUChar("bright", 200); if (brightness < 5) brightness = 5;
  savedBright = brightness;
  mode = prefs.getUChar("mode", M_CLOCK); if (mode < 1 || mode > 5) mode = M_CLOCK;
  osMac = prefs.getUChar("os", 0);
  kbLayout = prefs.getString("kbl", "en_US"); if (!layoutByName(kbLayout.c_str())) kbLayout = "en_US";
  pomoMinutes = prefs.getUChar("pomo", 25); if (pomoMinutes < 1 || pomoMinutes > 90) pomoMinutes = 25;
  pomoRemain = pomoMinutes * 60000UL;
  tzOff = prefs.getInt("tz", 0);
  wifiSsid = prefs.getString("ssid", ""); wifiPass = prefs.getString("wpass", "");
  uint32_t e = prefs.getULong("epoch", 0);                       // last known time: clock starts close even without host/NTP
  if (e > 1700000000UL) { struct timeval tv = {(time_t)e, 0}; settimeofday(&tv, nullptr); }
}

void setup() {
  for (int i = 0; i < 5; i++) { keyBtn[i].attach(PIN_KEY[i], INPUT_PULLUP); keyBtn[i].interval(8); }
  encBtn.attach(PIN_ENC_SW, INPUT_PULLUP); encBtn.interval(8);
  pinMode(PIN_ENC_A, INPUT_PULLUP); pinMode(PIN_ENC_B, INPUT_PULLUP);
  backlightInit(); backlightSet(0);

  // With "USB CDC On Boot" the core has already called Serial.begin() and USB.begin() before setup(), and the
  // HID classes register their interfaces in their global constructors - so USB descriptors (product name...)
  // can no longer be changed here; the app finds the pad by VID + a "hello" handshake instead.
  upChunk = (Serial.setRxBufferSize(8192) >= 4096) ? 768 : 128;  // core 2.0.x cannot grow the RX queue after boot -> small chunks
  Serial.begin(115200);
  Keyboard.begin();
  ConsumerControl.begin();
  USB.begin();                       // no-op when the core already started USB at boot
  rxLine.reserve(RX_MAX + 16);
  gif.begin(BIG_ENDIAN_PIXELS);      // TFT_eSPI::pushPixels() expects big-endian RGB565 (same as the library's TFT_eSPI example)

  prefs.begin("deskcomp", false);
  loadSettings();
  Keyboard.begin(layoutByName(kbLayout.c_str()));   // host keyboard layout saved on the pad
  LittleFS.begin(true);

  tft.init();
  tft.fillScreen(TFT_BLACK);
  spr.setColorDepth(16);
  if (!spr.createSprite(240, 240)) {
    tft.setTextColor(TFT_RED); tft.drawString("Out of RAM", 60, 110, 4);
    backlightSet(brightness);
    while (true) delay(1000);
  }
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encISR, CHANGE);
  if (mode == M_GIF) gifRestart = true;
  else { renderFrame(); }
  backlightSet(brightness);
}

void loop() {
  serialService();
  inputsService();
  macroTick();
  pomoTick();
  netService();
  uint32_t now = millis();

  if (uploading && now - upLast > 6000) uploadAbort();           // host vanished mid-upload
  if (menuOpen && now - menuTouched > MENU_TIMEOUT_MS) menuCloseNow();
  if (timeSynced && now - lastEpochSave > 1800000UL) { lastEpochSave = now; prefs.putULong("epoch", (uint32_t)time(nullptr)); }
  if (mode == M_CLOCK && !menuOpen && !uploading) {
    time_t s = time(nullptr) + tzOff;
    if (s != lastClockSec) { lastClockSec = s; needRedraw = true; }
  }

  bool gifMode = (mode == M_GIF && !menuOpen && !uploading);
  if (gifMode) gifService();
  if (!(gifMode && gifOpen)) {
    if (needRedraw || now - lastRender >= renderInterval()) { needRedraw = false; lastRender = now; renderFrame(); }
  }
  delay(1);
}
