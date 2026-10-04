#pragma once
#include "Arduino.h"
struct USBClass { void begin() {} void productName(const char*) {} void manufacturerName(const char*) {} void serialNumber(const char*) {} };
extern USBClass USB;
// recorded HID output: the test driver reads "#hid ..." lines
extern const uint8_t KeyboardLayout_en_US[], KeyboardLayout_de_DE[], KeyboardLayout_fr_FR[], KeyboardLayout_es_ES[], KeyboardLayout_it_IT[], KeyboardLayout_pt_PT[], KeyboardLayout_pt_BR[], KeyboardLayout_sv_SE[], KeyboardLayout_da_DK[], KeyboardLayout_hu_HU[];
struct USBHIDKeyboard { void begin(const uint8_t* = nullptr) {} size_t press(uint8_t k); size_t write(uint8_t k); size_t release(uint8_t k); void releaseAll(); };
struct USBHIDConsumerControl { void begin() {} void press(uint16_t k); void release(); };
struct USBHIDMouse { void begin() {} void click(uint8_t b = 1); void press(uint8_t b = 1); void release(uint8_t b = 1); void move(int8_t x, int8_t y, int8_t wheel = 0, int8_t pan = 0); };
