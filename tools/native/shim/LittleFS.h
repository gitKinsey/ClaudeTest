#pragma once
#include "Arduino.h"
namespace fs {
class File {
 public:
  File() {}
  explicit File(FILE* f) : fp(std::shared_ptr<FILE>(f, [](FILE* p) { if (p) fclose(p); })) {}
  explicit operator bool() const { return (bool)fp; }
  size_t write(const uint8_t* b, size_t n) { return fp ? fwrite(b, 1, n, fp.get()) : 0; }
  size_t write(uint8_t c) { return write(&c, 1); }
  size_t read(uint8_t* b, size_t n) { return fp ? fread(b, 1, n, fp.get()) : 0; }
  int read() { uint8_t c; return read(&c, 1) == 1 ? c : -1; }
  size_t size() { if (!fp) return 0; long p = ftell(fp.get()); fseek(fp.get(), 0, SEEK_END); long e = ftell(fp.get()); fseek(fp.get(), p, SEEK_SET); return (size_t)e; }
  size_t position() { return fp ? (size_t)ftell(fp.get()) : 0; }
  bool seek(uint32_t pos) { return fp && fseek(fp.get(), pos, SEEK_SET) == 0; }
  int available() { return fp ? (int)(size() - position()) : 0; }
  void flush() { if (fp) fflush(fp.get()); }
  void close() { fp.reset(); }
 private:
  std::shared_ptr<FILE> fp;
};
}
class LittleFSClass {
 public:
  bool begin(bool format = false);
  bool exists(const char* p);
  bool remove(const char* p);
  bool rename(const char* a, const char* b);
  fs::File open(const char* p, const char* mode = "r");
  size_t totalBytes() { return 1441792; }
  size_t usedBytes();
 private:
  std::string path(const char* p);
};
extern LittleFSClass LittleFS;
