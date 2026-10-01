// User_Setup.h for TFT_eSPI  -  GC9A01 1.28" round IPS (240x240) on ESP32-S3 Zero
// Install: replace  <Arduino>/libraries/TFT_eSPI/User_Setup.h  with this file
//          (User_Setup_Select.h must keep its default line  #include <User_Setup.h>).

#define USER_SETUP_ID 9001

// ---- Driver / geometry -------------------------------------------------------------------
#define GC9A01_DRIVER
#define TFT_WIDTH  240
#define TFT_HEIGHT 240

// Colour fixes (only if your module looks wrong):
//   red<->blue swapped : uncomment TFT_RGB_ORDER TFT_BGR
//   colours inverted   : uncomment TFT_INVERSION_ON  (or _OFF, whichever fixes it)
// #define TFT_RGB_ORDER TFT_BGR
// #define TFT_INVERSION_ON
// #define TFT_INVERSION_OFF

// ---- Pins (ESP32-S3 Zero) -----------------------------------------------------------------
#define TFT_MISO -1          // display is write-only
#define TFT_MOSI 11          // SDA
#define TFT_SCLK 12          // SCL / SCLK
#define TFT_CS    8
#define TFT_DC    9
#define TFT_RST  10
// Backlight (BLK -> GPIO 7) is deliberately NOT defined here: the firmware drives it with its own
// LEDC PWM channel for the brightness menu. Defining TFT_BL would make TFT_eSPI fight for the pin.
// #define TFT_BL 7

// If the screen stays black on some core / TFT_eSPI combinations, try enabling this:
// #define USE_HSPI_PORT

// ---- Fonts used by the firmware -------------------------------------------------------------
#define LOAD_GLCD    // Font 1
#define LOAD_FONT2   // Font 2  (small)
#define LOAD_FONT4   // Font 4  (medium)
#define LOAD_FONT6   // Font 6  (large digits)
#define LOAD_FONT7   // Font 7  (7-segment digits, Pomodoro timer)
#define LOAD_FONT8   // Font 8

// ---- SPI speed ---------------------------------------------------------------------------------
#define SPI_FREQUENCY  40000000     // 40 MHz is safe; many GC9A01 modules also run at 80 MHz
