#pragma once
#include "Arduino.h"
class Bounce {
 public:
  void attach(int pin, int mode = INPUT) { _pin = pin; _last = _cur = digitalRead(pin); }
  void interval(uint16_t) {}
  bool update() { _prev = _cur; _cur = digitalRead(_pin); return _prev != _cur; }
  int read() { return _cur; }
  bool fell() { return _prev == HIGH && _cur == LOW; }
  bool rose() { return _prev == LOW && _cur == HIGH; }
 private:
  int _pin = 0, _cur = 1, _prev = 1, _last = 1;
};
