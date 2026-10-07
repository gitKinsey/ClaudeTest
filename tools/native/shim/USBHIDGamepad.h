#pragma once
#include "Arduino.h"
struct USBHIDGamepad { void begin() {} bool send(int8_t x, int8_t y, int8_t z, int8_t rz, int8_t rx, int8_t ry, uint8_t hat, uint32_t buttons); };
