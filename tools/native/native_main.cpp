// Host runtime for the native build of DeskCompanion.ino: Arduino-core stand-ins, a software TFT_eSprite, NVS / LittleFS on the host file system,
// and a stdin protocol: ordinary lines are the serial link, lines starting with '#' are controls (#key, #enc, #encsw, #pin, #quit); stdout carries the
// firmware's serial output plus "#..." event lines (led, bl, hid, frame, restart).  See tools/native/README.md.
#include "Arduino.h"
#include "esp_system.h"
#include "esp_ota_ops.h"
#include "driver/gpio.h"
#include "Preferences.h"
#include "LittleFS.h"
#include "USB.h"
#include "TFT_eSPI.h"
#include "../../build/font_data.h"
#undef min
#undef max
#include <sys/stat.h>
#include <unistd.h>
#include <dirent.h>
#include <cstdarg>

static std::mutex outMu;
static std::chrono::steady_clock::time_point T0 = std::chrono::steady_clock::now();
static uint8_t pinLevel[64];
static void (*encIsr)() = nullptr;
static int encPinA = 13;
static std::mutex rxMu;
static std::deque<uint8_t> rxq;
static std::deque<std::string> ctlq;
HWSerial Serial;
ESPClass ESP;
USBClass USB;
LittleFSClass LittleFS;

uint32_t millis() { return (uint32_t)std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - T0).count(); }
uint32_t micros() { return (uint32_t)std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - T0).count(); }
void delay(uint32_t ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
void delayMicroseconds(uint32_t us) { std::this_thread::sleep_for(std::chrono::microseconds(us)); }
long random(long hi) { return hi <= 0 ? 0 : (long)(esp_random() % (uint32_t)hi); }
long random(long lo, long hi) { return lo + random(hi - lo); }
void randomSeed(unsigned long s) { srand((unsigned)s); }
uint32_t esp_random() { static uint32_t x = 2463534242u ^ (uint32_t)time(nullptr); x ^= x << 13; x ^= x >> 17; x ^= x << 5; return x; }
void pinMode(uint8_t pin, uint8_t mode) { if (pin < 64 && mode == INPUT_PULLUP) pinLevel[pin] = HIGH; }
int digitalRead(uint8_t pin) { return pin < 64 ? pinLevel[pin] : 0; }
void digitalWrite(uint8_t pin, uint8_t v) { if (pin < 64) pinLevel[pin] = v; }
int gpio_get_level(gpio_num_t pin) { return pin >= 0 && pin < 64 ? pinLevel[pin] : 0; }
int digitalPinToInterrupt(int pin) { return pin; }
void attachInterrupt(int irq, void (*fn)(), int) { encIsr = fn; encPinA = irq; }
uint32_t analogReadMilliVolts(uint8_t) { return 0; }
float temperatureRead() { return 31.5f; }
void native_event(const char* fmt, ...) {
  char b[512]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
  std::lock_guard<std::mutex> g(outMu); fprintf(stdout, "#%s\n", b); fflush(stdout);
}
void neopixelWrite(uint8_t, uint8_t r, uint8_t g, uint8_t b) { static uint32_t last = 0xFFFFFFFF; uint32_t v = (r << 16) | (g << 8) | b; if (v != last) { last = v; native_event("led %d %d %d", r, g, b); } }
void rgbLedWrite(uint8_t pin, uint8_t r, uint8_t g, uint8_t b) { neopixelWrite(pin, r, g, b); }
const uint8_t KeyboardLayout_en_US[] = {0}, KeyboardLayout_de_DE[] = {1}, KeyboardLayout_fr_FR[] = {2}, KeyboardLayout_es_ES[] = {3}, KeyboardLayout_it_IT[] = {4}, KeyboardLayout_pt_PT[] = {5}, KeyboardLayout_pt_BR[] = {6}, KeyboardLayout_sv_SE[] = {7}, KeyboardLayout_da_DK[] = {8}, KeyboardLayout_hu_HU[] = {9};
bool ledcAttach(uint8_t, uint32_t, uint8_t) { return true; }
bool ledcSetup(uint8_t, uint32_t, uint8_t) { return true; }
void ledcAttachPin(uint8_t, uint8_t) {}
void ledcWrite(uint8_t, uint32_t duty) { static uint32_t last = 0xFFFFFFFF; if (duty != last) { last = duty; native_event("bl %u", duty); } }
esp_reset_reason_t esp_reset_reason() { const char* r = getenv("DC_NATIVE_RESET"); return r && !strcmp(r, "sw") ? ESP_RST_SW : ESP_RST_POWERON; }
void esp_restart() { native_event("restart"); fflush(stdout); _exit(75); }
int xTaskCreatePinnedToCore(TaskFunction_t fn, const char*, uint32_t, void* arg, int, TaskHandle_t*, int) { std::thread([fn, arg]() { fn(arg); }).detach(); return 1; }
void vTaskDelete(TaskHandle_t) {}
static esp_partition_t part0 = {0, 0, 0x10000, 0x140000, "app0"};
const esp_partition_t* esp_ota_get_running_partition() { return &part0; }
const esp_partition_t* esp_ota_get_next_update_partition(const esp_partition_t*) { return nullptr; }
int esp_ota_get_partition_description(const esp_partition_t*, esp_app_desc_t* d) { memset(d, 0, sizeof *d); strcpy(d->version, "native"); return ESP_OK; }
int esp_ota_set_boot_partition(const esp_partition_t*) { return -1; }

// ---- serial
int HWSerial::available() { std::lock_guard<std::mutex> g(rxMu); return (int)rxq.size(); }
int HWSerial::read() { std::lock_guard<std::mutex> g(rxMu); if (rxq.empty()) return -1; int c = rxq.front(); rxq.pop_front(); return c; }
size_t HWSerial::write(const uint8_t* p, size_t n) { std::lock_guard<std::mutex> g(outMu); fwrite(p, 1, n, stdout); fflush(stdout); return n; }

// ---- HID recording
size_t USBHIDKeyboard::press(uint8_t k) { native_event("hid kb press %u", k); return 1; }
size_t USBHIDKeyboard::write(uint8_t k) { native_event("hid kb write %u", k); return 1; }
size_t USBHIDKeyboard::release(uint8_t k) { native_event("hid kb release %u", k); return 1; }
void USBHIDKeyboard::releaseAll() { native_event("hid kb releaseall"); }
void USBHIDConsumerControl::press(uint16_t k) { native_event("hid cc press %u", k); }
void USBHIDConsumerControl::release() { native_event("hid cc release"); }
void USBHIDMouse::click(uint8_t b) { native_event("hid ms click %u", b); }
void USBHIDMouse::press(uint8_t b) { native_event("hid ms press %u", b); }
void USBHIDMouse::release(uint8_t b) { native_event("hid ms release %u", b); }
void USBHIDMouse::move(int8_t x, int8_t y, int8_t w, int8_t p) { native_event("hid ms move %d %d %d %d", x, y, w, p); }

// ---- NVS
static std::map<std::string, std::pair<char, std::string>> nvs;       // key -> (type, value)
static std::string nvsPath() { const char* p = getenv("DC_NATIVE_NVS"); return p ? p : "native_nvs.tsv"; }
static void nvsSave() {
  FILE* f = fopen((nvsPath() + ".tmp").c_str(), "w"); if (!f) return;
  for (auto& kv : nvs) { fprintf(f, "%s\t%c\t", kv.first.c_str(), kv.second.first); for (unsigned char c : kv.second.second) fprintf(f, "%02x", c); fprintf(f, "\n"); }
  fclose(f); rename((nvsPath() + ".tmp").c_str(), nvsPath().c_str());
}
static std::string unhex(const std::string& h) { std::string o; for (size_t i = 0; i + 1 < h.size(); i += 2) o += (char)strtol(h.substr(i, 2).c_str(), nullptr, 16); return o; }
bool Preferences::begin(const char*, bool) {
  nvs.clear(); FILE* f = fopen(nvsPath().c_str(), "r"); if (!f) return true;
  char line[16384];
  while (fgets(line, sizeof line, f)) {
    std::string l(line); while (!l.empty() && (l.back() == '\n' || l.back() == '\r')) l.pop_back();
    size_t a = l.find('\t'), b = l.find('\t', a + 1); if (a == std::string::npos || b == std::string::npos) continue;
    nvs[l.substr(0, a)] = {l[a + 1], unhex(l.substr(b + 1))};
  }
  fclose(f); return true;
}
bool Preferences::clear() { nvs.clear(); nvsSave(); return true; }
bool Preferences::remove(const char* k) { bool r = nvs.erase(k) > 0; if (r) nvsSave(); return r; }
bool Preferences::isKey(const char* k) { return nvs.count(k) > 0; }
size_t Preferences::putNum(const char* k, char t, long long v) { nvs[k] = {t, std::to_string(v)}; nvsSave(); return 1; }
long long Preferences::getNum(const char* k, long long d) { auto it = nvs.find(k); return it == nvs.end() ? d : atoll(it->second.second.c_str()); }
size_t Preferences::putString(const char* k, const String& v) { nvs[k] = {'S', v.s}; nvsSave(); return v.length() ? v.length() : 1; }
String Preferences::getString(const char* k, const String& d) { auto it = nvs.find(k); return it == nvs.end() ? d : String(it->second.second); }
size_t Preferences::putBytes(const char* k, const void* p, size_t n) { nvs[k] = {'B', std::string((const char*)p, n)}; nvsSave(); return n; }
size_t Preferences::getBytes(const char* k, void* p, size_t n) { auto it = nvs.find(k); if (it == nvs.end()) return 0; size_t m = std::min(n, it->second.second.size()); memcpy(p, it->second.second.data(), m); return m; }

// ---- LittleFS on a host directory
static std::string fsRoot() { const char* p = getenv("DC_NATIVE_FS"); return p ? p : "native_fs"; }
std::string LittleFSClass::path(const char* p) { return fsRoot() + (p[0] == '/' ? "" : "/") + p; }
bool LittleFSClass::begin(bool) { mkdir(fsRoot().c_str(), 0755); return true; }
bool LittleFSClass::exists(const char* p) { struct stat st; return stat(path(p).c_str(), &st) == 0; }
bool LittleFSClass::remove(const char* p) { return ::remove(path(p).c_str()) == 0; }
bool LittleFSClass::rename(const char* a, const char* b) { return ::rename(path(a).c_str(), path(b).c_str()) == 0; }
fs::File LittleFSClass::open(const char* p, const char* mode) { FILE* f = fopen(path(p).c_str(), !strcmp(mode, "w") ? "wb" : !strcmp(mode, "a") ? "ab" : "rb"); return f ? fs::File(f) : fs::File(); }
size_t LittleFSClass::usedBytes() { size_t t = 0; DIR* d = opendir(fsRoot().c_str()); if (!d) return 0; while (auto* e = readdir(d)) { struct stat st; std::string q = fsRoot() + "/" + e->d_name; if (stat(q.c_str(), &st) == 0 && S_ISREG(st.st_mode)) t += (st.st_size + 4095) / 4096 * 4096; } closedir(d); return t; }

// ---- software sprite
#include <memory>
void* TFT_eSprite::createSprite(int16_t w, int16_t h) { W = w; H = h; buf.assign((size_t)w * h * 2, 0); cx0 = cy0 = 0; cx1 = w; cy1 = h; return buf.data(); }
void TFT_eSprite::px(int x, int y, uint16_t c) {
  x += vx; y += vy;
  if (x < cx0 || y < cy0 || x >= cx1 || y >= cy1) return;
  size_t i = ((size_t)y * W + x) * 2; buf[i] = c >> 8; buf[i + 1] = c & 0xFF;
}
void TFT_eSprite::drawPixel(int32_t x, int32_t y, uint32_t c) { px(x, y, (uint16_t)c); }
void TFT_eSprite::fillSprite(uint32_t c) { for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) { size_t i = ((size_t)y * W + x) * 2; buf[i] = (uint16_t)c >> 8; buf[i + 1] = c & 0xFF; } }
void TFT_eSprite::fillRect(int32_t x, int32_t y, int32_t w, int32_t h, uint32_t c) { for (int j = 0; j < h; j++) for (int i = 0; i < w; i++) px(x + i, y + j, (uint16_t)c); }
void TFT_eSprite::drawRect(int32_t x, int32_t y, int32_t w, int32_t h, uint32_t c) { drawFastHLine(x, y, w, c); drawFastHLine(x, y + h - 1, w, c); drawFastVLine(x, y, h, c); drawFastVLine(x + w - 1, y, h, c); }
void TFT_eSprite::drawFastHLine(int32_t x, int32_t y, int32_t w, uint32_t c) { for (int i = 0; i < w; i++) px(x + i, y, (uint16_t)c); }
void TFT_eSprite::drawFastVLine(int32_t x, int32_t y, int32_t h, uint32_t c) { for (int i = 0; i < h; i++) px(x, y + i, (uint16_t)c); }
void TFT_eSprite::drawLine(int32_t x0, int32_t y0, int32_t x1, int32_t y1, uint32_t c) {
  int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1, dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1, err = dx + dy;
  for (;;) { px(x0, y0, (uint16_t)c); if (x0 == x1 && y0 == y1) break; int e2 = 2 * err; if (e2 >= dy) { err += dy; x0 += sx; } if (e2 <= dx) { err += dx; y0 += sy; } }
}
static bool inRound(int x, int y, int w, int h, int r) {
  if (r <= 0) return true; r = std::min(r, std::min(w, h) / 2);
  int cx = x < r ? r : x >= w - r ? w - r - 1 : x, cy = y < r ? r : y >= h - r ? h - r - 1 : y;
  int dx = x - cx, dy = y - cy; return dx * dx + dy * dy <= r * r + r / 2;
}
void TFT_eSprite::fillRoundRect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t r, uint32_t c) { for (int j = 0; j < h; j++) for (int i = 0; i < w; i++) if (inRound(i, j, w, h, r)) px(x + i, y + j, (uint16_t)c); }
void TFT_eSprite::drawRoundRect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t r, uint32_t c) { for (int j = 0; j < h; j++) for (int i = 0; i < w; i++) if (inRound(i, j, w, h, r) && !(i > 0 && j > 0 && i < w - 1 && j < h - 1 && inRound(i - 1, j, w, h, r) && inRound(i + 1, j, w, h, r) && inRound(i, j - 1, w, h, r) && inRound(i, j + 1, w, h, r))) px(x + i, y + j, (uint16_t)c); }
void TFT_eSprite::fillCircle(int32_t x, int32_t y, int32_t r, uint32_t c) { for (int j = -r; j <= r; j++) for (int i = -r; i <= r; i++) if (i * i + j * j <= r * r + r / 2) px(x + i, y + j, (uint16_t)c); }
void TFT_eSprite::drawCircle(int32_t x, int32_t y, int32_t r, uint32_t c) { for (int j = -r - 1; j <= r + 1; j++) for (int i = -r - 1; i <= r + 1; i++) { int d = i * i + j * j; if (d <= r * r + r && d >= (r - 1) * (r - 1) + (r - 1) - 1 && d >= r * r - r) px(x + i, y + j, (uint16_t)c); } }
void TFT_eSprite::fillTriangle(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t x2, int32_t y2, uint32_t c) {
  int minx = x0 < x1 ? (x0 < x2 ? x0 : x2) : (x1 < x2 ? x1 : x2), maxx = x0 > x1 ? (x0 > x2 ? x0 : x2) : (x1 > x2 ? x1 : x2), miny = y0 < y1 ? (y0 < y2 ? y0 : y2) : (y1 < y2 ? y1 : y2), maxy = y0 > y1 ? (y0 > y2 ? y0 : y2) : (y1 > y2 ? y1 : y2);
  auto edge = [](int ax, int ay, int bx, int by, int x, int y) { return (long)(bx - ax) * (y - ay) - (long)(by - ay) * (x - ax); };
  for (int y = miny; y <= maxy; y++) for (int x = minx; x <= maxx; x++) { long a = edge(x0, y0, x1, y1, x, y), b = edge(x1, y1, x2, y2, x, y), d = edge(x2, y2, x0, y0, x, y); if ((a >= 0 && b >= 0 && d >= 0) || (a <= 0 && b <= 0 && d <= 0)) px(x, y, (uint16_t)c); }
}
static const NFont* nf(uint8_t font) { if (font >= 9 || !NFONTS[font].g) font = 2; return &NFONTS[font]; }
int16_t TFT_eSprite::textWidth(const char* s, uint8_t font) { const NFont* f = nf(font); int w = 0; for (; *s; s++) { int c = (unsigned char)*s; if (c < 32 || c > 126) c = '?'; w += f->g[c - 32].adv; } return (int16_t)w; }
int16_t TFT_eSprite::drawString(const char* s, int32_t x, int32_t y, uint8_t font) {
  const NFont* f = nf(font); int w = textWidth(s, font), h = f->px;
  int hx = datum % 3, vy2 = datum / 3;
  int left = hx == 0 ? x : hx == 1 ? x - w / 2 : x - w;
  int top = vy2 == 0 ? y : vy2 == 1 ? y - h / 2 : y - h;
  int cx = left;
  for (const char* p = s; *p; p++) {
    int c = (unsigned char)*p; if (c < 32 || c > 126) c = '?';
    const NGlyph& g = f->g[c - 32];
    for (int yy = 0; yy < g.h; yy++) for (int xx = 0; xx < g.w; xx++) if (f->bits[g.off + yy * g.w + xx]) px(cx + g.xo + xx, top + f->ascent + g.yo + yy, (uint16_t)fgc);
    cx += g.adv;
  }
  return (int16_t)w;
}
void TFT_eSprite::setViewport(int32_t x, int32_t y, int32_t w, int32_t h, bool) { vx = x; vy = y; cx0 = x; cy0 = y; cx1 = x + w; cy1 = y + h; }
void TFT_eSprite::resetViewport() { vx = vy = 0; cx0 = cy0 = 0; cx1 = W; cy1 = H; }
void TFT_eSprite::pushSprite(int32_t, int32_t) {}

// ---- main
static void readerThread() {
  std::string line; int c;
  while ((c = getchar()) != EOF) {
    if (c == '\n') {
      if (!line.empty() && line[0] == '#') { std::lock_guard<std::mutex> g(rxMu); ctlq.push_back(line); }
      else { std::lock_guard<std::mutex> g(rxMu); for (char ch : line) rxq.push_back((uint8_t)ch); rxq.push_back('\n'); }
      line.clear();
    } else line += (char)c;
  }
  std::lock_guard<std::mutex> g(rxMu); ctlq.push_back("#quit");
}
static void control(const std::string& l) {
  int a = 0, b = 0;
  if (!strncmp(l.c_str(), "#key ", 5) && sscanf(l.c_str() + 5, "%d %d", &a, &b) == 2) {       // #key <1..5> <0|1>: 1 = pressed (pin LOW)
    static const int PIN_KEY[5] = {1, 2, 4, 5, 6};
    if (a >= 1 && a <= 5) pinLevel[PIN_KEY[a - 1]] = b ? LOW : HIGH;
  } else if (!strncmp(l.c_str(), "#encsw ", 7) && sscanf(l.c_str() + 7, "%d", &a) == 1) pinLevel[15] = a ? LOW : HIGH;
  else if (!strncmp(l.c_str(), "#enc ", 5) && sscanf(l.c_str() + 5, "%d", &a) == 1) {          // #enc <detents>: positive = clockwise, 2 edges per detent
    int n = abs(a) * 2;
    for (int i = 0; i < n; i++) { pinLevel[13] ^= 1; pinLevel[14] = a > 0 ? !pinLevel[13] : pinLevel[13]; if (encIsr) encIsr(); delayMicroseconds(900); }
  } else if (!strncmp(l.c_str(), "#pin ", 5) && sscanf(l.c_str() + 5, "%d %d", &a, &b) == 2) { if (a >= 0 && a < 64) pinLevel[a] = (uint8_t)b; }
  else if (l == "#quit") { fflush(stdout); _exit(0); }
  else if (l == "#frame") {                              // dump the sprite as one hex line for the test driver
    extern TFT_eSprite spr; TFT_eSprite* s = &spr;
    std::lock_guard<std::mutex> g(outMu); fputs("#frame ", stdout); for (uint8_t c : s->buf) fprintf(stdout, "%02x", c); fputc('\n', stdout); fflush(stdout);
  }
}
extern void setup();
extern void loop();
int main() {
  for (int i = 0; i < 64; i++) pinLevel[i] = HIGH;
  pinLevel[15] = HIGH;
  setvbuf(stdout, nullptr, _IONBF, 0);
  std::thread(readerThread).detach();
  setup();
  for (;;) {
    std::string c; { std::lock_guard<std::mutex> g(rxMu); if (!ctlq.empty()) { c = ctlq.front(); ctlq.pop_front(); } }
    if (!c.empty()) control(c);
    loop();
  }
}
