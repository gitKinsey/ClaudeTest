#pragma once
#include "Arduino.h"
// Arduino-ESP32 3.x USBMIDI: the native build records every message as "#midi ..." for the test driver
struct USBMIDI { void begin() {} void noteOn(uint8_t note, uint8_t velocity = 0, uint8_t channel = 1); void noteOff(uint8_t note, uint8_t velocity = 0, uint8_t channel = 1); void controlChange(uint8_t control, uint8_t value, uint8_t channel = 1); };
