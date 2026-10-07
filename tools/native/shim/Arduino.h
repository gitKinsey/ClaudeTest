// Host (Linux) stand-ins for the Arduino-ESP32 core, just enough to compile and RUN DeskCompanion.ino natively (tools/native). Not a faithful ESP32 model:
// timing is the host's, there is no real USB / display; keys, encoder and the serial link are driven from stdin (see native_main.cpp).
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <cmath>
#include <ctime>
#include <string>
#include <vector>
#include <map>
#include <algorithm>
#include <functional>
#include <mutex>
#include <thread>
#include <atomic>
#include <chrono>
#include <deque>
#include <memory>
#include <initializer_list>
#include <sys/time.h>

#ifndef ARDUINO_USB_CDC_ON_BOOT
#define ARDUINO_USB_CDC_ON_BOOT 1
#endif
#ifndef ARDUINO_USB_MODE
#define ARDUINO_USB_MODE 0
#endif
#define ESP_ARDUINO_VERSION_VAL(a, b, c) (((a) << 16) | ((b) << 8) | (c))
#define ESP_ARDUINO_VERSION ESP_ARDUINO_VERSION_VAL(3, 3, 6)
#define ESP_ARDUINO_VERSION_MAJOR 3
#define ESP_ARDUINO_VERSION_MINOR 3
#define ESP_ARDUINO_VERSION_PATCH 6
#define IRAM_ATTR
#define RTC_DATA_ATTR
#define ARDUINO_RUNNING_CORE 1
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define CHANGE 1
#define BIG_ENDIAN_PIXELS 1
#define PROGMEM
#define DEG_TO_RAD 0.017453292519943295769236907684886
#define PI 3.1415926535897932384626433832795
#ifndef constrain
#define constrain(a, lo, hi) ((a) < (lo) ? (lo) : ((a) > (hi) ? (hi) : (a)))
#endif
using std::min;
using std::max;
using std::isnan;
#define RTC_NOINIT_ATTR __attribute__((section("rtcmem")))      // the native harness keeps this section across restarts / panics (see native_main.cpp)
typedef uint8_t byte;
typedef bool boolean;

uint32_t millis();
uint32_t micros();
void delay(uint32_t ms);
void delayMicroseconds(uint32_t us);
long random(long hi);
long random(long lo, long hi);
void randomSeed(unsigned long s);
uint32_t esp_random();
void pinMode(uint8_t pin, uint8_t mode);
int digitalRead(uint8_t pin);
void digitalWrite(uint8_t pin, uint8_t v);
int digitalPinToInterrupt(int pin);
void attachInterrupt(int irq, void (*fn)(), int mode);
uint32_t analogReadMilliVolts(uint8_t pin);
float temperatureRead();
void neopixelWrite(uint8_t pin, uint8_t r, uint8_t g, uint8_t b);
void rgbLedWrite(uint8_t pin, uint8_t r, uint8_t g, uint8_t b);
bool ledcAttach(uint8_t pin, uint32_t freq, uint8_t res);
bool ledcSetup(uint8_t ch, uint32_t freq, uint8_t res);
void ledcAttachPin(uint8_t pin, uint8_t ch);
void ledcWrite(uint8_t pin, uint32_t duty);
void native_event(const char* fmt, ...);          // a line starting with '#' on stdout for the test driver

class String {
 public:
  std::string s;
  String() {}
  String(const char* c) : s(c ? c : "") {}
  String(const std::string& c) : s(c) {}
  String(char c) : s(1, c) {}
  String(int v) : s(std::to_string(v)) {}
  String(unsigned v) : s(std::to_string(v)) {}
  String(long v) : s(std::to_string(v)) {}
  String(unsigned long v) : s(std::to_string(v)) {}
  String(long long v) : s(std::to_string(v)) {}
  String(unsigned long long v) : s(std::to_string(v)) {}
  String(float v, int dec = 2) { char b[40]; snprintf(b, sizeof b, "%.*f", dec, (double)v); s = b; }
  String(double v, int dec = 2) { char b[40]; snprintf(b, sizeof b, "%.*f", dec, v); s = b; }
  const char* c_str() const { return s.c_str(); }
  size_t length() const { return s.size(); }
  char operator[](size_t i) const { return i < s.size() ? s[i] : 0; }
  char& operator[](size_t i) { return s[i]; }
  String& operator+=(const String& o) { s += o.s; return *this; }
  String& operator+=(const char* o) { s += o; return *this; }
  String& operator+=(char c) { s += c; return *this; }
  String& operator+=(int v) { s += std::to_string(v); return *this; }
  String& operator+=(unsigned v) { s += std::to_string(v); return *this; }
  String& operator+=(long v) { s += std::to_string(v); return *this; }
  String& operator+=(unsigned long v) { s += std::to_string(v); return *this; }
  bool concat(const char* o) { s += o; return true; }
  bool concat(char c) { s += c; return true; }
  String operator+(const String& o) const { return String(s + o.s); }
  String operator+(const char* o) const { return String(s + o); }
  String operator+(char c) const { return String(s + c); }
  String operator+(int v) const { return String(s + std::to_string(v)); }
  String operator+(unsigned v) const { return String(s + std::to_string(v)); }
  String operator+(long v) const { return String(s + std::to_string(v)); }
  String operator+(unsigned long v) const { return String(s + std::to_string(v)); }
  bool operator==(const String& o) const { return s == o.s; }
  bool operator==(const char* o) const { return s == o; }
  bool operator!=(const String& o) const { return s != o.s; }
  bool operator!=(const char* o) const { return s != o; }
  bool operator<(const String& o) const { return s < o.s; }
  explicit operator bool() const { return !s.empty(); }
  int indexOf(char c, size_t from = 0) const { size_t p = s.find(c, from); return p == std::string::npos ? -1 : (int)p; }
  int indexOf(const char* c, size_t from = 0) const { size_t p = s.find(c, from); return p == std::string::npos ? -1 : (int)p; }
  int indexOf(const String& c, size_t from = 0) const { return indexOf(c.c_str(), from); }
  int lastIndexOf(char c) const { size_t p = s.rfind(c); return p == std::string::npos ? -1 : (int)p; }
  String substring(size_t a) const { return a >= s.size() ? String() : String(s.substr(a)); }
  String substring(size_t a, size_t b) const { return a >= s.size() || b <= a ? String() : String(s.substr(a, b - a)); }
  void trim() { size_t a = s.find_first_not_of(" \t\r\n"); if (a == std::string::npos) { s.clear(); return; } size_t b = s.find_last_not_of(" \t\r\n"); s = s.substr(a, b - a + 1); }
  void toUpperCase() { for (auto& c : s) c = (char)toupper((unsigned char)c); }
  void toLowerCase() { for (auto& c : s) c = (char)tolower((unsigned char)c); }
  bool startsWith(const String& o) const { return s.rfind(o.s, 0) == 0; }
  bool endsWith(const String& o) const { return s.size() >= o.s.size() && s.compare(s.size() - o.s.size(), o.s.size(), o.s) == 0; }
  bool equals(const String& o) const { return s == o.s; }
  bool equalsIgnoreCase(const String& o) const { if (s.size() != o.s.size()) return false; for (size_t i = 0; i < s.size(); i++) if (tolower((unsigned char)s[i]) != tolower((unsigned char)o.s[i])) return false; return true; }
  long toInt() const { return atol(s.c_str()); }
  float toFloat() const { return (float)atof(s.c_str()); }
  void remove(size_t idx) { if (idx < s.size()) s.erase(idx); }
  void remove(size_t idx, size_t n) { if (idx < s.size()) s.erase(idx, n); }
  void replace(const String& a, const String& b) { if (a.s.empty()) return; size_t p = 0; while ((p = s.find(a.s, p)) != std::string::npos) { s.replace(p, a.s.size(), b.s); p += b.s.size(); } }
  void clear() { s.clear(); }
  bool reserve(size_t n) { s.reserve(n); return true; }
  operator std::string() const { return s; }
  size_t write(uint8_t c) { s += (char)c; return 1; }
  size_t write(const uint8_t* p, size_t n) { s.append((const char*)p, n); return n; }
};
inline String operator+(const char* a, const String& b) { return String(std::string(a) + b.s); }

class HWSerial {
 public:
  void begin(unsigned long = 115200) {}
  size_t setRxBufferSize(size_t n) { return n; }
  int available();
  int read();
  size_t write(const uint8_t* p, size_t n);
  size_t write(uint8_t c) { return write(&c, 1); }
  int availableForWrite() { return 4096; }
  void flush() {}
  explicit operator bool() const { return true; }
};
extern HWSerial Serial;

typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
typedef void (*TaskFunction_t)(void*);
typedef void* TaskHandle_t;
#define pdPASS 1
int xTaskCreatePinnedToCore(TaskFunction_t fn, const char* name, uint32_t stack, void* arg, int prio, TaskHandle_t* h, int core);
void vTaskDelete(TaskHandle_t h);

struct ESPClass {
  const char* getChipModel() { return "ESP32-S3 (native)"; }
  int getChipRevision() { return 0; }
  int getChipCores() { return 2; }
  int getCpuFreqMHz() { return 240; }
  uint32_t getFlashChipSize() { return 4194304; }
  uint32_t getFreeHeap() { return 210000; }
  uint32_t getMinFreeHeap() { return 190000; }
  uint32_t getMaxAllocHeap() { return 110000; }
  uint32_t getHeapSize() { return 300000; }
  uint32_t getPsramSize() { return 0; }
};
extern ESPClass ESP;
