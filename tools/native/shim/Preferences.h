#pragma once
#include "Arduino.h"
class Preferences {
 public:
  bool begin(const char* ns, bool readOnly = false);
  void end() {}
  bool clear();
  bool remove(const char* k);
  bool isKey(const char* k);
  size_t freeEntries() { return 300; }
  size_t putUChar(const char* k, uint8_t v) { return putNum(k, 'c', (long long)v); }
  size_t putBool(const char* k, bool v) { return putNum(k, 'b', v ? 1 : 0); }
  size_t putUShort(const char* k, uint16_t v) { return putNum(k, 's', (long long)v); }
  size_t putInt(const char* k, int32_t v) { return putNum(k, 'i', (long long)v); }
  size_t putUInt(const char* k, uint32_t v) { return putNum(k, 'u', (long long)v); }
  size_t putULong(const char* k, uint32_t v) { return putNum(k, 'u', (long long)v); }
  size_t putString(const char* k, const String& v);
  size_t putString(const char* k, const char* v) { return putString(k, String(v)); }
  size_t putBytes(const char* k, const void* p, size_t n);
  uint8_t getUChar(const char* k, uint8_t d = 0) { return (uint8_t)getNum(k, d); }
  bool getBool(const char* k, bool d = false) { return getNum(k, d ? 1 : 0) != 0; }
  uint16_t getUShort(const char* k, uint16_t d = 0) { return (uint16_t)getNum(k, d); }
  int32_t getInt(const char* k, int32_t d = 0) { return (int32_t)getNum(k, d); }
  uint32_t getUInt(const char* k, uint32_t d = 0) { return (uint32_t)getNum(k, d); }
  uint32_t getULong(const char* k, uint32_t d = 0) { return (uint32_t)getNum(k, d); }
  String getString(const char* k, const String& d = String());
  size_t getBytes(const char* k, void* p, size_t n);
 private:
  size_t putNum(const char* k, char t, long long v);
  long long getNum(const char* k, long long d);
};
