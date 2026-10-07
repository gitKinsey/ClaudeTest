#pragma once
// TinyUSB device calls used by the sketch for remote wake-up; the test driver flips the state with "#usb 1" (suspended) / "#usb 0"
#include "Arduino.h"
extern bool native_usb_suspended;
inline bool tud_suspended() { return native_usb_suspended; }
inline bool tud_remote_wakeup() { native_event("usb wake"); native_usb_suspended = false; return true; }
