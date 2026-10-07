#pragma once
#include "Arduino.h"
#define TFT_BLACK 0x0000
#define TFT_WHITE 0xFFFF
#define TFT_RED 0xF800
#define TFT_GREEN 0x07E0
#define TFT_BLUE 0x001F
#define TL_DATUM 0
#define TC_DATUM 1
#define TR_DATUM 2
#define ML_DATUM 3
#define MC_DATUM 4
#define MR_DATUM 5
#define BL_DATUM 6
#define BC_DATUM 7
#define BR_DATUM 8
#define GC9A01_DRIVER 1
class TFT_eSPI {
 public:
  void init() {}
  void setRotation(uint8_t) {}
  void fillScreen(uint16_t) {}
  void setAddrWindow(int32_t, int32_t, int32_t, int32_t) {}
  void pushPixels(const void*, uint32_t) {}
  void startWrite() {}
  void endWrite() {}
};
class TFT_eSprite {
 public:
  TFT_eSprite(TFT_eSPI*) {}
  void* createSprite(int16_t w, int16_t h);
  void setColorDepth(int8_t) {}
  void* getPointer() { return buf.data(); }
  void fillSprite(uint32_t c);
  void fillRect(int32_t x, int32_t y, int32_t w, int32_t h, uint32_t c);
  void drawRect(int32_t x, int32_t y, int32_t w, int32_t h, uint32_t c);
  void fillRoundRect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t r, uint32_t c);
  void drawRoundRect(int32_t x, int32_t y, int32_t w, int32_t h, int32_t r, uint32_t c);
  void fillCircle(int32_t x, int32_t y, int32_t r, uint32_t c);
  void drawCircle(int32_t x, int32_t y, int32_t r, uint32_t c);
  void fillTriangle(int32_t x0, int32_t y0, int32_t x1, int32_t y1, int32_t x2, int32_t y2, uint32_t c);
  void drawLine(int32_t x0, int32_t y0, int32_t x1, int32_t y1, uint32_t c);
  void drawFastHLine(int32_t x, int32_t y, int32_t w, uint32_t c);
  void drawFastVLine(int32_t x, int32_t y, int32_t h, uint32_t c);
  void drawPixel(int32_t x, int32_t y, uint32_t c);
  uint16_t readPixel(int32_t x, int32_t y) { if (x < 0 || y < 0 || x >= W || y >= H) return 0; size_t i = ((size_t)y * W + x) * 2; return (uint16_t)(buf[i] << 8 | buf[i + 1]); }
  void setTextColor(uint32_t fg) { fgc = fg; }
  void setTextColor(uint32_t fg, uint32_t) { fgc = fg; }
  void setTextDatum(uint8_t d) { datum = d; }
  int16_t drawString(const char* s, int32_t x, int32_t y, uint8_t font = 1);
  int16_t drawString(const String& s, int32_t x, int32_t y, uint8_t font = 1) { return drawString(s.c_str(), x, y, font); }
  int16_t textWidth(const char* s, uint8_t font = 1);
  int16_t textWidth(const String& s, uint8_t font = 1) { return textWidth(s.c_str(), font); }
  void setViewport(int32_t x, int32_t y, int32_t w, int32_t h, bool vpDatum = true);
  void resetViewport();
  void pushSprite(int32_t, int32_t);
  void setSwapBytes(bool) {}
  std::vector<uint8_t> buf;
  int W = 0, H = 0;
 private:
  uint32_t fgc = 0xFFFF; uint8_t datum = 0;
  int vx = 0, vy = 0, cx0 = 0, cy0 = 0, cx1 = 0, cy1 = 0;
  void px(int x, int y, uint16_t c);
};
