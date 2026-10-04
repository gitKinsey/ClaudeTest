#pragma once
#include "Arduino.h"
typedef struct { int32_t iPos, iSize; void* fHandle; } GIFFILE;
typedef struct { int iX, iY, y, iWidth; uint8_t* pPixels; uint16_t* pPalette; uint8_t ucDisposalMethod, ucTransparent, ucBackground, ucHasTransparency; } GIFDRAW;
typedef void* (*GIF_OPEN_CALLBACK)(const char*, int32_t*);
typedef void (*GIF_CLOSE_CALLBACK)(void*);
typedef int32_t (*GIF_READ_CALLBACK)(GIFFILE*, uint8_t*, int32_t);
typedef int32_t (*GIF_SEEK_CALLBACK)(GIFFILE*, int32_t);
typedef void (*GIF_DRAW_CALLBACK)(GIFDRAW*);
class AnimatedGIF {
 public:
  void begin(int) {}
  int open(const char* name, GIF_OPEN_CALLBACK o, GIF_CLOSE_CALLBACK c, GIF_READ_CALLBACK, GIF_SEEK_CALLBACK, GIF_DRAW_CALLBACK) {   // the host build cannot decode GIFs: a file that opens "plays" a blank 100 ms frame
    int32_t sz = 0; void* h = o(name, &sz); if (!h) return 0; c(h); return sz > 0 ? 1 : 0;
  }
  int playFrame(bool, int* d) { if (d) *d = 100; return 1; }
  void reset() {}
  void close() {}
  int getCanvasWidth() { return 240; }
  int getCanvasHeight() { return 240; }
};
