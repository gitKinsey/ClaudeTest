#pragma once
#include "Arduino.h"
// Arduino-ESP32 3.x USBMSC: the native build keeps the callbacks so a test can read blocks through them ("#msc read <lba>" is answered by the driver command "#mscread")
struct USBMSC {
  typedef int32_t (*rd_t)(uint32_t lba, uint32_t offset, void* buffer, uint32_t bufsize);
  typedef int32_t (*wr_t)(uint32_t lba, uint32_t offset, uint8_t* buffer, uint32_t bufsize);
  typedef bool (*ss_t)(uint8_t power_condition, bool start, bool load_eject);
  void vendorID(const char*) {} void productID(const char*) {} void productRevision(const char*) {}
  void onRead(rd_t cb); void onWrite(wr_t cb); void onStartStop(ss_t cb); void mediaPresent(bool) {}
  bool begin(uint32_t block_count, uint16_t block_size);
};
