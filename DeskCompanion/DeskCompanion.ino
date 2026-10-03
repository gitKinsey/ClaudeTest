/*
  DeskCompanion.ino - ESP32-S3 macro pad firmware v1.2.0  (target: Waveshare ESP32-S3-Zero)

  Hardware : ESP32-S3-Zero, GC9A01 1.28" round TFT (SPI), 5 keys, EC11 encoder, onboard WS2812 LED (GPIO21)
  USB      : native USB -> CDC serial (JSON lines) + HID keyboard + HID consumer control
  Works standalone; the Python companion app is optional.

  CORE FIRST: the USB serial link, the onboard LED and the command interpreter come up before anything
  else and never depend on the display, keys, filesystem or HID. Every other subsystem reports its own
  init result ("info" command / Dev tab in the app); if the firmware crash-loops it boots into SAFE MODE
  (no display / GIF) so the pad stays reachable instead of vanishing from the PC.

  Arduino IDE settings (see README.md):
    Board = "Waveshare ESP32-S3-Zero" (or "ESP32S3 Dev Module"), USB Mode = "USB-OTG (TinyUSB)",
    USB CDC On Boot = "Enabled", Flash 4MB, Partition Scheme = "Default 4MB with spiffs", PSRAM = Disabled
  Libraries: TFT_eSPI, AnimatedGIF, Bounce2, ArduinoJson (7.x)

  Key slots used by the "remap" command: 1..5 = K1..K5, 6 = encoder CW, 7 = encoder CCW (x3 layers, optional "layer":0..2)
  v1.2: layers, mouse + host actions, info screen (mode 6), 4 GIF slots with rotation, factory / safe-mode recovery,
        opt-in Wi-Fi OTA.  Protocol additions are listed in README.md; "hello" reports a "caps" list for feature detection.

  Build flags (optional):  -DDC_SIM       emulator test build: no USB, no real display (see tools/)
                           -DDC_RGB_PIN=n onboard RGB LED pin (default 21 = Waveshare ESP32-S3-Zero)
*/
#ifndef DC_RGB_PIN
#define DC_RGB_PIN 21
#endif

// ---------------------------------------------------------------------------------------------
//  WI-FI SWITCH.  The pad is a cable device: everything works over USB. The ESP32-S3 does have Wi-Fi (and BLE), so two
//  OPTIONAL extras exist - NTP time sync and a Wi-Fi firmware update (OTA) - but they are compiled OUT by default.
//     0 = no Wi-Fi code at all (smaller, nothing can connect to a network)      1 = include them
//  Change it here, or build with -DDC_ENABLE_WIFI=1.  The app greys out its Wi-Fi settings when the pad reports no "wifi" capability.
#ifndef DC_ENABLE_WIFI
#define DC_ENABLE_WIFI 0
#endif
// ---------------------------------------------------------------------------------------------
#if defined(DC_SIM)
  #define DC_HAS_HID 0
  #define DC_HAS_TFT 0
  #define DC_HAS_OTA 0                 // the emulator has no network
#else
  #if DC_ENABLE_WIFI && !defined(DC_HAS_OTA)
  #define DC_HAS_OTA 1                 // -DDC_HAS_OTA=0 keeps Wi-Fi time sync but removes the Wi-Fi update (saves ~60 KB flash)
  #endif
  #define DC_HAS_TFT 1
  #if !defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT
    #error "Arduino IDE: Tools > USB CDC On Boot must be 'Enabled'."
  #endif
  #if defined(ARDUINO_USB_MODE) && (ARDUINO_USB_MODE == 0)
    #define DC_HAS_HID 1
  #else
    #define DC_HAS_HID 0     // hardware-CDC mode: the serial link + LED + display work, only the USB keyboard is off
    #warning "Tools > USB Mode is not 'USB-OTG (TinyUSB)': serial link works, but keyboard / media keys are disabled."
  #endif
#endif

#ifndef DC_HAS_OTA
#define DC_HAS_OTA 0
#endif
#include <Arduino.h>
#include <vector>
#include <time.h>
#include <sys/time.h>
#include <SPI.h>
#include <TFT_eSPI.h>
#if !defined(GC9A01_DRIVER) && !defined(DC_SIM)
#error "TFT_eSPI is not configured for the GC9A01 round display. Copy User_Setup.h from this project over <Arduino sketchbook>/libraries/TFT_eSPI/User_Setup.h (README, Step 2) and compile again."
#endif
#include <Bounce2.h>
#include <ArduinoJson.h>
#include <AnimatedGIF.h>
#include <Preferences.h>
#include <LittleFS.h>
#if DC_ENABLE_WIFI
#include <WiFi.h>
#include "esp_sntp.h"
#endif
#if DC_HAS_OTA
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#endif
#include "esp_system.h"
#include "esp_attr.h"
#include "driver/gpio.h"
#include "soc/rtc_cntl_reg.h"
#if DC_HAS_HID
#include "USB.h"
#include "USBHIDKeyboard.h"
#include "USBHIDConsumerControl.h"
#include "USBHIDMouse.h"
#define DC_KB_TYPE USBHIDKeyboard
#define DC_CC_TYPE USBHIDConsumerControl
#define DC_MS_TYPE USBHIDMouse
#else
struct HidStub {                               // keeps every call site valid when USB HID is not available
  void begin() {}
  size_t press(uint16_t) { return 0; }
  size_t write(uint8_t) { return 0; }
  void release() {}
  size_t release(uint8_t) { return 0; }
  void releaseAll() {}
  void click(uint8_t) {}                       // mouse calls (HID mouse is not available either)
  void move(int8_t, int8_t, int8_t, int8_t) {}
};
#define DC_KB_TYPE HidStub
#define DC_CC_TYPE HidStub
#define DC_MS_TYPE HidStub
#endif
#ifdef DC_SIM
#define DC_SIM_FLAG 1
#else
#define DC_SIM_FLAG 0
#endif
#ifdef DC_FORCE_SAFE_MODE
#define DC_FORCE_SAFE 1                // test builds: boot straight into safe mode
#else
#define DC_FORCE_SAFE 0
#endif

// ================================================================ types (kept above all functions)
enum StepType : uint8_t { ST_KEYS, ST_MEDIA, ST_TEXT, ST_DELAY, ST_LAYER, ST_HOST, ST_MOUSE, ST_PANIC };
struct Step { uint8_t t = 0; uint8_t n = 0; uint8_t keys[6] = {0, 0, 0, 0, 0, 0}; uint16_t val = 0; String text; };
struct KeyName { const char* name; uint8_t code; };
struct MediaName { const char* name; uint16_t code; };
struct FileOut { fs::File f; void put(const uint8_t* b, size_t n) { f.write(b, n); } void tick() { delay(1); } };
enum Mode : uint8_t { M_CLOCK = 1, M_POMO, M_MEDIA, M_TELEM, M_GIF, M_INFO, M_STOPW, M_BREATH, M_TOYS, M_REACT, M_SNAKE, M_HABIT };
struct InfoCard { char kind = 'c'; String label, t, a, b; };
struct InfoBadge { String name; uint16_t n = 0; };
enum PomoState : uint8_t { PS_IDLE, PS_RUN, PS_PAUSE, PS_DONE };

// ================================================================ forward declarations
// Written out explicitly rather than relying on the Arduino IDE's automatic (ctags-based)
// prototype generator: on a single-file sketch this large that step can misparse a function and
// silently corrupt the auto-generated declaration for every function after it in the file,
// producing confusing "ambiguating declaration" / "was not declared" errors at compile time.
static void backlightInit();
static void backlightSet(uint8_t v);
static uint32_t crc32u(uint32_t crc, const uint8_t* p, size_t n);
static int b64dec(const char* in, size_t n, uint8_t* out);
static void localTm(struct tm& t);
static void uiTouch();
static void activity();
static void applyBacklight();
static void loadGestureFlags();
static void sceneClockAlt(const struct tm& t);
static void sceneSaver();
static void cmdSettings(JsonDocument& doc);
static void cmdGestureTest(JsonDocument& doc);
static void panicNow();
static void fmtTime(char* b, size_t n, uint32_t ms);
static uint32_t swElapsed();
static int toyRoll(uint8_t kind);
static void snakeFood();
static void snakeNew();
static uint8_t habStreak(uint8_t h);
static void remTrigger(uint8_t i);
static void habitsReply();
static void remindersReply();
static bool snCellOk(int x, int y);
static void runSpecJson(const String& js, const char* fallback, uint8_t sid);
static bool modeKey(int i);
static void snakeTurn(int steps);
static void snakeTick();
static void reactTick();
static void remTick();
static void remDismiss();
static void habitRoll();
static void habitSave();
static uint8_t nextEnabledMode(uint8_t m, int dir);
static void sceneStopwatch();
static void sceneBreathe();
static void sceneToys();
static void sceneReact();
static void sceneSnake();
static void sceneHabits();
static void sceneReminder();
static void cmdScreens(JsonDocument& doc);
static void cmdReminders(JsonDocument& doc);
static void cmdHabits(JsonDocument& doc);
static void onEncPressTurn(int steps);
static bool nightActive(int hour, uint8_t from, uint8_t to);
static uint8_t effBright();
static void gestureKey(uint8_t lay, uint8_t i, char g, char* out);
static void runGesture(uint8_t i, char g);
static uint8_t accelMult(uint32_t dtPerDetent, uint8_t level);
static void onKeyGesture(int i, char g);
static void settingsReply();
static bool optInt(JsonDocument& doc, const char* key, int lo, int hi, int& out, bool& bad);
static uint32_t sRand();
static uint32_t fsFreeBytes();
static void sendDoc(JsonDocument& d);
static void ack(const char* evt);
static void nack(const char* err);
static bool resolveKey(const char* nm, uint8_t& code);
static bool resolveMedia(const char* nm, uint16_t& code);
static void sendMedia(uint16_t code);
static bool parseKeys(JsonVariantConst arr, Step& s);
static bool parseSpec(JsonVariantConst spec, std::vector<Step>& out);
static void macroStart(std::vector<Step>& steps);
static void macroTick();
static void slotKey(uint8_t lay, uint8_t i, char* out);
static void runSlot(uint8_t i);
static void setLayer(uint8_t n);
static void ledFlash(uint8_t r, uint8_t g, uint8_t b, uint32_t ms);
static void evtHost(const Step& s);
static void mouseDo(const Step& s);
static bool parseHost(JsonVariantConst o, Step& s);
static bool parseMouse(JsonVariantConst o, Step& s);
static String fitStr(const String& s, unsigned maxc);
static void drawOverlays();
static void sceneInfo();
static void gifPath(uint8_t slot, char* out);
static bool gifSlotExists(uint8_t slot);
static int gifNextSlot(int from);
static uint8_t gifSlotCount();
static void cmdGifList();
static void cmdLayer(JsonDocument& doc);
static void cmdInfoCards(JsonDocument& doc);
static void cmdFactory(JsonDocument& doc);
static void cmdBootOpt(JsonDocument& doc);
static void cmdOta(JsonDocument& doc);
static void otaStart();
static void otaStop();
static void otaService();
static void evtLayer();
static void arcBand(int cx, int cy, float ro, float ri, float a0, float a1, uint16_t col);
static void gauge(int cx, int cy, float ro, float ri, float frac, uint16_t col);
static void thickLine(float x0, float y0, float x1, float y1, float w, uint16_t col);
static void hand(float deg, float len, float w, uint16_t col);
static void dimSprite();
static void sceneClock();
static void scenePomo();
static void sceneMedia();
static void sceneTelemetry();
static void sceneGifMsg();
static void sceneUpload();
static void sceneMenu();
static void renderScene();
static void pushScreen();
static void renderFrame();
static uint32_t renderInterval();
static void lzwByte(uint8_t b);
static void lzwEmit(uint16_t code);
static void lzwFlush();
static uint8_t demoPix(int x, int y, int f, int F, int S);
static void demoGif(FileOut& o, int S, int F);
static bool makeDemoGif();
static void fsTask(void* arg);
static void fsPrepTask(void* arg);
static void fsStartAsync(TaskFunction_t fn, uint8_t state);
static void* GIFOpenFile(const char* fname, int32_t* pSize);
static void GIFCloseFile(void* pHandle);
static int32_t GIFReadFile(GIFFILE* pFile, uint8_t* pBuf, int32_t iLen);
static int32_t GIFSeekFile(GIFFILE* pFile, int32_t iPosition);
static void GIFDraw(GIFDRAW* pDraw);
static void gifClose();
static void gifBegin();
static void gifService();
static void setMode(uint8_t m);
static void pomoToggle();
static void pomoReset();
static void pomoTick();
static void menuCloseNow();
static void menuTurn(int steps);
static void menuClick();
static void onKey(int i);
static void onEncSteps(int steps);
static void onEncClick();
static void onEncLong();
static void encTurned(int steps);
static void encISR();
static void inputsService();
static void onNtp(struct timeval*);
static String wifiIp();
static void cmdWifi(JsonDocument& doc);
static void netService();
static void uploadAbort();
static const uint8_t* layoutByName(const char* n);
static void cmdHello();
static String coreVersionStr();
static void cmdInfo();
static void cmdLed(JsonDocument& doc);
static const char* pinUse(int p);
static bool gpioReadable(int p);
static bool gpioDrivable(int p);
static void cmdGpio(JsonDocument& doc);
static void cmdInputs();
static void cmdDisplay(JsonDocument& doc);
static void b64enc(const uint8_t* in, size_t n, char* out);
static void cmdSnapshot();
static void cmdRun(JsonDocument& doc);
static void cmdSelftest();
static void cmdGetKeys(JsonDocument& doc);
static void cmdInput(JsonDocument& doc);
static bool cmdDebug(const char* cmd);
static void cmdReboot(JsonDocument& doc);
static bool hostActive();
static void txRaw(const uint8_t* p, size_t n);
static void txLine(String s);
static void evtKey(int k, int down);
static void evtEnc(int dir, int pos);
static void evtEncSw(int down);
static float chipTemp();
static void bootNote(const char* what, bool ok);
static const char* resetReasonStr(esp_reset_reason_t r);
static void ledPixel(uint8_t r, uint8_t g, uint8_t b);
static void ledRaw(uint8_t r, uint8_t g, uint8_t b);
static void hsv2rgb(uint16_t h, uint8_t* r, uint8_t* g, uint8_t* b);
static void ledBoot(uint8_t r, uint8_t g, uint8_t b);
static void ledService();
static void handleLine(const String& line);
static void serialService();
static void loadSettings();
void setup();
void loop();

// ================================================================ pin map & tunables
static const uint8_t PIN_BLK = 7;
static const uint8_t PIN_KEY[5] = {1, 2, 4, 5, 6};            // K1..K5, active LOW
static const uint8_t PIN_ENC_A = 13, PIN_ENC_B = 14, PIN_ENC_SW = 15;

static const int      ENC_EDGES_PER_DETENT = 2;   // EC11 20-detent/20-pulse: 2 edges on A per click (use 4 for 40-edge types)
static const bool     ENC_INVERT = false;         // flip if clockwise feels like counter-clockwise
static const uint32_t ENC_HOLD_MS = 700;          // long press on encoder = next display mode
static const uint32_t MENU_TIMEOUT_MS = 4000;     // radial menu auto-close
static const int      VCC_SENSE_PIN = -1;         // optional ADC pin behind a divider (ESP32-S3 cannot measure its own VCC)
static const float    VCC_DIVIDER = 2.0f;
static const size_t   RX_MAX = 6000;              // longest accepted JSON line
static const bool     DC_IS_SIM = DC_SIM_FLAG;
static const uint8_t  PIN_RGB = DC_RGB_PIN;       // onboard WS2812 (Waveshare ESP32-S3-Zero: GPIO21)
static const char*    FW_VERSION = "1.4.0";
static const char*    MODE_NAME[13] = {"", "CLOCK", "FOCUS", "MEDIA", "SYSTEM", "GIF", "INFO", "STOPWATCH", "BREATHE", "DICE", "REACTION", "SNAKE", "HABITS"};
static const uint8_t  NUM_MODES = 12;   // 1-6 are the classic screens; 7-12 are optional (enable them with settings "mode_mask")
static const uint8_t  LAYERS = 3;                 // key layers (each has K1..K5 + dial right / left)
static const uint8_t  GIF_SLOTS = 4;              // /anim.gif (slot 0, also the built-in demo) + /anim1.gif .. /anim3.gif
static const uint32_t INFO_STALE_MS = 300000UL;   // info cards older than this are shown as "no data"

static inline uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) { return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3); }
static const uint16_t C_BG = 0x0000, C_TXT = 0xFFFF;
static const uint16_t C_DIM = rgb(38, 42, 52), C_DIM2 = rgb(70, 76, 92), C_GRAY = rgb(140, 146, 160);
static const uint16_t C_ACC = rgb(0, 210, 255), C_ACC2 = rgb(255, 70, 170), C_OK = rgb(70, 220, 110);
static const uint16_t C_WARN = rgb(255, 176, 0), C_RED = rgb(255, 72, 72);

// ================================================================ globals
TFT_eSPI tft;
TFT_eSprite spr(&tft);
AnimatedGIF gif;
Preferences prefs;
DC_KB_TYPE Keyboard;
DC_CC_TYPE ConsumerControl;
DC_MS_TYPE Mouse;
Bounce keyBtn[5];
Bounce encBtn;

uint8_t  mode = M_CLOCK, brightness = 200, osMac = 0, pomoMinutes = 25;
// ---- v1.3 settings (all persisted in NVS, changed with {"cmd":"settings"})
uint8_t  dialAccel = 0;                          // 0 off, 1 normal, 2 fast: fast turns of the dial repeat the action 2-6x
uint8_t  clockStyle = 0;                         // 0 classic, 1 digital, 2 binary, 3 minimal
uint16_t saverSec = 0;                           // idle seconds before the screensaver starts (0 = off)
uint8_t  saverStyle = 1;                         // 1 stars, 2 rain, 3 dim clock
bool     nightOn = false;                        // night dimming: cap the backlight between nightFrom and nightTo (local time, needs a synced clock)
uint8_t  nightFrom = 22, nightTo = 7, nightLevel = 30, lastBl = 255;
uint32_t lastActivity = 0, lastTurnAt = 0;
bool     saverOn = false;
int32_t  turnDtOverride = -1;                    // tests: pretend the previous detent was this many ms ago
uint8_t  lastTurnRuns = 0;                       // how many times the dial action ran for the last turn (after acceleration)
uint8_t  gHold[LAYERS], gDbl[LAYERS];            // bit i = key i of that layer has a stored hold / double-tap action
// ---- v1.4: optional screens 7-12, reminders, toggle / random actions, LED effects
uint16_t modeMask = 0x003F;                      // screens in the dial menu / long-press cycle (bit m-1 = screen m); 1-6 by default
uint8_t  tglState[LAYERS * 9 * 3];               // toggle actions: which half runs next, per (layer, slot, gesture)
bool     swRun = false; uint32_t swStartAt = 0, swAccum = 0, swLaps[4]; uint8_t swLapN = 0;
bool     brRun = false; uint8_t brPattern = 0; uint32_t brStartAt = 0;
uint8_t  toyKind = 0; int toyResult = 0; uint32_t toyRollUntil = 0;
uint8_t  rxState = 0; uint32_t rxAt = 0, rxGoAt = 0; uint16_t rxMs = 0, rxBest = 0;        // 0 idle, 1 wait, 2 go, 3 result, 4 too early
bool     snRun = false, snOver = false; uint8_t snLen = 0, snDir = 1, snFx = 0, snFy = 0; int8_t snTurn = 0; int8_t snX[100], snY[100]; uint16_t snBest = 0; uint32_t snAt = 0;
uint32_t habDay = 0; uint8_t habHist[5]; char habName[5][11] = {"WATER", "MOVE", "READ", "SLEEP", "FOCUS"};
struct Reminder { uint16_t min = 0; char text[17] = ""; uint32_t nextAt = 0; };
Reminder remArr[3]; bool remActive = false; uint8_t remIdx = 0; uint32_t remUntil = 0, remShownAt = 0, remPulseAt = 0;
uint32_t alertStart = 0, alertEnd = 0; uint8_t alertR = 0, alertG = 0, alertB = 0;      // LED alert pattern (a few quick flashes)
uint8_t  curLayer = 0, menuLayer = 0;
uint32_t layerToastUntil = 0, flashAt = 0, ledFlashUntil = 0;
uint8_t  ledFlashR = 0, ledFlashG = 0, ledFlashB = 0;
InfoCard cards[4];
InfoBadge badges[4];
uint8_t  nCards = 0, cardIdx = 0, nBadges = 0;
uint16_t cardRotSec = 6;
uint32_t cardAt = 0, infoRxAt = 0;
uint8_t  gifSlot = 0, upSlot = 0;
uint16_t gifRot = 0;
uint32_t gifSlotAt = 0;
bool     dispOff = false, otaOn = false, otaStarted = false;
String   otaPw;
bool     needRedraw = true;
uint32_t lastRender = 0;
int      vol = 50;                       // local estimate (HID gives no read-back); host may correct it via {"cmd":"media"}
bool     muted = false, playing = false;
uint8_t  hostCpu = 0, hostRam = 0;
uint32_t lastStatsMs = 0;
int32_t  tzOff = 0;
bool     timeSynced = false;
String   wifiSsid, wifiPass;
bool     wifiBusy = false, ntpDone = false, ntpStarted = false;
uint32_t wifiStart = 0, wifiNextAt = 0, lastEpochSave = 0;

bool     menuOpen = false;
uint8_t  menuSel = 0, menuEdit = 0, menuMode = 1;
uint32_t menuTouched = 0;
uint8_t  savedBright = 200;

uint8_t  pomoState = PS_IDLE;
uint32_t pomoRemain = 25UL * 60000UL, pomoLast = 0, pomoDoneAt = 0;

bool     gifOpen = false, gifRestart = true, gifFailed = false;
uint32_t gifNextAt = 0;
int      gifOffX = 0, gifOffY = 0;
fs::File     gifFile;

bool     uploading = false;
fs::File     upFile;
uint32_t upSize = 0, upRx = 0, upCrc = 0, upExpect = 0, upLast = 0;
int      upSeq = 0;
uint8_t  chunkBuf[1100];
uint16_t upChunk = 768;                  // raw bytes per upload chunk the host may send (set in setup)

std::vector<Step> macro;
bool     macroRun = false;
size_t   macroIdx = 0, textPos = 0;
uint32_t macroWake = 0;

String   rxLine;
bool     rxOverflow = false;

volatile int32_t encAccum = 0;
volatile uint32_t encLastUs = 0;
portMUX_TYPE encMux = portMUX_INITIALIZER_UNLOCKED;
int32_t  encRem = 0;
uint32_t encDownAt = 0;
bool     encHeld = false, encLongDone = false;
time_t   lastClockSec = 0;

// ---- bring-up / diagnostics state
static RTC_NOINIT_ATTR uint32_t rtcMagic;      // survives panics / watchdog resets, cleared on power-on
static RTC_NOINIT_ATTR uint32_t rtcCrashCount;
static RTC_NOINIT_ATTR uint32_t rtcDispTry;    // == RTC_DISP_TRY while a display start-up is (or was, when the pad reset) in progress
static const uint32_t RTC_MAGIC = 0xDC5AFE01u;
static const uint32_t RTC_DISP_TRY = 0xD15C0DE1u;
const char* dispWhy = "";                       // "" | init_hang | skipped_after_hang | sprite_alloc - why the display is not running
volatile bool dispInitDone = false;
bool     okPrefs = false, okSprite = false, okDisp = false;
volatile bool okFs = false;                    // true only while LittleFS is mounted AND no background job is using it
uint32_t fsStartAt = 0;                         // start of the background filesystem job (0 = already started)
volatile bool demoFailed = false;               // building the built-in demo animation failed (storage full / error)
volatile uint8_t fsState = 0;                  // FS_* below: mounting / formatting / preparing / ready / failed
bool     safeMode = false;                     // 3+ crashes in a row: display / GIF disabled so the core stays reachable
uint32_t crashCount = 0;
esp_reset_reason_t resetReason = ESP_RST_UNKNOWN;
String   bootLog;
uint32_t lastRxMs = 0;                         // last time any byte arrived from the host
bool     eventsOn = false;                     // stream key / encoder events to the app's Dev tab
int32_t  reqId = -1;                           // "id" of the request being answered (echoed back)
int32_t  encPos = 0;
uint32_t dispHoldUntil = 0;                    // test pattern on screen: normal rendering paused until then
bool     gpioTouched = false;

enum FsState : uint8_t { FS_IDLE, FS_MOUNTING, FS_FORMATTING, FS_PREPARING, FS_READY, FS_FAILED };
static const char* const FS_STATE_NAME[6] = {"idle", "mounting", "formatting", "preparing", "ready", "failed"};
enum LedMode : uint8_t { LM_AUTO, LM_OFF, LM_SOLID, LM_BLINK, LM_RAINBOW, LM_BREATHE, LM_FIRE };
uint8_t  ledMode = LM_AUTO, ledUserR = 0, ledUserG = 0, ledUserB = 0;
uint8_t  ledBootR = 24, ledBootG = 0, ledBootB = 24;
uint8_t  ledLastR = 0, ledLastG = 0, ledLastB = 0;
bool     ledReady = false;
uint32_t ledNextAt = 0;

// ================================================================ backlight (PWM on GPIO7)
// NOTE: #if/#else/#endif must NOT appear *inside* a function body in a .ino file - the Arduino
// prototype-generator (ctags-based) can misparse the brace nesting and corrupt every prototype it
// generates for the rest of the file. Each branch below is therefore a complete, separate function.
#if ESP_ARDUINO_VERSION_MAJOR >= 3
static void backlightInit() { ledcAttach(PIN_BLK, 5000, 8); }
static void backlightSet(uint8_t v) { ledcWrite(PIN_BLK, v); }
#else
static void backlightInit() { ledcSetup(0, 5000, 8); ledcAttachPin(PIN_BLK, 0); }
static void backlightSet(uint8_t v) { ledcWrite(0, v); }
#endif

// ================================================================ small utilities
static uint32_t crc32u(uint32_t crc, const uint8_t* p, size_t n) {   // zlib-compatible, chainable
  crc = ~crc;
  while (n--) { crc ^= *p++; for (int k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u))); }
  return ~crc;
}
static int b64dec(const char* in, size_t n, uint8_t* out) {
  static int8_t T[256];
  static bool init = false;
  if (!init) {
    memset(T, -1, sizeof T);
    const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (int i = 0; i < 64; i++) T[(uint8_t)A[i]] = i;
    init = true;
  }
  int o = 0; uint32_t acc = 0; int bits = 0;
  for (size_t i = 0; i < n; i++) {
    if (in[i] == '=') break;
    int v = T[(uint8_t)in[i]];
    if (v < 0) return -1;
    acc = (acc << 6) | (uint32_t)v; bits += 6;
    if (bits >= 8) { bits -= 8; out[o++] = (acc >> bits) & 0xFF; }
  }
  return o;
}
static void localTm(struct tm& t) { time_t n = time(nullptr) + tzOff; gmtime_r(&n, &t); }
static void uiTouch() { menuTouched = millis(); }
static void activity() { lastActivity = millis(); if (saverOn) { saverOn = false; needRedraw = true; } if (remActive && millis() - remShownAt > 400) remDismiss(); }   // any physical input wakes the screensaver
static bool nightActive(int hour, uint8_t from, uint8_t to) {                  // pure: is `hour` inside [from, to) - the window may wrap midnight
  if (from == to) return false;
  return from < to ? (hour >= from && hour < to) : (hour >= from || hour < to);
}
static uint8_t effBright() {
  if (!nightOn || !timeSynced) return brightness;
  struct tm t; localTm(t);
  return nightActive(t.tm_hour, nightFrom, nightTo) ? (uint8_t)min((int)brightness, (int)nightLevel) : brightness;
}
static void applyBacklight() { lastBl = effBright(); backlightSet(lastBl); }
static uint32_t fsFreeBytes() { return (uint32_t)(LittleFS.totalBytes() - LittleFS.usedBytes()); }   // raw free space (an upload replaces its own slot first)

// ================================================================ serial TX (bounded: never stalls the firmware)
static bool hostActive() { return lastRxMs != 0 && (uint32_t)(millis() - lastRxMs) < 8000; }
static void txRaw(const uint8_t* p, size_t n) {
  size_t off = 0;
  uint32_t t0 = millis();
  while (off < n && (uint32_t)(millis() - t0) < 250) {          // gives up after 250 ms if the host stopped reading
    int room = Serial.availableForWrite();
    if (room <= 0) { delay(1); continue; }
    size_t k = n - off;
    if (k > (size_t)room) k = room;
    size_t w = Serial.write(p + off, k);
    if (w == 0) { delay(1); continue; }
    off += w;
  }
}
static void txLine(String s) { s += '\n'; txRaw((const uint8_t*)s.c_str(), s.length()); }   // one write = one USB packet train

// ================================================================ JSON replies
static void sendDoc(JsonDocument& d) {
  if (reqId >= 0) d["id"] = reqId;
  String s;
  serializeJson(d, s);
  txLine(s);
}
static void ack(const char* evt) { JsonDocument d; d["ok"] = true; d["evt"] = evt; sendDoc(d); }
static void nack(const char* err) { JsonDocument d; d["ok"] = false; d["err"] = err; sendDoc(d); }
static void evtKey(int k, int down) {
  if (!eventsOn || !hostActive()) return;
  char b[64]; int n = snprintf(b, sizeof b, "{\"evt\":\"key\",\"k\":%d,\"v\":%d}\n", k, down);
  txRaw((const uint8_t*)b, n);
}
static void evtEnc(int dir, int pos) {
  if (!eventsOn || !hostActive()) return;
  char b[80]; int n = snprintf(b, sizeof b, "{\"evt\":\"enc\",\"d\":%d,\"pos\":%d}\n", dir, pos);
  txRaw((const uint8_t*)b, n);
}
static void evtEncSw(int down) {
  if (!eventsOn || !hostActive()) return;
  char b[48]; int n = snprintf(b, sizeof b, "{\"evt\":\"encsw\",\"v\":%d}\n", down);
  txRaw((const uint8_t*)b, n);
}

// ================================================================ boot log + onboard RGB LED (WS2812, GRB handled by the core)
#ifdef DC_SIM
static float chipTemp() { return 25.0f; }          // the emulator has no temperature sensor (reading it would hang)
#else
static float chipTemp() { return temperatureRead(); }
#endif
static void bootNote(const char* what, bool ok) {
  if (bootLog.length() < 360) { bootLog += String(millis()); bootLog += ':'; bootLog += what; bootLog += ok ? "=ok;" : "=FAIL;"; }
}
static const char* resetReasonStr(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "deep-sleep";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    default: return "unknown";
  }
}
#ifndef DC_SIM
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 1, 0)
static void ledPixel(uint8_t r, uint8_t g, uint8_t b) { rgbLedWrite(PIN_RGB, r, g, b); }
#else
static void ledPixel(uint8_t r, uint8_t g, uint8_t b) { neopixelWrite(PIN_RGB, r, g, b); }
#endif
#else
static void ledPixel(uint8_t r, uint8_t g, uint8_t b) { (void)r; (void)g; (void)b; }   // emulator build: no LED peripheral
#endif
static void ledRaw(uint8_t r, uint8_t g, uint8_t b) {
  static uint32_t wroteAt = 0;
  uint32_t now = millis();
  if (wroteAt && r == ledLastR && g == ledLastG && b == ledLastB && (uint32_t)(now - wroteAt) < 1000) return;   // unchanged: refresh once a second
  wroteAt = now; ledLastR = r; ledLastG = g; ledLastB = b;
  ledPixel(r, g, b);
}
static void hsv2rgb(uint16_t h, uint8_t* r, uint8_t* g, uint8_t* b) {      // h 0..359, full saturation / value 0..255
  uint8_t region = h / 60, rem = (h % 60) * 255 / 60;
  uint8_t q = 255 - rem, t = rem;
  switch (region) {
    case 0: *r = 255; *g = t; *b = 0; break;
    case 1: *r = q; *g = 255; *b = 0; break;
    case 2: *r = 0; *g = 255; *b = t; break;
    case 3: *r = 0; *g = q; *b = 255; break;
    case 4: *r = t; *g = 0; *b = 255; break;
    default: *r = 255; *g = 0; *b = q; break;
  }
}
static void ledBoot(uint8_t r, uint8_t g, uint8_t b) {                // boot-stage colour, visible even if USB / display fail
  ledBootR = r; ledBootG = g; ledBootB = b;
  if (ledMode == LM_AUTO) ledRaw(r, g, b);
}
static void ledFlash(uint8_t r, uint8_t g, uint8_t b, uint32_t ms) {   // short coloured blink (layer change); only in LED "auto" mode
  ledFlashR = r; ledFlashG = g; ledFlashB = b; ledFlashUntil = millis() + ms; ledNextAt = 0;
}
static void ledService() {
  uint32_t now = millis();
  if ((int32_t)(now - ledNextAt) < 0) return;
  if ((int32_t)(alertEnd - now) > 0) { bool on = ((now - alertStart) / 120) % 2 == 0; ledRaw(on ? alertR / 3 : 0, on ? alertG / 3 : 0, on ? alertB / 3 : 0); ledNextAt = now + 20; return; }
  switch (ledMode) {
    case LM_OFF: ledRaw(0, 0, 0); ledNextAt = now + 500; break;
    case LM_SOLID: ledRaw(ledUserR, ledUserG, ledUserB); ledNextAt = now + 500; break;
    case LM_BLINK: ledRaw(((now / 400) & 1) ? ledUserR : 0, ((now / 400) & 1) ? ledUserG : 0, ((now / 400) & 1) ? ledUserB : 0); ledNextAt = now + 40; break;
    case LM_RAINBOW: { uint8_t r, g, b; hsv2rgb((now / 8) % 360, &r, &g, &b); ledRaw(r / 4, g / 4, b / 4); ledNextAt = now + 20; break; }
    case LM_BREATHE: {                                              // slow sine fade in the user's colour (cyan until one is set)
      float f = 0.5f - 0.5f * cosf(now / 1100.0f);
      bool unset = !ledUserR && !ledUserG && !ledUserB;
      ledRaw((uint8_t)((unset ? 0 : ledUserR) * f), (uint8_t)((unset ? 60 : ledUserG) * f), (uint8_t)((unset ? 70 : ledUserB) * f)); ledNextAt = now + 25; break;
    }
    case LM_FIRE: { uint32_t r = esp_random(); uint8_t base = 18 + (r & 31); ledRaw(base, (uint8_t)(base / 4 + ((r >> 8) & 7)), 0); ledNextAt = now + 45 + ((r >> 16) & 31); break; }
    default:                                                          // LM_AUTO
      if ((int32_t)(ledFlashUntil - now) > 0) { ledRaw(ledFlashR, ledFlashG, ledFlashB); ledNextAt = now + 30; }
      else if (!ledReady) { ledRaw(ledBootR, ledBootG, ledBootB); ledNextAt = now + 100; }
      else if (safeMode) { uint32_t t = now % 1500; ledRaw((t < 150 || (t > 300 && t < 450)) ? 40 : 0, 0, 0); ledNextAt = now + 30; }
      else {                                                          // heartbeat: green = all good, amber = a subsystem failed
        bool bad = !okFs || !okDisp;
        bool on = (now % 3000) < 120;
        ledRaw(on && bad ? 30 : 0, on ? (bad ? 18 : 24) : 0, 0);
        ledNextAt = now + 30;
      }
  }
}

// ================================================================ HID: key names, media, macro engine
static const KeyName KEY_NAMES[] = {
  {"CTRL", 0x80}, {"CONTROL", 0x80}, {"SHIFT", 0x81}, {"ALT", 0x82}, {"OPT", 0x82}, {"OPTION", 0x82},
  {"GUI", 0x83}, {"WIN", 0x83}, {"CMD", 0x83}, {"META", 0x83}, {"SUPER", 0x83},
  {"ENTER", 176}, {"RETURN", 176}, {"ESC", 177}, {"ESCAPE", 177}, {"BACKSPACE", 178}, {"TAB", 179},
  {"SPACE", ' '}, {"CAPSLOCK", 193}, {"PRTSC", 206}, {"PRINTSCREEN", 206}, {"PAUSE", 208}, {"INSERT", 209},
  {"HOME", 210}, {"PGUP", 211}, {"PAGEUP", 211}, {"DELETE", 212}, {"DEL", 212}, {"END", 213},
  {"PGDN", 214}, {"PAGEDOWN", 214}, {"RIGHT", 215}, {"LEFT", 216}, {"DOWN", 217}, {"UP", 218}, {"MENU", 254}};
static const MediaName MEDIA_NAMES[] = {
  {"PLAY_PAUSE", 0xCD}, {"NEXT", 0xB5}, {"PREV", 0xB6}, {"STOP", 0xB7}, {"MUTE", 0xE2},
  {"VOL_UP", 0xE9}, {"VOL_DOWN", 0xEA}, {"FF", 0xB3}, {"REWIND", 0xB4}};

static bool resolveKey(const char* nm, uint8_t& code) {
  if (!nm || !*nm) return false;
  if (nm[1] == 0) {                                   // single printable character
    char c = nm[0];
    if (c >= 'A' && c <= 'Z') c += 32;
    code = (uint8_t)c;
    return c > 0 && c < 127;
  }
  String n(nm); n.toUpperCase();
  if (n == "PRIMARY" || n == "MOD") { code = osMac ? 0x83 : 0x80; return true; }   // Cmd on macOS, Ctrl elsewhere
  for (const KeyName& k : KEY_NAMES) if (n == k.name) { code = k.code; return true; }
  if (n[0] == 'F' && n.length() <= 3) {
    int f = n.substring(1).toInt();
    if (f >= 1 && f <= 12) { code = 193 + f; return true; }
    if (f >= 13 && f <= 24) { code = 240 + f - 13; return true; }
  }
  return false;
}
static bool resolveMedia(const char* nm, uint16_t& code) {
  if (!nm) return false;
  String n(nm); n.toUpperCase();
  for (const MediaName& m : MEDIA_NAMES) if (n == m.name) { code = m.code; return true; }
  return false;
}
static void sendMedia(uint16_t code) {
  ConsumerControl.press(code);
  delay(6);
  ConsumerControl.release();
  switch (code) {                                     // keep the local media/volume model in sync
    case 0xE9: vol = vol + 2 > 100 ? 100 : vol + 2; muted = false; break;
    case 0xEA: vol = vol - 2 < 0 ? 0 : vol - 2; muted = false; break;
    case 0xE2: muted = !muted; break;
    case 0xCD: playing = !playing; break;
    case 0xB7: playing = false; break;
  }
  needRedraw = true;
}

static bool parseKeys(JsonVariantConst arr, Step& s) {
  if (!arr.is<JsonArrayConst>()) return false;
  s.t = ST_KEYS; s.n = 0;
  for (JsonVariantConst v : arr.as<JsonArrayConst>()) {
    uint8_t c;
    if (s.n >= 6 || !resolveKey(v.as<const char*>(), c)) return false;
    s.keys[s.n++] = c;
  }
  return s.n > 0;
}
// spec = {"type":"combo|media|text|macro|none","val":...}
static bool parseSpec(JsonVariantConst spec, std::vector<Step>& out) {
  const char* type = spec["type"] | "none";
  JsonVariantConst val = spec["val"];
  if (!strcmp(type, "none")) return true;
  if (!strcmp(type, "combo")) { Step s; if (!parseKeys(val, s)) return false; out.push_back(s); return true; }
  if (!strcmp(type, "media")) {
    Step s; s.t = ST_MEDIA;
    if (!resolveMedia(val.as<const char*>(), s.val)) return false;
    out.push_back(s); return true;
  }
  if (!strcmp(type, "text")) { Step s; s.t = ST_TEXT; s.text = val.as<const char*>() ? val.as<const char*>() : ""; out.push_back(s); return true; }
  if (!strcmp(type, "layer")) {                                  // "next" | "prev" | 0..2
    Step s; s.t = ST_LAYER;
    if (val.is<const char*>()) { String v = val.as<const char*>(); if (v == "next") s.val = 100; else if (v == "prev") s.val = 101; else return false; }
    else if (val.is<int>() && val.as<int>() >= 0 && val.as<int>() < LAYERS) s.val = (uint16_t)val.as<int>();
    else return false;
    out.push_back(s); return true;
  }
  if (!strcmp(type, "host")) { Step s; if (!parseHost(val, s)) return false; out.push_back(s); return true; }
  if (!strcmp(type, "mouse")) { Step s; if (!parseMouse(val, s)) return false; out.push_back(s); return true; }
  if (!strcmp(type, "panic")) { Step s; s.t = ST_PANIC; out.push_back(s); return true; }
  if (!strcmp(type, "toggle") || !strcmp(type, "random")) {      // {"type":"toggle","val":[specA,specB]} alternates; {"type":"random","val":[spec x2..6]} picks one
    if (!val.is<JsonArrayConst>()) return false;
    size_t n = val.size(), lo = !strcmp(type, "toggle") ? 2 : 2, hi = !strcmp(type, "toggle") ? 2 : 6;
    if (n < lo || n > hi) return false;
    for (JsonVariantConst sub : val.as<JsonArrayConst>()) {
      const char* st = sub["type"] | "";
      if (!strcmp(st, "toggle") || !strcmp(st, "random")) return false;       // no nesting
      std::vector<Step> tmp;
      if (!parseSpec(sub, tmp)) return false;
    }
    Step s; s.t = ST_DELAY; s.val = 0; out.push_back(s);          // a harmless placeholder: the real choice is made when the key runs (runSpecJson)
    return true;
  }
  if (!strcmp(type, "macro")) {
    if (!val.is<JsonArrayConst>()) return false;
    for (JsonVariantConst o : val.as<JsonArrayConst>()) {
      if (out.size() >= 64) return false;
      Step s;
      if (!o["combo"].isNull()) { if (!parseKeys(o["combo"], s)) return false; }
      else if (!o["text"].isNull()) { s.t = ST_TEXT; s.text = o["text"].as<String>(); }
      else if (!o["delay"].isNull()) { s.t = ST_DELAY; s.val = (uint16_t)constrain(o["delay"].as<int>(), 0, 60000); }
      else if (!o["media"].isNull()) { s.t = ST_MEDIA; if (!resolveMedia(o["media"].as<const char*>(), s.val)) return false; }
      else if (!o["host"].isNull()) { if (!parseHost(o["host"], s)) return false; }
      else if (!o["mouse"].isNull()) { if (!parseMouse(o["mouse"], s)) return false; }
      else if (!o["panic"].isNull()) { s.t = ST_PANIC; }
      else if (!o["layer"].isNull()) {
        s.t = ST_LAYER;
        if (o["layer"].is<const char*>()) { String v = o["layer"].as<const char*>(); if (v == "next") s.val = 100; else if (v == "prev") s.val = 101; else return false; }
        else if (o["layer"].is<int>() && o["layer"].as<int>() >= 0 && o["layer"].as<int>() < LAYERS) s.val = (uint16_t)o["layer"].as<int>();
        else return false;
      }
      else return false;
      out.push_back(s);
    }
    return true;
  }
  return false;
}
// ---- host actions: the pad cannot launch programs, so it tells the companion app ({"evt":"host",...}) which does it
static const char* const HOST_OPS[] = {"url", "app", "shell", "clipboard", "file", "notify", "snippet", "clip", "script",
                                      "appvol", "dnd", "audio_out", "mic", "shot", "translate", "ai", "webhook", "layout", "cliphist", "plugin", "art", "qr"};   // new ops are appended: the index is stored in saved macros
static bool parseHost(JsonVariantConst o, Step& s) {
  const char* op = o["op"] | "";
  const char* arg = o["arg"] | "";
  for (uint8_t i = 0; i < sizeof HOST_OPS / sizeof HOST_OPS[0]; i++) {
    if (strcmp(op, HOST_OPS[i])) continue;
    if (strcmp(op, "clipboard") && !*arg) return false;                 // everything but "type the clipboard" needs an argument
    if (strlen(arg) > 400) return false;
    s.t = ST_HOST; s.n = i + 1; s.text = arg;
    return true;
  }
  return false;
}
static void evtHost(const Step& s) {
  if (!hostActive()) return;                                             // nobody listening: the pad itself cannot run it
  int32_t keep = reqId; reqId = -1;                                      // async event: never carries a request id
  JsonDocument d; d["evt"] = "host"; d["op"] = HOST_OPS[s.n - 1]; d["arg"] = s.text;
  sendDoc(d);
  reqId = keep;
}
// ---- HID mouse: {"btn":"left|right|middle|back|forward","act":"click|double|down|up"} | {"wheel":n} | {"move":[dx,dy]}
static bool parseMouse(JsonVariantConst o, Step& s) {
  if (!o.is<JsonObjectConst>()) return false;
  s.t = ST_MOUSE;
  if (!o["wheel"].isNull()) {                                    // {"wheel":n,"mods":["CTRL"],"h":true}: Ctrl+wheel = zoom, "h" = sideways scroll
    s.n = 3; s.val = (uint16_t)(int16_t)constrain(o["wheel"].as<int>(), -20, 20); s.keys[0] = 0; s.keys[1] = o["h"] | false ? 1 : 0;
    if (!o["mods"].isNull()) {
      if (!o["mods"].is<JsonArrayConst>()) return false;
      for (JsonVariantConst m : o["mods"].as<JsonArrayConst>()) {
        String n = m.as<String>(); n.toUpperCase();
        if (n == "CTRL") s.keys[0] |= 1; else if (n == "SHIFT") s.keys[0] |= 2; else if (n == "ALT") s.keys[0] |= 4; else if (n == "GUI") s.keys[0] |= 8; else return false;
      }
    }
    return true;
  }
  if (!o["move"].isNull()) {
    if (!o["move"].is<JsonArrayConst>() || o["move"].size() != 2) return false;
    s.n = 4; s.keys[1] = (uint8_t)(int8_t)constrain(o["move"][0].as<int>(), -100, 100); s.keys[2] = (uint8_t)(int8_t)constrain(o["move"][1].as<int>(), -100, 100);
    return true;
  }
  String b = o["btn"] | "left", a = o["act"] | "click";
  if (b == "left") s.keys[0] = 1; else if (b == "right") s.keys[0] = 2; else if (b == "middle") s.keys[0] = 4;
  else if (b == "back") s.keys[0] = 8; else if (b == "forward") s.keys[0] = 16; else return false;
  if (a == "click") s.n = 1; else if (a == "double") s.n = 2; else if (a == "down") s.n = 5; else if (a == "up") s.n = 6; else return false;
  return true;
}
static void mouseDo(const Step& s) {
  switch (s.n) {
    case 1: Mouse.click(s.keys[0]); break;
    case 2: Mouse.click(s.keys[0]); delay(40); Mouse.click(s.keys[0]); break;
    case 3: {
      static const uint8_t MODCODE[4] = {0x80, 0x81, 0x82, 0x83};
      for (int i = 0; i < 4; i++) if (s.keys[0] & (1 << i)) Keyboard.press(MODCODE[i]);
      if (s.keys[0]) delay(15);
      int8_t w = (int8_t)(int16_t)s.val;
      if (s.keys[1]) Mouse.move(0, 0, 0, w); else Mouse.move(0, 0, w, 0);
      if (s.keys[0]) { delay(15); Keyboard.releaseAll(); }
      break;
    }
    case 4: Mouse.move((int8_t)s.keys[1], (int8_t)s.keys[2], 0, 0); break;
    case 5: Mouse.press(s.keys[0]); break;
    case 6: Mouse.release(s.keys[0]); break;
  }
}

static void macroStart(std::vector<Step>& steps) {
  if (steps.empty()) return;
  if (macroRun) {                                    // queue behind the running macro so fast encoder turns keep every step
    for (const Step& s : steps) if (s.t == ST_TEXT) return;       // text snippets are never stacked up
    if (macro.size() - macroIdx + steps.size() > 96) return;      // bounded backlog
    for (const Step& s : steps) macro.push_back(s);
    return;
  }
  macro.swap(steps); macroIdx = 0; textPos = 0; macroWake = millis(); macroRun = true;
}
static void panicNow() {                                          // panic key: let go of everything and drop every queued macro step
  Keyboard.releaseAll(); Mouse.release(0xFF); ConsumerControl.release();
  macro.clear(); macroIdx = 0; macroRun = false; textPos = 0;
  ledFlash(60, 0, 0, 300);
}
static void macroTick() {
  while (macroRun) {
    if ((int32_t)(millis() - macroWake) < 0) return;
    if (macroIdx >= macro.size()) { macroRun = false; macro.clear(); return; }
    const Step& s = macro[macroIdx];
    switch (s.t) {
      case ST_KEYS:
        for (uint8_t i = 0; i < s.n; i++) Keyboard.press(s.keys[i]);
        delay(12);
        Keyboard.releaseAll();
        macroIdx++; macroWake = millis() + 8; break;
      case ST_MEDIA: sendMedia(s.val); macroIdx++; macroWake = millis() + 5; break;
      case ST_DELAY: macroIdx++; macroWake = millis() + s.val; break;
      case ST_LAYER: setLayer(s.val == 100 ? (curLayer + 1) % LAYERS : s.val == 101 ? (curLayer + LAYERS - 1) % LAYERS : (uint8_t)s.val); macroIdx++; macroWake = millis() + 5; break;
      case ST_HOST: evtHost(s); macroIdx++; macroWake = millis() + 5; break;
      case ST_MOUSE: mouseDo(s); macroIdx++; macroWake = millis() + 8; break;
      case ST_PANIC: panicNow(); return;
      case ST_TEXT:
        if (textPos < s.text.length()) {
          uint8_t c = (uint8_t)s.text[textPos++];
          if (c < 128 && c != '\r') Keyboard.write(c);
          macroWake = millis() + 4;
        }
        if (textPos >= s.text.length()) { textPos = 0; macroIdx++; }
        break;
    }
    if (macroIdx >= macro.size()) { macroRun = false; macro.clear(); }
  }
}

static const char* const DEFAULT_SLOT[LAYERS][7] = {
  { R"({"type":"combo","val":["PRIMARY","c"]})",       // layer 1: K1 copy
    R"({"type":"combo","val":["PRIMARY","v"]})",       //          K2 paste
    R"({"type":"combo","val":["PRIMARY","z"]})",       //          K3 undo
    R"({"type":"media","val":"PLAY_PAUSE"})",          //          K4
    R"({"type":"media","val":"MUTE"})",                //          K5
    R"({"type":"media","val":"VOL_UP"})",              //          encoder CW
    R"({"type":"media","val":"VOL_DOWN"})" },          //          encoder CCW
  { R"({"type":"media","val":"PREV"})",                // layer 2: media - previous / play / next / stop / mute, dial = volume
    R"({"type":"media","val":"PLAY_PAUSE"})",
    R"({"type":"media","val":"NEXT"})",
    R"({"type":"media","val":"STOP"})",
    R"({"type":"media","val":"MUTE"})",
    R"({"type":"media","val":"VOL_UP"})",
    R"({"type":"media","val":"VOL_DOWN"})" },
  { R"({"type":"combo","val":["ALT","LEFT"]})",        // layer 3: browser - back / forward / reload / new tab / close tab, dial = page down / up
    R"({"type":"combo","val":["ALT","RIGHT"]})",
    R"({"type":"combo","val":["F5"]})",
    R"({"type":"combo","val":["PRIMARY","t"]})",
    R"({"type":"combo","val":["PRIMARY","w"]})",
    R"({"type":"combo","val":["PGDN"]})",
    R"({"type":"combo","val":["PGUP"]})" }};

static void slotKey(uint8_t lay, uint8_t i, char* out) {         // NVS key (max 15 chars): layer 0 keeps the v1.0/1.1 names "s0".."s6"
  if (lay == 0) snprintf(out, 8, "s%u", i); else snprintf(out, 8, "L%us%u", lay, i);
}
// Runs a stored spec. Toggle / random / panic are decided here, at the moment the key runs; everything else goes through parseSpec().
// sid identifies the key (layer, slot, gesture) so every toggle key remembers its own state.
static void runSpecJson(const String& js, const char* fallback, uint8_t sid) {
  JsonDocument d;
  if (deserializeJson(d, js)) { if (!fallback || deserializeJson(d, fallback)) return; }
  JsonVariantConst spec = d.as<JsonVariantConst>();
  const char* t = spec["type"] | "";
  if (!strcmp(t, "panic")) { panicNow(); return; }
  if (!strcmp(t, "toggle") && spec["val"].is<JsonArrayConst>() && spec["val"].size() == 2) {
    uint8_t half = tglState[sid % sizeof tglState] ? 1 : 0;
    tglState[sid % sizeof tglState] = half ? 0 : 1;
    spec = spec["val"][half];
    ledFlash(half ? 40 : 0, half ? 20 : 40, 0, 220);                // green = first action, amber = second
  } else if (!strcmp(t, "random") && spec["val"].is<JsonArrayConst>() && spec["val"].size() >= 2) {
    spec = spec["val"][esp_random() % spec["val"].size()];
  }
  if (!strcmp(spec["type"] | "", "panic")) { panicNow(); return; }
  std::vector<Step> steps;
  if (parseSpec(spec, steps)) macroStart(steps);
}
static void runSlot(uint8_t i) {                                  // i: 0..6 = K1..K5, dial right, dial left; 7 / 8 = dial pressed + turned right / left
  char k[8]; slotKey(curLayer, i, k);
  const char* def = i < 7 ? DEFAULT_SLOT[curLayer][i] : "";
  if (i >= 7 && !prefs.isKey(k)) return;
  runSpecJson(prefs.getString(k, String(def)), def, (curLayer * 9 + i) * 3);
}
static void evtLayer() {
  if (!hostActive()) return;
  int32_t keep = reqId; reqId = -1;
  JsonDocument d; d["evt"] = "layer"; d["n"] = curLayer; sendDoc(d);
  reqId = keep;
}
static void setLayer(uint8_t n) {
  if (n >= LAYERS) return;
  bool changed = n != curLayer;
  curLayer = n; layerToastUntil = millis() + 1600; needRedraw = true;
  static const uint8_t LC[LAYERS][3] = {{0, 40, 40}, {40, 0, 40}, {40, 30, 0}};   // 1 cyan, 2 magenta, 3 amber
  ledFlash(LC[n][0], LC[n][1], LC[n][2], 450);
  if (changed) evtLayer();
}

// ================================================================ drawing helpers (all draw into the 240x240 sprite)
static void arcBand(int cx, int cy, float ro, float ri, float a0, float a1, uint16_t col) {   // 0 deg = 12 o'clock, clockwise
  if (a1 <= a0) return;
  int n = (int)ceilf((a1 - a0) / 4.0f); if (n < 1) n = 1;
  float da = (a1 - a0) / n;
  for (int i = 0; i < n; i++) {
    float s = (a0 + i * da) * DEG_TO_RAD, e = (a0 + (i + 1) * da + 0.6f) * DEG_TO_RAD;
    float ss = sinf(s), cs = cosf(s), se = sinf(e), ce = cosf(e);
    int x0 = lroundf(cx + ro * ss), y0 = lroundf(cy - ro * cs), x1 = lroundf(cx + ro * se), y1 = lroundf(cy - ro * ce);
    int x2 = lroundf(cx + ri * se), y2 = lroundf(cy - ri * ce), x3 = lroundf(cx + ri * ss), y3 = lroundf(cy - ri * cs);
    spr.fillTriangle(x0, y0, x1, y1, x2, y2, col);
    spr.fillTriangle(x0, y0, x2, y2, x3, y3, col);
  }
}
static void gauge(int cx, int cy, float ro, float ri, float frac, uint16_t col) {   // 270 degree gauge, gap at the bottom
  arcBand(cx, cy, ro, ri, 225, 495, C_DIM);
  frac = constrain(frac, 0.0f, 1.0f);
  if (frac > 0.005f) arcBand(cx, cy, ro, ri, 225, 225 + 270 * frac, col);
}
static void thickLine(float x0, float y0, float x1, float y1, float w, uint16_t col) {
  float dx = x1 - x0, dy = y1 - y0, l = sqrtf(dx * dx + dy * dy);
  if (l < 0.5f) return;
  float nx = -dy / l * w * 0.5f, ny = dx / l * w * 0.5f;
  spr.fillTriangle(lroundf(x0 + nx), lroundf(y0 + ny), lroundf(x0 - nx), lroundf(y0 - ny), lroundf(x1 + nx), lroundf(y1 + ny), col);
  spr.fillTriangle(lroundf(x0 - nx), lroundf(y0 - ny), lroundf(x1 - nx), lroundf(y1 - ny), lroundf(x1 + nx), lroundf(y1 + ny), col);
}
static void hand(float deg, float len, float w, uint16_t col) {
  float a = deg * DEG_TO_RAD;
  thickLine(120 - len * 0.15f * sinf(a), 120 + len * 0.15f * cosf(a), 120 + len * sinf(a), 120 - len * cosf(a), w, col);
}
static void dimSprite() {                              // halve brightness of every pixel (sprite stores byte-swapped RGB565)
  uint16_t* p = (uint16_t*)spr.getPointer();
  if (!p) return;
  for (int i = 0; i < 240 * 240; i++) { uint16_t c = __builtin_bswap16(p[i]); c = (c >> 1) & 0x7BEF; p[i] = __builtin_bswap16(c); }
}

// ================================================================ scenes
static const char* const DOW[7] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};
static const char* const MON[12] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};

static void sceneClockAlt(const struct tm& t) {
  char b[24];
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM);
  if (clockStyle == 1) {                                       // digital: big 7-segment time, seconds ring, date
    gauge(120, 120, 117, 107, t.tm_sec / 59.0f, C_ACC);
    snprintf(b, sizeof b, "%02d:%02d", t.tm_hour, t.tm_min);
    spr.setTextColor(C_TXT); spr.drawString(b, 120, 112, 7);
    spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%s %02d %s", DOW[t.tm_wday], t.tm_mday, MON[t.tm_mon]); spr.drawString(b, 120, 166, 2);
    spr.fillCircle(120, 196, 3, timeSynced ? C_OK : C_WARN);
  } else if (clockStyle == 2) {                                // binary-coded decimal: columns H H M M S S, 4 rows, lit dot = 1
    int v[6] = {t.tm_hour / 10, t.tm_hour % 10, t.tm_min / 10, t.tm_min % 10, t.tm_sec / 10, t.tm_sec % 10};
    static const uint8_t bits[6] = {2, 4, 3, 4, 3, 4};
    for (int c = 0; c < 6; c++) {
      int x = 40 + c * 32 + (c / 2) * 8 - 8;
      for (int r = 0; r < 4; r++) {
        int bit = 3 - r; if (bit >= bits[c]) continue;
        bool on = (v[c] >> bit) & 1;
        if (on) spr.fillCircle(x, 70 + r * 28, 10, c < 2 ? C_ACC : c < 4 ? C_ACC2 : C_OK); else spr.drawCircle(x, 70 + r * 28, 10, C_DIM2);
      }
    }
    spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%02d:%02d:%02d", t.tm_hour, t.tm_min, t.tm_sec); spr.drawString(b, 120, 186, 4);
  } else {                                                     // 3 minimal: huge time, one thin minute ring
    arcBand(120, 120, 118, 114, 0, (t.tm_sec / 60.0f) * 360.0f, C_ACC);
    snprintf(b, sizeof b, "%02d", t.tm_hour); spr.setTextColor(C_TXT); spr.drawString(b, 120, 86, 7);
    snprintf(b, sizeof b, "%02d", t.tm_min); spr.setTextColor(C_ACC); spr.drawString(b, 120, 156, 7);
  }
}

static void sceneClock() {
  struct tm t; localTm(t);
  if (clockStyle >= 1 && clockStyle <= 3) { sceneClockAlt(t); return; }
  spr.fillSprite(C_BG);
  spr.drawCircle(120, 120, 119, C_DIM2);
  spr.drawCircle(120, 120, 118, C_DIM);
  for (int i = 0; i < 60; i++) {
    float a = i * 6 * DEG_TO_RAD; bool big = (i % 5 == 0);
    float r0 = big ? 98 : 107, r1 = 114;
    if (big) thickLine(120 + r0 * sinf(a), 120 - r0 * cosf(a), 120 + r1 * sinf(a), 120 - r1 * cosf(a), 3, C_ACC);
    else spr.drawLine(120 + r0 * sinf(a), 120 - r0 * cosf(a), 120 + r1 * sinf(a), 120 - r1 * cosf(a), C_DIM2);
  }
  char b[24];
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(C_GRAY);
  snprintf(b, sizeof b, "%s %02d %s", DOW[t.tm_wday], t.tm_mday, MON[t.tm_mon]);
  spr.drawString(b, 120, 78, 2);
  spr.fillCircle(120, 60, 3, timeSynced ? C_OK : C_WARN);      // green = synced (host/NTP), amber = free-running
  spr.setTextColor(C_TXT);
  snprintf(b, sizeof b, "%02d:%02d", t.tm_hour, t.tm_min);
  spr.drawString(b, 120, 166, 4);
  hand(((t.tm_hour % 12) + t.tm_min / 60.0f) * 30.0f, 52, 6, C_TXT);
  hand((t.tm_min + t.tm_sec / 60.0f) * 6.0f, 82, 4, C_ACC);
  hand(t.tm_sec * 6.0f, 94, 2, C_RED);
  spr.fillCircle(120, 120, 6, C_TXT);
  spr.fillCircle(120, 120, 3, C_RED);
}

static void scenePomo() {
  uint32_t total = pomoMinutes * 60000UL;
  uint32_t rem = (pomoState == PS_IDLE) ? total : pomoRemain;
  bool blink = (pomoState == PS_DONE) && ((millis() / 400) & 1);
  uint16_t col = pomoState == PS_RUN ? C_ACC : pomoState == PS_PAUSE ? C_WARN : pomoState == PS_DONE ? C_RED : C_OK;
  const char* label = pomoState == PS_RUN ? "FOCUS" : pomoState == PS_PAUSE ? "PAUSED" : pomoState == PS_DONE ? "DONE!" : "READY";
  spr.fillSprite(blink ? rgb(90, 0, 0) : C_BG);
  arcBand(120, 120, 116, 102, 0, 360, C_DIM);
  float f = total ? (float)rem / total : 0;
  if (f > 0.003f) arcBand(120, 120, 116, 102, 0, 360 * f, col);
  uint32_t s = (rem + 999) / 1000; char b[16];
  snprintf(b, sizeof b, "%02lu:%02lu", (unsigned long)(s / 60), (unsigned long)(s % 60));
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(col); spr.drawString(label, 120, 64, 2);
  spr.setTextColor(C_TXT); spr.drawString(b, 120, 112, 7);
  spr.setTextColor(C_GRAY);
  spr.drawString("K1 start/pause", 120, 158, 2);
  spr.drawString("K2 reset", 120, 176, 2);
  if (pomoState == PS_IDLE) spr.drawString("turn dial: minutes", 120, 196, 2);
}

static void sceneMedia() {
  spr.fillSprite(C_BG);
  gauge(120, 120, 116, 102, muted ? 0 : vol / 100.0f, C_ACC);
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(C_GRAY); spr.drawString("MEDIA", 120, 50, 2);
  if (playing) { spr.fillRect(97, 72, 16, 46, C_TXT); spr.fillRect(127, 72, 16, 46, C_TXT); }
  else spr.fillTriangle(100, 70, 100, 120, 146, 95, C_TXT);
  for (int b = 0; b < 7; b++) {                        // equalizer bars: animate while "playing"
    int h = playing ? 6 + (int)(16 * (0.5f + 0.5f * sinf(millis() / 140.0f + b * 1.3f))) : 4;
    spr.fillRect(78 + b * 12, 154 - h, 8, h, playing ? C_ACC2 : C_DIM2);
  }
  char b[16];
  if (muted) snprintf(b, sizeof b, "MUTED"); else snprintf(b, sizeof b, "%d%%", vol);
  spr.setTextColor(muted ? C_RED : C_TXT); spr.drawString(b, 120, 180, 4);
  spr.setTextColor(C_GRAY); spr.drawString("VOLUME", 120, 204, 2);
}

static void sceneTelemetry() {
  bool live = lastStatsMs && (millis() - lastStatsMs < 3500);
  float vcc = VCC_SENSE_PIN >= 0 ? analogReadMilliVolts(VCC_SENSE_PIN) * VCC_DIVIDER / 1000.0f : 0;
  float temp = chipTemp();
  uint32_t heapTot = ESP.getHeapSize(), heapFree = ESP.getFreeHeap();
  spr.fillSprite(C_BG);
  gauge(120, 120, 116, 103, live ? hostCpu / 100.0f : 1.0f - (float)heapFree / heapTot, C_ACC);
  gauge(120, 120, 98, 85, live ? hostRam / 100.0f : constrain(temp / 80.0f, 0.0f, 1.0f), C_ACC2);
  char b[24];
  spr.setTextDatum(MC_DATUM);
  if (live) {
    spr.setTextColor(C_OK); spr.drawString("HOST LIVE", 120, 62, 2);
    spr.setTextColor(C_ACC); snprintf(b, sizeof b, "CPU %d%%", hostCpu); spr.drawString(b, 120, 98, 4);
    spr.setTextColor(C_ACC2); snprintf(b, sizeof b, "RAM %d%%", hostRam); spr.drawString(b, 120, 134, 4);
    spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%.0f C", temp); spr.drawString(b, 120, 170, 2);
  } else {
    uint32_t up = millis() / 1000;
    spr.setTextColor(C_GRAY); spr.drawString("STANDALONE", 120, 62, 2);
    snprintf(b, sizeof b, "%02lu:%02lu:%02lu", (unsigned long)(up / 3600), (unsigned long)((up / 60) % 60), (unsigned long)(up % 60));
    spr.setTextColor(C_TXT); spr.drawString(b, 120, 96, 4);
    spr.setTextColor(C_GRAY); spr.drawString("UPTIME", 120, 116, 2);
    snprintf(b, sizeof b, "%.1f C", temp); spr.setTextColor(C_ACC2); spr.drawString(b, 120, 142, 4);
    if (VCC_SENSE_PIN >= 0) snprintf(b, sizeof b, "VCC %.2f V", vcc); else snprintf(b, sizeof b, "HEAP %luK", (unsigned long)(heapFree / 1024));
    spr.setTextColor(C_GRAY); spr.drawString(b, 120, 172, 2);
  }
}

static void sceneGifMsg() {
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_WARN);
  spr.drawString(gifFailed ? "GIF ERROR" : "GIF", 120, 104, 4);
  spr.setTextColor(C_GRAY);
  spr.drawString("upload a .gif with", 120, 136, 2);
  spr.drawString("the companion app", 120, 154, 2);
}

static void sceneUpload() {
  spr.fillSprite(C_BG);
  float f = upSize ? (float)upRx / upSize : 0;
  gauge(120, 120, 116, 102, f, C_ACC2);
  char b[24];
  spr.setTextDatum(MC_DATUM);
  spr.setTextColor(C_GRAY); spr.drawString("UPLOADING GIF", 120, 84, 2);
  spr.setTextColor(C_TXT); snprintf(b, sizeof b, "%d%%", (int)(f * 100)); spr.drawString(b, 120, 120, 4);
  spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%lu KB", (unsigned long)(upRx / 1024)); spr.drawString(b, 120, 152, 2);
}

static void sceneMenu() {                              // radial overlay: BRIGHT / VOL / MODE / LAYER / EXIT around the ring
  static const char* const LBL[5] = {"BRIGHT", "VOL", "MODE", "LAYER", "EXIT"};
  static const char* const FULL[5] = {"BRIGHTNESS", "VOLUME", "DISPLAY MODE", "KEY LAYER", "EXIT"};
  dimSprite();
  spr.setTextDatum(MC_DATUM);
  for (int i = 0; i < 5; i++) {
    float c = i * 72.0f; bool sel = (i == menuSel);
    arcBand(120, 120, 118, 72, c - 34, c + 34, sel ? (menuEdit ? C_WARN : C_ACC) : C_DIM2);
    float a = c * DEG_TO_RAD;
    spr.setTextColor(sel ? C_BG : C_TXT);
    spr.drawString(LBL[i], 120 + lroundf(95 * sinf(a)), 120 - lroundf(95 * cosf(a)), 2);
  }
  spr.fillCircle(120, 120, 66, C_BG);
  spr.drawCircle(120, 120, 66, C_DIM2);
  char b[20];
  spr.setTextColor(C_GRAY); spr.drawString(FULL[menuSel], 120, 82, 2);
  spr.setTextColor(menuEdit ? C_WARN : C_TXT);
  switch (menuSel) {
    case 0: snprintf(b, sizeof b, "%d%%", brightness * 100 / 255); spr.drawString(b, 120, 118, 4); break;
    case 1: if (muted) snprintf(b, sizeof b, "MUTE"); else snprintf(b, sizeof b, "%d%%", vol); spr.drawString(b, 120, 118, 4); break;
    case 2: {
      uint8_t m = menuEdit ? menuMode : mode;
      snprintf(b, sizeof b, "%d / %d", m, NUM_MODES); spr.drawString(b, 120, 112, 4);
      spr.setTextColor(C_ACC); spr.drawString(MODE_NAME[m], 120, 136, 2);
      break;
    }
    case 3: snprintf(b, sizeof b, "%d / %d", (menuEdit ? menuLayer : curLayer) + 1, LAYERS); spr.drawString(b, 120, 118, 4); break;
    default: spr.drawString("CLOSE", 120, 118, 4); break;
  }
  spr.setTextColor(C_GRAY);
  spr.drawString(menuEdit ? "CLICK = OK" : "CLICK = ENTER", 120, 158, 1);
}

// small status overlays drawn on top of every sprite scene: layer badge + key-press ring
static void drawOverlays() {
  uint32_t now = millis();
  if (curLayer > 0 || (int32_t)(layerToastUntil - now) > 0) {
    char b[8]; snprintf(b, sizeof b, "L%d", curLayer + 1);
    uint16_t c = curLayer == 0 ? C_ACC : curLayer == 1 ? C_ACC2 : C_WARN;
    spr.fillRoundRect(100, 214, 40, 20, 8, c);
    spr.setTextDatum(MC_DATUM); spr.setTextColor(C_BG); spr.drawString(b, 120, 224, 2);
  }
  uint32_t age = now - flashAt;
  if (flashAt && age < 260) {                                    // ring that fades while it shrinks - feedback for every key press
    int th = 10 - (int)(age / 28);
    if (th > 0) arcBand(120, 120, 119, 119 - th, 0, 360, curLayer == 0 ? C_ACC : curLayer == 1 ? C_ACC2 : C_WARN);
  }
}

// ---- info screen (mode 6): cards pushed by the companion app - now playing, weather, next event, anything custom
static String fitStr(const String& s, unsigned maxc) {
  if (s.length() <= maxc) return s;
  return s.substring(0, maxc > 2 ? maxc - 2 : maxc) + "..";
}
static void sceneInfo() {
  uint32_t now = millis();
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM);
  bool fresh = infoRxAt && (now - infoRxAt) < INFO_STALE_MS && nCards > 0;
  if (!fresh) {
    arcBand(120, 120, 116, 108, 0, 360, C_DIM);
    spr.setTextColor(C_GRAY); spr.drawString("INFO", 120, 84, 2);
    spr.setTextColor(C_TXT); spr.drawString("no data", 120, 118, 4);
    spr.setTextColor(C_GRAY); spr.drawString("open the companion app", 120, 152, 2);
    spr.drawString("(Info tab)", 120, 170, 2);
  } else {
    const InfoCard& c = cards[cardIdx % nCards];
    uint16_t col = c.kind == 'm' ? C_ACC2 : c.kind == 'w' ? C_ACC : c.kind == 'e' ? C_WARN : C_OK;
    arcBand(120, 120, 116, 108, 0, 360, C_DIM);
    if (nCards > 1 && cardRotSec) {                                // ring fills over the rotation interval
      float f = constrain((float)(now - cardAt) / (cardRotSec * 1000.0f), 0.0f, 1.0f);
      arcBand(120, 120, 116, 108, 0, 360 * f, col);
    } else arcBand(120, 120, 116, 108, 0, 360, col);
    spr.setTextColor(C_GRAY); spr.drawString(fitStr(c.label, 18), 120, 52, 2);
    spr.setTextColor(col);   spr.drawString(fitStr(c.t, 11), 120, 98, 4);
    spr.setTextColor(C_TXT); spr.drawString(fitStr(c.a, 20), 120, 136, 2);
    spr.setTextColor(C_GRAY); spr.drawString(fitStr(c.b, 20), 120, 156, 2);
    for (uint8_t i = 0; nCards > 1 && i < nCards; i++) spr.fillCircle(120 + (int)(i - (nCards - 1) * 0.5f) * 12, 182, i == cardIdx % nCards ? 3 : 2, i == cardIdx % nCards ? col : C_DIM2);
  }
  for (uint8_t i = 0; i < nBadges && i < 4; i++) {                 // unread / notification counters along the bottom
    if (!badges[i].n) continue;
    int x = 120 + ((int)i - (nBadges - 1) * 0.5f) * 46;
    char b[8]; snprintf(b, sizeof b, "%u", (unsigned)(badges[i].n > 99 ? 99 : badges[i].n));
    spr.fillRoundRect(x - 20, 196, 40, 22, 8, C_ACC2);
    spr.setTextColor(C_BG); spr.drawString(b, x, 207, 2);
    spr.setTextColor(C_GRAY); spr.drawString(fitStr(badges[i].name, 6), x, 228, 1);
  }
}

// ================================================================ v1.4 screens 7-12 (optional: see settings "mode_mask")
static void fmtTime(char* b, size_t n, uint32_t ms) {            // "MM:SS" or "H:MM:SS"
  uint32_t t = ms / 1000;
  if (t >= 3600) snprintf(b, n, "%lu:%02lu:%02lu", (unsigned long)(t / 3600), (unsigned long)((t / 60) % 60), (unsigned long)(t % 60));
  else snprintf(b, n, "%02lu:%02lu", (unsigned long)(t / 60), (unsigned long)(t % 60));
}
static uint32_t swElapsed() { return swRun ? swAccum + (millis() - swStartAt) : swAccum; }
static void sceneStopwatch() {
  uint32_t e = swElapsed(); char b[24];
  spr.fillSprite(C_BG);
  gauge(120, 120, 116, 104, (e % 60000) / 59999.0f, swRun ? C_ACC : C_DIM2);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY); spr.drawString("STOPWATCH", 120, 46, 2);
  fmtTime(b, sizeof b, e); spr.setTextColor(swRun ? C_TXT : C_WARN); spr.drawString(b, 120, 98, e >= 3600000UL ? 6 : 7);
  snprintf(b, sizeof b, ".%02lu", (unsigned long)((e % 1000) / 10)); spr.setTextColor(C_ACC); spr.drawString(b, 120, 142, 4);
  spr.setTextColor(C_GRAY);
  for (int i = 0; i < 2 && i < swLapN; i++) {
    char t[16]; fmtTime(t, sizeof t, swLaps[i]); snprintf(b, sizeof b, "LAP %d  %s", swLapN - i, t); spr.drawString(b, 120, 168 + i * 17, 2);
  }
  if (!swLapN) spr.drawString(swRun ? "K2 = lap" : "K1 = start", 120, 172, 2);
  spr.setTextColor(C_DIM2); spr.drawString(swRun ? "K1 stop" : (e ? "K1 go  K2 reset" : ""), 120, 206, 1);
}
static const uint8_t BR_PHASES[4][4] = {{4, 7, 8, 0}, {4, 4, 4, 4}, {5, 0, 5, 0}, {4, 0, 6, 0}};    // seconds: in, hold, out, hold
static const char* const BR_NAMES[4] = {"4-7-8", "BOX 4-4-4-4", "EVEN 5-5", "RELAX 4-6"};
static void sceneBreathe() {
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY); spr.drawString(BR_NAMES[brPattern], 120, 40, 2);
  uint32_t cyc = 0; for (int i = 0; i < 4; i++) cyc += BR_PHASES[brPattern][i] * 1000UL;
  float r = 34; const char* lbl = "READY"; int secLeft = 0;
  if (brRun) {
    uint32_t t = (millis() - brStartAt) % cyc, acc = 0;
    static const char* const PL[4] = {"BREATHE IN", "HOLD", "BREATHE OUT", "HOLD"};
    for (int i = 0; i < 4; i++) {
      uint32_t len = BR_PHASES[brPattern][i] * 1000UL;
      if (!len) continue;
      if (t < acc + len) {
        float f = (t - acc) / (float)len;
        r = i == 0 ? 34 + 56 * f : i == 1 ? 90 : i == 2 ? 90 - 56 * f : 34;
        lbl = PL[i]; secLeft = (int)((len - (t - acc)) / 1000) + 1; break;
      }
      acc += len;
    }
  }
  spr.fillCircle(120, 120, (int)r, rgb(0, 70, 95)); spr.drawCircle(120, 120, (int)r, C_ACC); spr.drawCircle(120, 120, (int)r + 1, C_ACC);
  spr.setTextColor(C_TXT); spr.drawString(lbl, 120, 120, brRun ? 2 : 4);
  char b[8]; if (brRun) { snprintf(b, sizeof b, "%d", secLeft); spr.setTextColor(C_ACC); spr.drawString(b, 120, 146, 4); }
  spr.setTextColor(C_DIM2); spr.drawString(brRun ? "K1 stop  K2 pattern" : "K1 start  K2 pattern", 120, 208, 1);
}
static const char* const TOY_NAME[5] = {"DICE D6", "DICE D20", "COIN", "8-BALL", "D100"};
static const char* const BALL[8] = {"YES", "NO", "MAYBE", "ASK LATER", "DEFINITELY", "UNLIKELY", "LOOKS GOOD", "DOUBTFUL"};
static int toyRoll(uint8_t kind) {
  switch (kind) { case 0: return 1 + esp_random() % 6; case 1: return 1 + esp_random() % 20; case 2: return esp_random() % 2; case 3: return esp_random() % 8; default: return 1 + esp_random() % 100; }
}
static void sceneToys() {
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY);
  spr.setTextColor(C_ACC); spr.drawString(TOY_NAME[toyKind], 120, 48, 2);
  bool rolling = (int32_t)(toyRollUntil - millis()) > 0;
  int v = rolling ? toyRoll(toyKind) : toyResult; char b[16];
  spr.setTextColor(rolling ? C_GRAY : C_TXT);
  if (toyKind == 2) spr.drawString(v ? "HEADS" : "TAILS", 120, 120, 4);
  else if (toyKind == 3) spr.drawString(BALL[v & 7], 120, 120, 4);
  else { snprintf(b, sizeof b, "%d", v); spr.drawString(b, 120, 120, 7); }
  spr.setTextColor(C_DIM2); spr.drawString("K1 d6  K2 d20  K3 coin", 120, 192, 1); spr.drawString("K4 8-ball  K5 d100", 120, 206, 1);
}
static void sceneReact() {
  uint16_t bg = rxState == 1 ? rgb(120, 0, 0) : rxState == 2 ? rgb(0, 140, 40) : C_BG;
  spr.fillSprite(bg);
  spr.setTextDatum(MC_DATUM); char b[24];
  spr.setTextColor(C_TXT);
  if (rxState == 0) { spr.setTextColor(C_GRAY); spr.drawString("REACTION", 120, 70, 2); spr.setTextColor(C_TXT); spr.drawString("PRESS ANY KEY", 120, 110, 2); spr.drawString("TO START", 120, 130, 2); }
  else if (rxState == 1) spr.drawString("WAIT...", 120, 120, 4);
  else if (rxState == 2) spr.drawString("GO!", 120, 120, 7);
  else if (rxState == 3) { snprintf(b, sizeof b, "%u ms", rxMs); spr.drawString(b, 120, 108, 4); spr.setTextColor(rxMs < 250 ? C_OK : C_WARN); spr.drawString(rxMs < 200 ? "FAST!" : rxMs < 300 ? "GOOD" : "SLOW", 120, 140, 2); }
  else { spr.setTextColor(C_WARN); spr.drawString("TOO SOON", 120, 120, 4); }
  if (rxBest) { snprintf(b, sizeof b, "BEST %u ms", rxBest); spr.setTextColor(C_GRAY); spr.drawString(b, 120, 196, 2); }
}
static const int8_t SN_DX[4] = {0, 1, 0, -1}, SN_DY[4] = {-1, 0, 1, 0};
static bool snCellOk(int x, int y) { if (x < 0 || x > 10 || y < 0 || y > 10) return false; int dx = x - 5, dy = y - 5; return dx * dx + dy * dy <= 29; }
static void snakeFood() {
  for (int tries = 0; tries < 300; tries++) {
    int x = esp_random() % 11, y = esp_random() % 11;
    if (!snCellOk(x, y)) continue;
    bool hit = false; for (int i = 0; i < snLen; i++) if (snX[i] == x && snY[i] == y) { hit = true; break; }
    if (!hit) { snFx = (uint8_t)x; snFy = (uint8_t)y; return; }
  }
  snFx = snFy = 255;
}
static void snakeNew() {
  snLen = 3; snDir = 1; snTurn = 0;
  for (int i = 0; i < 3; i++) { snX[i] = 5 - i; snY[i] = 5; }
  snakeFood(); snRun = true; snOver = false; snAt = millis() + 500; needRedraw = true;
}
static void snakeTurn(int steps) { snTurn = (int8_t)(steps > 0 ? 1 : -1); }
static void snakeTick() {
  if (!snRun || mode != M_SNAKE) return;
  uint32_t now = millis();
  if ((int32_t)(now - snAt) < 0) return;
  snAt = now + (uint32_t)max(70, 170 - (snLen - 3) * 5);
  if (snTurn > 0) snDir = (snDir + 1) & 3; else if (snTurn < 0) snDir = (snDir + 3) & 3;
  snTurn = 0;
  int nx = snX[0] + SN_DX[snDir], ny = snY[0] + SN_DY[snDir];
  bool eat = (nx == snFx && ny == snFy);
  bool dead = !snCellOk(nx, ny);
  for (int i = 0; !dead && i < snLen - (eat ? 0 : 1); i++) if (snX[i] == nx && snY[i] == ny) dead = true;     // the tail cell frees up unless we grow
  if (dead) {
    snRun = false; snOver = true;
    if ((uint16_t)(snLen - 3) > snBest) { snBest = (uint16_t)(snLen - 3); prefs.putUShort("snkb", snBest); }
    needRedraw = true; return;
  }
  if (eat && snLen < 99) snLen++;
  for (int i = snLen - 1; i > 0; i--) { snX[i] = snX[i - 1]; snY[i] = snY[i - 1]; }
  snX[0] = (int8_t)nx; snY[0] = (int8_t)ny;
  if (eat) snakeFood();
  needRedraw = true;
}
static void sceneSnake() {
  spr.fillSprite(C_BG);
  spr.drawCircle(120, 120, 119, C_DIM2);
  for (int y = 0; y < 11; y++) for (int x = 0; x < 11; x++) if (snCellOk(x, y)) spr.drawRect(21 + x * 18, 21 + y * 18, 18, 18, C_DIM);
  if (snFx != 255) spr.fillRect(21 + snFx * 18 + 3, 21 + snFy * 18 + 3, 12, 12, C_RED);
  for (int i = snLen - 1; i >= 0; i--) spr.fillRect(21 + snX[i] * 18 + 1, 21 + snY[i] * 18 + 1, 16, 16, i == 0 ? C_OK : rgb(30, 150, 80));
  spr.setTextDatum(MC_DATUM); char b[24];
  if (!snRun) {
    spr.fillRoundRect(40, 90, 160, 60, 8, C_BG); spr.drawRoundRect(40, 90, 160, 60, 8, C_ACC);
    spr.setTextColor(snOver ? C_RED : C_ACC);
    if (snOver) { snprintf(b, sizeof b, "GAME OVER  %d", snLen - 3); spr.drawString(b, 120, 108, 2); } else spr.drawString("SNAKE", 120, 108, 4);
    spr.setTextColor(C_GRAY); spr.drawString("K1 = start", 120, 132, 2);
  } else { spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "%d", snLen - 3); spr.drawString(b, 120, 12, 2); }
}
// ---- habits: five daily toggles with an 8-day history each (bit 0 = today); streak = consecutive days up to today / yesterday
static uint8_t habStreak(uint8_t h) {
  uint8_t i = (h & 1) ? 0 : 1, n = 0;
  while (i < 8 && ((h >> i) & 1)) { n++; i++; }
  return n;
}
static void habitSave() {
  prefs.putULong("hb_d", habDay);
  for (int i = 0; i < 5; i++) { char k[6]; snprintf(k, sizeof k, "hb_%d", i); prefs.putUChar(k, habHist[i]); }
}
static void habitRoll() {
  if (!timeSynced) return;
  uint32_t day = (uint32_t)(((int64_t)time(nullptr) + tzOff) / 86400);
  if (habDay == 0 || day < habDay) { habDay = day; habitSave(); return; }
  if (day > habDay) {
    uint32_t sh = day - habDay;
    for (int i = 0; i < 5; i++) habHist[i] = sh >= 8 ? 0 : (uint8_t)(habHist[i] << sh);
    habDay = day; habitSave();
  }
}
static void sceneHabits() {
  habitRoll();
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY); spr.drawString("HABITS", 120, 38, 2);
  if (!timeSynced) { spr.setTextColor(C_WARN); spr.drawString("CLOCK NOT SET", 120, 112, 4); spr.setTextColor(C_GRAY); spr.drawString("open the companion app", 120, 146, 2); return; }
  char b[24];
  for (int i = 0; i < 5; i++) {
    int y = 66 + i * 25; bool done = habHist[i] & 1;
    if (done) spr.fillCircle(66, y, 8, C_OK); else spr.drawCircle(66, y, 8, C_DIM2);
    spr.setTextDatum(ML_DATUM); spr.setTextColor(done ? C_TXT : C_GRAY); spr.drawString(habName[i], 82, y, 2);
    spr.setTextDatum(MR_DATUM); snprintf(b, sizeof b, "%dd", habStreak(habHist[i])); spr.setTextColor(habStreak(habHist[i]) ? C_ACC : C_DIM2); spr.drawString(b, 176, y, 2);
  }
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_DIM2); spr.drawString("K1-K5 toggle today", 120, 204, 1);
}
static bool modeKey(int i) {                                      // a screen may claim some keys (like the focus timer claims K1 / K2); true = handled
  uint32_t now = millis();
  switch (mode) {
    case M_STOPW:
      if (i == 0) { if (swRun) { swAccum += now - swStartAt; swRun = false; } else { swStartAt = now; swRun = true; } needRedraw = true; return true; }
      if (i == 1) {
        if (swRun) { for (int k = 3; k > 0; k--) swLaps[k] = swLaps[k - 1]; swLaps[0] = swElapsed(); if (swLapN < 4) swLapN++; }
        else { swAccum = 0; swLapN = 0; }
        needRedraw = true; return true;
      }
      return false;
    case M_BREATH:
      if (i == 0) { brRun = !brRun; brStartAt = now; needRedraw = true; return true; }
      if (i == 1) { brPattern = (brPattern + 1) & 3; brStartAt = now; needRedraw = true; return true; }
      return false;
    case M_TOYS:
      if (i < 5) { toyKind = (uint8_t)i; toyResult = toyRoll(toyKind); toyRollUntil = now + 600; needRedraw = true; return true; }
      return false;
    case M_REACT:
      if (rxState == 2) { rxMs = (uint16_t)min<uint32_t>(now - rxGoAt, 9999); if (!rxBest || rxMs < rxBest) rxBest = rxMs; rxState = 3; }
      else if (rxState == 1) rxState = 4;
      else { rxState = 1; rxAt = now + 1500 + esp_random() % 3500; }
      needRedraw = true; return true;
    case M_SNAKE:
      if (i == 0) { if (snRun) snRun = false; else snakeNew(); needRedraw = true; return true; }
      return false;
    case M_HABIT:
      if (i < 5) { habitRoll(); if (timeSynced) { habHist[i] ^= 1; habitSave(); } needRedraw = true; return true; }
      return false;
  }
  return false;
}
static void reactTick() {
  if (rxState == 1 && (int32_t)(millis() - rxAt) >= 0) { rxState = 2; rxGoAt = millis(); needRedraw = true; }
}

// ---- reminders (drink / stand up / rest your eyes): a full-screen nudge every N minutes, any key or dial turn dismisses it
static void remDismiss() { remActive = false; needRedraw = true; }
static void remTrigger(uint8_t i) {
  remActive = true; remIdx = i; remShownAt = millis(); remUntil = remShownAt + 12000; saverOn = false; needRedraw = true;
  if (hostActive()) { int32_t keep = reqId; reqId = -1; JsonDocument d; d["evt"] = "reminder"; d["i"] = i; d["text"] = remArr[i].text; sendDoc(d); reqId = keep; }
}
static void remTick() {
  uint32_t now = millis();
  if (remActive) {
    if ((int32_t)(now - remUntil) >= 0) { remDismiss(); return; }
    if ((int32_t)(now - remPulseAt) >= 0) { remPulseAt = now + 700; ledFlash(0, 45, 45, 350); }
    return;
  }
  for (uint8_t i = 0; i < 3; i++) {
    if (!remArr[i].min) continue;
    if (!remArr[i].nextAt) { remArr[i].nextAt = now + remArr[i].min * 60000UL; continue; }
    if ((int32_t)(now - remArr[i].nextAt) >= 0) { remArr[i].nextAt = now + remArr[i].min * 60000UL; remTrigger(i); return; }
  }
}
static void sceneReminder() {
  uint32_t now = millis();
  float pulse = 0.5f + 0.5f * sinf(now / 220.0f);
  spr.fillSprite(C_BG);
  arcBand(120, 120, 118, 108 - (int)(6 * pulse), 0, 360, rgb(0, (uint8_t)(120 + 100 * pulse), (uint8_t)(140 + 80 * pulse)));
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY); spr.drawString("REMINDER", 120, 62, 2);
  const char* t = remArr[remIdx].text; spr.setTextColor(C_TXT);
  spr.drawString(t, 120, 118, strlen(t) <= 9 ? 4 : 2);
  spr.setTextColor(C_DIM2); spr.drawString("any key = dismiss", 120, 188, 1);
}

// ---- screensaver (starts after saverSec seconds without input; any key / dial action wakes it)
static uint32_t sRng = 0x1234ABCDu;
static uint32_t sRand() { sRng ^= sRng << 13; sRng ^= sRng >> 17; sRng ^= sRng << 5; return sRng; }
static void sceneSaver() {
  spr.fillSprite(C_BG);
  if (saverStyle == 1) {                                       // starfield: 60 stars fly out of the centre
    static int16_t sx[60], sy[60], sz[60]; static bool init = false;
    if (!init) { init = true; for (int i = 0; i < 60; i++) { sx[i] = (int)(sRand() % 200) - 100; sy[i] = (int)(sRand() % 200) - 100; sz[i] = 1 + sRand() % 120; } }
    for (int i = 0; i < 60; i++) {
      sz[i] -= 2;
      if (sz[i] < 1) { sx[i] = (int)(sRand() % 200) - 100; sy[i] = (int)(sRand() % 200) - 100; sz[i] = 120; }
      int px = 120 + sx[i] * 60 / sz[i], py = 120 + sy[i] * 60 / sz[i];
      if (px < 0 || px > 239 || py < 0 || py > 239) { sz[i] = 120; continue; }
      uint8_t l = (uint8_t)(255 - sz[i] * 2);
      spr.fillRect(px, py, sz[i] < 40 ? 3 : 2, sz[i] < 40 ? 3 : 2, rgb(l, l, l));
    }
  } else if (saverStyle == 2) {                                // matrix rain: 16 columns of falling trails
    static int16_t ry[16], rv[16]; static bool init = false;
    if (!init) { init = true; for (int i = 0; i < 16; i++) { ry[i] = (int)(sRand() % 240); rv[i] = 3 + sRand() % 6; } }
    for (int i = 0; i < 16; i++) {
      int x = 8 + i * 15;
      ry[i] += rv[i]; if (ry[i] > 300) { ry[i] = -(int)(sRand() % 80); rv[i] = 3 + sRand() % 6; }
      for (int k = 0; k < 8; k++) { int y = ry[i] - k * 10; if (y < 0 || y > 239) continue; spr.fillRect(x, y, 6, 8, rgb(0, (uint8_t)(255 - k * 32), (uint8_t)(40 - k * 4))); }
    }
  } else {                                                     // dim clock that drifts every minute (no burn-in)
    struct tm t; localTm(t); char b[8];
    snprintf(b, sizeof b, "%02d:%02d", t.tm_hour, t.tm_min);
    spr.setTextDatum(MC_DATUM); spr.setTextColor(rgb(60, 66, 80));
    spr.drawString(b, 120 + ((t.tm_min * 7) % 41) - 20, 120 + ((t.tm_min * 13) % 41) - 20, 6);
  }
}

static void renderScene() {
  switch (mode) {
    case M_CLOCK: sceneClock(); break;
    case M_POMO:  scenePomo(); break;
    case M_MEDIA: sceneMedia(); break;
    case M_TELEM: sceneTelemetry(); break;
    case M_INFO:  sceneInfo(); break;
    case M_STOPW: sceneStopwatch(); break;
    case M_BREATH: sceneBreathe(); break;
    case M_TOYS:  sceneToys(); break;
    case M_REACT: sceneReact(); break;
    case M_SNAKE: sceneSnake(); break;
    case M_HABIT: sceneHabits(); break;
    default:      sceneGifMsg(); break;
  }
}
static void pushScreen() {
#if DC_HAS_TFT
  if (okDisp) spr.pushSprite(0, 0);
#endif
}
static void renderFrame() {
  if (!okSprite) return;
  if (uploading) sceneUpload();
  else if (remActive && !menuOpen) sceneReminder();
  else if (saverOn && !menuOpen) sceneSaver();
  else {
    if (menuOpen && mode == M_GIF) spr.fillSprite(C_BG); else renderScene();
    if (menuOpen) sceneMenu();
    else if (mode != M_GIF) drawOverlays();
  }
  pushScreen();
}
static uint32_t renderInterval() {
  if (uploading || menuOpen) return 100;
  if (remActive) return 50;
  if (saverOn) return saverStyle == 3 ? 5000 : 45;
  if (flashAt && millis() - flashAt < 280 && mode != M_GIF) return 30;
  switch (mode) {
    case M_POMO:  return (pomoState == PS_RUN || pomoState == PS_DONE) ? 250 : 100000;
    case M_MEDIA: return playing ? 90 : 100000;
    case M_TELEM: return 500;
    case M_GIF:   return 1000;
    case M_INFO:  return 500;
    case M_STOPW: return swRun ? 50 : 1000;
    case M_BREATH: return brRun ? 40 : 1000;
    case M_TOYS:  return (int32_t)(toyRollUntil - millis()) > 0 ? 60 : 1000;
    case M_REACT: return rxState == 1 || rxState == 2 ? 100 : 1000;
    case M_SNAKE: return snRun ? 60 : 1000;
    case M_HABIT: return 1000;
    default:      return 100000;                       // clock redraws on second change
  }
}

// ---- DEMO-GIF-BEGIN ---- (writes a 16-colour "radar" GIF using uncompressed LZW; no encoder library needed)
static FileOut* gOut; static uint8_t gBlk[255]; static uint8_t gBl; static uint32_t gAcc; static uint8_t gNb;
static void lzwByte(uint8_t b) { gBlk[gBl++] = b; if (gBl == 255) { uint8_t l = 255; gOut->put(&l, 1); gOut->put(gBlk, 255); gBl = 0; } }
static void lzwEmit(uint16_t code) { gAcc |= (uint32_t)code << gNb; gNb += 5; while (gNb >= 8) { lzwByte(gAcc & 255); gAcc >>= 8; gNb -= 8; } }
static void lzwFlush() {
  if (gNb > 0) { lzwByte(gAcc & 255); gAcc = 0; gNb = 0; }
  if (gBl) { gOut->put(&gBl, 1); gOut->put(gBlk, gBl); gBl = 0; }
}
static uint8_t demoPix(int x, int y, int f, int F, int S) {
  const float c = (S - 1) * 0.5f, R = S * 0.5f;
  float dx = x - c, dy = y - c, r = sqrtf(dx * dx + dy * dy);
  if (r > R - 0.5f) return 0;
  if (r < 4.0f) return 13;
  float sw = 6.2831853f * f / F;
  float bx = c + R * 0.55f * sinf(sw * 2.0f), by = c - R * 0.55f * cosf(sw * 2.0f);
  if ((x - bx) * (x - bx) + (y - by) * (y - by) < 20.0f) return 14;
  if (fabsf(r - (R - 2)) < 1.1f || fabsf(r - R * 0.66f) < 0.8f || fabsf(r - R * 0.33f) < 0.8f) return 12;
  float ang = atan2f(dx, -dy); if (ang < 0) ang += 6.2831853f;
  float d = sw - ang; if (d < 0) d += 6.2831853f;
  if (d < 2.4f) { int lv = 11 - (int)(d * (10.0f / 2.4f)); return lv < 1 ? 1 : lv; }
  return 0;
}
static void demoGif(FileOut& o, int S, int F) {
  gOut = &o; gBl = 0; gAcc = 0; gNb = 0;
  uint8_t h[13] = {'G', 'I', 'F', '8', '9', 'a', (uint8_t)(S & 255), (uint8_t)(S >> 8), (uint8_t)(S & 255), (uint8_t)(S >> 8), 0xF3, 0, 0};
  o.put(h, 13);
  uint8_t pal[48] = {0};
  for (int i = 1; i <= 11; i++) { pal[i * 3] = 0; pal[i * 3 + 1] = 30 + i * 18; pal[i * 3 + 2] = 50 + i * 18; }
  pal[36] = 60; pal[37] = 66; pal[38] = 80;             // 12 ring grey
  pal[39] = 255; pal[40] = 255; pal[41] = 255;          // 13 white
  pal[42] = 255; pal[43] = 70; pal[44] = 170;           // 14 magenta blip
  o.put(pal, 48);
  static const uint8_t ns[19] = {0x21, 0xFF, 0x0B, 'N', 'E', 'T', 'S', 'C', 'A', 'P', 'E', '2', '.', '0', 0x03, 0x01, 0x00, 0x00, 0x00};
  o.put(ns, 19);
  for (int f = 0; f < F; f++) {
    uint8_t gce[8] = {0x21, 0xF9, 0x04, 0x04, 10, 0, 0, 0};           // 100 ms, disposal 1
    o.put(gce, 8);
    uint8_t id[11] = {0x2C, 0, 0, 0, 0, (uint8_t)(S & 255), (uint8_t)(S >> 8), (uint8_t)(S & 255), (uint8_t)(S >> 8), 0x00, 4};   // last byte: LZW min code size
    o.put(id, 11);
    lzwEmit(16);
    int run = 0;
    for (int y = 0; y < S; y++) {
      o.tick();
      for (int x = 0; x < S; x++) {
        lzwEmit(demoPix(x, y, f, F, S));
        if (++run == 12) { lzwEmit(16); run = 0; }                    // clear code keeps the code width at 5 bits
      }
    }
    lzwEmit(17);
    lzwFlush();
    uint8_t z = 0; o.put(&z, 1);
  }
  uint8_t tr = 0x3B; o.put(&tr, 1);
}
// ---- DEMO-GIF-END ----

static bool makeDemoGif() {
  FileOut o;
  o.f = LittleFS.open("/anim.tmp", "w");
  if (!o.f) return false;
  demoGif(o, 160, 10);
  o.f.close();
  LittleFS.remove("/anim.gif");
  return LittleFS.rename("/anim.tmp", "/anim.gif");
}

// Mounting a blank flash partition formats it, which takes 10-30 s on real hardware - far too long to block setup():
// the USB link, LED and command interpreter must already be answering. So the filesystem is brought up by a
// background task; okFs flips to true when it is done, and every filesystem user checks okFs first.
#ifndef ARDUINO_RUNNING_CORE
#define ARDUINO_RUNNING_CORE (portNUM_PROCESSORS - 1)
#endif
static void fsTask(void* arg) {
  (void)arg;
  bool ok = LittleFS.begin(false);
  if (!ok) { fsState = FS_FORMATTING; ok = LittleFS.begin(true); }
  if (ok && !safeMode && !LittleFS.exists("/anim.gif") && !LittleFS.exists("/anim1.gif") && !LittleFS.exists("/anim2.gif") && !LittleFS.exists("/anim3.gif")) { fsState = FS_PREPARING; if (!makeDemoGif()) demoFailed = true; }
  fsState = ok ? FS_READY : FS_FAILED;
  okFs = ok;
  gifRestart = true;
  vTaskDelete(NULL);
}
static void fsPrepTask(void* arg) {                       // regenerate the built-in demo animation after "gif_delete"
  (void)arg;
  if (!safeMode && !makeDemoGif()) demoFailed = true;
  fsState = FS_READY;
  okFs = true;
  gifRestart = true;
  vTaskDelete(NULL);
}
static void fsStartAsync(TaskFunction_t fn, uint8_t state) {
  fsState = state;
  if (xTaskCreatePinnedToCore(fn, "fs", 12288, nullptr, 1, nullptr, ARDUINO_RUNNING_CORE) != pdPASS) {
    fsState = FS_FAILED;                                   // out of memory: leave okFs as it is
    if (fn == fsPrepTask) okFs = true;
  }
}

// ================================================================ GIF slots: /anim.gif (0) and /anim1.gif .. /anim3.gif, optional rotation
static void gifPath(uint8_t slot, char* out) { if (slot == 0) strcpy(out, "/anim.gif"); else snprintf(out, 16, "/anim%u.gif", slot); }
static bool gifSlotExists(uint8_t slot) {
  if (!okFs || slot >= GIF_SLOTS) return false;
  char p[16]; gifPath(slot, p);
  return LittleFS.exists(p);
}
static uint8_t gifSlotCount() { uint8_t n = 0; for (uint8_t i = 0; i < GIF_SLOTS; i++) if (gifSlotExists(i)) n++; return n; }
static int gifNextSlot(int from) {                       // next existing slot after `from` (wraps), -1 if none
  for (int k = 1; k <= GIF_SLOTS; k++) { int i = (from + k) % GIF_SLOTS; if (gifSlotExists((uint8_t)i)) return i; }
  return -1;
}

// ================================================================ GIF playback (AnimatedGIF + LittleFS, drawn straight to the panel)
static void* GIFOpenFile(const char* fname, int32_t* pSize) {
  gifFile = LittleFS.open(fname, "r");
  if (gifFile) { *pSize = gifFile.size(); return (void*)&gifFile; }
  return NULL;
}
static void GIFCloseFile(void* pHandle) { fs::File* f = static_cast<fs::File*>(pHandle); if (f) f->close(); }
static int32_t GIFReadFile(GIFFILE* pFile, uint8_t* pBuf, int32_t iLen) {
  int32_t n = iLen;
  fs::File* f = static_cast<fs::File*>(pFile->fHandle);
  if ((pFile->iSize - pFile->iPos) < iLen) n = pFile->iSize - pFile->iPos - 1;
  if (n <= 0) return 0;
  n = (int32_t)f->read(pBuf, n);
  pFile->iPos = f->position();
  return n;
}
static int32_t GIFSeekFile(GIFFILE* pFile, int32_t iPosition) {
  fs::File* f = static_cast<fs::File*>(pFile->fHandle);
  f->seek(iPosition);
  pFile->iPos = (int32_t)f->position();
  return pFile->iPos;
}
static void GIFDraw(GIFDRAW* pDraw) {
  static uint16_t line[320];
  int y = pDraw->iY + pDraw->y + gifOffY;
  int x0 = pDraw->iX + gifOffX, w = pDraw->iWidth;
  if (y < 0 || y >= 240 || x0 >= 240 || w < 1) return;
  if (x0 + w > 240) w = 240 - x0;
  uint8_t* s = pDraw->pPixels;
  uint16_t* pal = pDraw->pPalette;
  if (pDraw->ucDisposalMethod == 2) {                  // restore-to-background: opaque background instead of "transparent"
    for (int x = 0; x < w; x++) if (s[x] == pDraw->ucTransparent) s[x] = pDraw->ucBackground;
    pDraw->ucHasTransparency = 0;
  }
  if (pDraw->ucHasTransparency) {                      // draw runs of opaque pixels, skip transparent ones
    uint8_t tr = pDraw->ucTransparent;
    int x = 0;
    while (x < w) {
      int n = 0;
      while (x + n < w && s[x + n] != tr) { line[n] = pal[s[x + n]]; n++; }
      if (n) {
#if DC_HAS_TFT
        tft.setAddrWindow(x0 + x, y, n, 1); tft.pushPixels(line, n);
#endif
        x += n;
      }
      while (x < w && s[x] == tr) x++;
    }
  } else {
    for (int x = 0; x < w; x++) line[x] = pal[s[x]];
#if DC_HAS_TFT
    tft.setAddrWindow(x0, y, w, 1);
    tft.pushPixels(line, w);
#endif
  }
}
static void gifClose() { if (gifOpen) { gif.close(); gifOpen = false; } }
static void gifBegin() {
  gifFailed = false;
  if (!gifSlotExists(gifSlot)) { int n = gifNextSlot(gifSlot); if (n >= 0) gifSlot = (uint8_t)n; }
  if (!gifSlotExists(gifSlot)) {
    if (demoFailed) { gifFailed = true; needRedraw = true; return; }
    spr.fillSprite(C_BG); spr.setTextDatum(MC_DATUM); spr.setTextColor(C_TXT);
    spr.drawString("Preparing demo", 120, 110, 4); spr.drawString("animation...", 120, 140, 4);
    pushScreen();
    okFs = false;                                           // regenerate the demo in the background; gifRestart is set when done
    fsStartAsync(fsPrepTask, FS_PREPARING);
    return;
  }
#if DC_HAS_TFT
  tft.fillScreen(TFT_BLACK);
#endif
  char gp[16]; gifPath(gifSlot, gp);
  gifSlotAt = millis();
  if (gif.open(gp, GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw)) {
    gifOpen = true;
    gifOffX = (240 - gif.getCanvasWidth()) / 2; gifOffY = (240 - gif.getCanvasHeight()) / 2;
    if (gifOffX < 0) gifOffX = 0;
    if (gifOffY < 0) gifOffY = 0;
    gifNextAt = 0;
  } else { gifFailed = true; needRedraw = true; }
}
static void gifService() {
  if (!okFs) return;                                      // filesystem still starting / being rewritten
  if (gifRot && gifOpen && (millis() - gifSlotAt) > (uint32_t)gifRot * 1000UL) {      // rotate to the next stored animation
    int n = gifNextSlot(gifSlot);
    gifSlotAt = millis();
    if (n >= 0 && n != gifSlot) { gifSlot = (uint8_t)n; gifRestart = true; }
  }
  if (gifRestart) { gifClose(); gifRestart = false; gifBegin(); }
  if (!gifOpen || (int32_t)(millis() - gifNextAt) < 0) return;
  int d = 0;
#if DC_HAS_TFT
  tft.startWrite();
#endif
  int r = gif.playFrame(false, &d);
#if DC_HAS_TFT
  tft.endWrite();
#endif
  if (r == 0) gif.reset();                              // finished the last frame: loop
  else if (r < 0) { gifClose(); gifFailed = true; needRedraw = true; return; }
  gifNextAt = millis() + (d < 10 ? 10 : d);
}

// ================================================================ modes, menu, pomodoro
static void setMode(uint8_t m) {
  if (m < 1 || m > NUM_MODES) return;
  if (m != mode) prefs.putUChar("mode", m);
  mode = m; needRedraw = true;
  if (m != M_SNAKE) snRun = false;                                // leaving a game pauses / resets it
  if (m != M_REACT) rxState = 0;
  if (mode == M_GIF) gifRestart = true; else gifClose();
}
static void pomoToggle() {
  if (pomoState == PS_IDLE || pomoState == PS_DONE) { pomoRemain = pomoMinutes * 60000UL; pomoState = PS_RUN; prefs.putUChar("pomo", pomoMinutes); }
  else if (pomoState == PS_RUN) pomoState = PS_PAUSE;
  else pomoState = PS_RUN;
  pomoLast = millis(); needRedraw = true;
}
static void pomoReset() { pomoState = PS_IDLE; pomoRemain = pomoMinutes * 60000UL; needRedraw = true; }
static void pomoTick() {
  uint32_t now = millis();
  if (pomoState == PS_RUN) {
    uint32_t dt = now - pomoLast; pomoLast = now;
    if (dt >= pomoRemain) { pomoRemain = 0; pomoState = PS_DONE; pomoDoneAt = now; needRedraw = true; }
    else pomoRemain -= dt;
  } else if (pomoState == PS_DONE && now - pomoDoneAt > 15000) pomoReset();
}
static void menuCloseNow() {
  if (brightness != savedBright) { prefs.putUChar("bright", brightness); savedBright = brightness; }
  menuOpen = false; menuEdit = 0; needRedraw = true;
  if (mode == M_GIF) gifRestart = true;
}
static uint8_t nextEnabledMode(uint8_t m, int dir) {            // next screen in the dial-menu / long-press cycle (screens switched off in mode_mask are skipped)
  for (int i = 1; i <= NUM_MODES; i++) {
    int c = (((int)m - 1 + dir * i) % NUM_MODES + NUM_MODES) % NUM_MODES + 1;
    if ((modeMask >> (c - 1)) & 1) return (uint8_t)c;
  }
  return m;
}
static void menuTurn(int steps) {
  uiTouch();
  int n = steps < 0 ? -steps : steps, dir = steps > 0 ? 1 : -1;
  if (!menuEdit) { for (int i = 0; i < n; i++) menuSel = (menuSel + (dir > 0 ? 1 : 4)) % 5; }
  else if (menuEdit == 1) { int b = (int)brightness + steps * 8; brightness = (uint8_t)constrain(b, 5, 255); applyBacklight(); }
  else if (menuEdit == 2) { for (int i = 0; i < n; i++) sendMedia(dir > 0 ? 0xE9 : 0xEA); }
  else if (menuEdit == 3) { for (int i = 0; i < n; i++) menuMode = nextEnabledMode(menuMode, dir); }
  else if (menuEdit == 4) { menuLayer = (uint8_t)((((int)menuLayer + steps) % LAYERS + LAYERS) % LAYERS); }
  needRedraw = true;
}
static void menuClick() {
  uiTouch();
  if (!menuEdit) {
    if (menuSel == 4) { menuCloseNow(); return; }
    menuEdit = menuSel + 1;
    if (menuEdit == 3) menuMode = mode;
    if (menuEdit == 4) menuLayer = curLayer;
  } else {
    if (menuEdit == 3 && menuMode != mode) setMode(menuMode);
    if (menuEdit == 4 && menuLayer != curLayer) setLayer(menuLayer);
    menuEdit = 0;
  }
  needRedraw = true;
}

// ================================================================ input handling
// ---- key gestures. A key with only a tap action acts the instant it is pressed (as before). A key that also has a HOLD and / or a
// DOUBLE-TAP action decides on release: tap = released before G_HOLD_MS (and, with a double-tap action, not followed by a second press
// within G_DBL_MS); hold = still down after G_HOLD_MS; double = second press within G_DBL_MS. Pure state machine, driven with explicit
// timestamps so it can be tested with synthetic timing (see cmdGestureTest).
enum : uint8_t { G_TAP = 1, G_HOLD = 2, G_DOUBLE = 4 };
static const uint32_t G_HOLD_MS = 450, G_DBL_MS = 260;
struct GestureFsm {
  bool down = false, holdFired = false, consumed = false, pending = false;
  uint32_t downAt = 0, upAt = 0;
  uint8_t step(bool isDown, uint32_t now, bool hasHold, bool hasDbl) {
    uint8_t m = 0;
    if (pending && !down && (uint32_t)(now - upAt) > G_DBL_MS) { pending = false; m |= G_TAP; }          // the double-tap window closed
    if (down && !holdFired && !consumed && hasHold && (uint32_t)(now - downAt) >= G_HOLD_MS) { holdFired = true; m |= G_HOLD; }
    if (isDown && !down) {                                      // press
      down = true; downAt = now; holdFired = false; consumed = false;
      if (!hasHold && !hasDbl && !pending) { consumed = true; m |= G_TAP; }                             // plain key: act at once
      else if (pending) { pending = false; consumed = true; m |= hasDbl ? G_DOUBLE : G_TAP; }
    } else if (!isDown && down) {                               // release
      down = false; upAt = now;
      if (!consumed && !holdFired) { if (hasDbl) pending = true; else m |= G_TAP; }
      consumed = false; holdFired = false;
    }
    return m;
  }
  uint32_t deadline(bool hasHold) const {                       // next moment step() has something to say without a new edge (0 = none)
    if (down && !holdFired && !consumed && hasHold) return downAt + G_HOLD_MS;
    if (pending && !down) return upAt + G_DBL_MS + 1;
    return 0;
  }
};
static GestureFsm gFsm[5];
static void gestureKey(uint8_t lay, uint8_t i, char g, char* out) {          // NVS key of a hold ('h') / double-tap ('d') action: "hs3", "dL1s3"
  char b[8]; slotKey(lay, i, b); out[0] = g; strcpy(out + 1, b);
}
static void loadGestureFlags() {
  for (uint8_t l = 0; l < LAYERS; l++) {
    gHold[l] = gDbl[l] = 0;
    for (uint8_t i = 0; i < 5; i++) {
      char k[10]; gestureKey(l, i, 'h', k); if (prefs.isKey(k)) gHold[l] |= (1 << i);
      gestureKey(l, i, 'd', k); if (prefs.isKey(k)) gDbl[l] |= (1 << i);
    }
  }
}
static void runGesture(uint8_t i, char g) {
  char k[10]; gestureKey(curLayer, i, g, k);
  String js = prefs.getString(k, "");
  if (!js.length()) return;
  runSpecJson(js, nullptr, (curLayer * 9 + i) * 3 + (g == 'h' ? 1 : 2));
}
// ---- dial acceleration: the faster the dial turns, the more often the action (volume ...) repeats
static uint8_t accelMult(uint32_t dtPerDetent, uint8_t level) {
  if (level == 0) return 1;
  if (dtPerDetent < 35) return level == 1 ? 3 : 6;
  if (dtPerDetent < 80) return level == 1 ? 2 : 4;
  if (level == 2 && dtPerDetent < 140) return 2;
  return 1;
}
static void onKey(int i) {                              // the key's TAP action
  flashAt = millis(); needRedraw = true;                  // key-press ring (sprite screens)
  activity();
  if (menuOpen) uiTouch();
  if (mode == M_POMO && i < 2) { if (i == 0) pomoToggle(); else pomoReset(); return; }   // K1/K2 = timer controls in focus mode
  if (!menuOpen && modeKey(i)) return;                      // stopwatch / breathing / dice / reaction / snake / habits claim some keys
  runSlot(i);
}
static void onKeyGesture(int i, char g) {               // 'h' hold / 'd' double-tap
  flashAt = millis(); needRedraw = true; activity();
  if (menuOpen) { uiTouch(); return; }
  runGesture(i, g);
}
static void onEncPressTurn(int steps) {                       // dial pressed + turned: its own pair of actions (slots 7 / 8), when configured
  int n = steps < 0 ? -steps : steps;
  for (int i = 0; i < n; i++) runSlot(steps > 0 ? 7 : 8);
}
static void onEncSteps(int steps) {
  if (menuOpen) { menuTurn(steps); return; }
  if (mode == M_SNAKE && snRun) { snakeTurn(steps); return; }
  if (mode == M_POMO && pomoState == PS_IDLE) {
    pomoMinutes = (uint8_t)constrain((int)pomoMinutes + steps, 1, 90);
    pomoRemain = pomoMinutes * 60000UL; needRedraw = true; return;
  }
  uint32_t now = millis();
  uint32_t dt = turnDtOverride >= 0 ? (uint32_t)turnDtOverride : (lastTurnAt ? now - lastTurnAt : 1000);
  turnDtOverride = -1; lastTurnAt = now;
  int n = steps < 0 ? -steps : steps;
  n *= accelMult(dt / (uint32_t)(n ? n : 1), dialAccel);
  if (n > 24) n = 24;
  lastTurnRuns = (uint8_t)n;
  for (int i = 0; i < n; i++) runSlot(steps > 0 ? 5 : 6);
}
static void encTurned(int steps) {                       // every detent, whatever screen / menu state: counter + event for the Dev tab
  activity();
  encPos += steps;
  evtEnc(steps > 0 ? 1 : -1, encPos);
}
static void onEncClick() {
  activity();
  if (menuOpen) { menuClick(); return; }
  menuOpen = true; menuSel = 0; menuEdit = 0; menuMode = mode; savedBright = brightness; uiTouch(); needRedraw = true;
}
static void onEncLong() {
  activity();
  if (menuOpen) { menuCloseNow(); return; }
  setMode(nextEnabledMode(mode, 1));
}
static void IRAM_ATTR encISR() {
  uint32_t now = micros();
  if (now - encLastUs < 400) return;                    // contact-bounce guard
  encLastUs = now;
  encAccum += (gpio_get_level((gpio_num_t)PIN_ENC_A) != gpio_get_level((gpio_num_t)PIN_ENC_B)) ? 1 : -1;   // IRAM-safe reads
}
static void inputsService() {
  uint32_t now = millis();
  static bool gInit = false;
  if (!gInit) {                                                 // a key already held at power-up must not count as a press
    gInit = true;
    for (int i = 0; i < 5; i++) if (keyBtn[i].read() == LOW) { gFsm[i].down = true; gFsm[i].consumed = true; gFsm[i].downAt = now; }
  }
  for (int i = 0; i < 5; i++) {
    keyBtn[i].update();
    if (keyBtn[i].fell()) { evtKey(i + 1, 1); flashAt = now; needRedraw = true; activity(); }
    if (keyBtn[i].rose()) evtKey(i + 1, 0);
    bool hh = (gHold[curLayer] >> i) & 1, dd = (gDbl[curLayer] >> i) & 1;
    uint8_t m = gFsm[i].step(keyBtn[i].read() == LOW, now, hh, dd);          // INPUT_PULLUP: pressed = LOW
    if (m & G_TAP) onKey(i);
    if (m & G_HOLD) onKeyGesture(i, 'h');
    if (m & G_DOUBLE) onKeyGesture(i, 'd');
  }
  encBtn.update();
  if (encBtn.fell()) { encDownAt = millis(); encHeld = true; encLongDone = false; evtEncSw(1); }
  if (encBtn.rose()) evtEncSw(0);
  if (encHeld && !encLongDone && millis() - encDownAt >= ENC_HOLD_MS) { encLongDone = true; onEncLong(); }
  if (encBtn.rose()) { if (encHeld && !encLongDone) onEncClick(); encHeld = false; }
  int32_t d;
  portENTER_CRITICAL(&encMux); d = encAccum; encAccum = 0; portEXIT_CRITICAL(&encMux);
  encRem += d;
  int steps = encRem / ENC_EDGES_PER_DETENT;
  encRem -= steps * ENC_EDGES_PER_DETENT;
  if (ENC_INVERT) steps = -steps;
  if (steps) {
    encTurned(steps);
    char pk[8]; slotKey(curLayer, steps > 0 ? 7 : 8, pk);
    if (encHeld && !menuOpen && prefs.isKey(pk)) { encLongDone = true; onEncPressTurn(steps); }    // turning while pressed: no click / long-press afterwards
    else onEncSteps(steps);
  }
}

// ================================================================ Wi-Fi / NTP (compiled in only with DC_ENABLE_WIFI=1; needs credentials from {"cmd":"wifi"})
#if DC_ENABLE_WIFI
static void onNtp(struct timeval*) { ntpDone = true; }
static void netService() {
  if (wifiSsid.isEmpty()) return;
  uint32_t now = millis();
  if (!wifiBusy) {
    if ((int32_t)(now - wifiNextAt) < 0) return;
    ntpDone = false; ntpStarted = false;
    WiFi.mode(WIFI_STA); WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
    wifiBusy = true; wifiStart = now; return;
  }
  if (WiFi.status() == WL_CONNECTED && !ntpStarted) {
    sntp_set_time_sync_notification_cb(onNtp);
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    ntpStarted = true;
  }
  if (ntpDone) {
    timeSynced = true; needRedraw = true;
    if (otaOn) ntpDone = false;                                       // OTA needs the connection: stay online
    else { wifiBusy = false; WiFi.disconnect(true); WiFi.mode(WIFI_OFF); wifiNextAt = now + 6UL * 3600000UL; }
  }
  else if (now - wifiStart > 20000 && !(otaOn && WiFi.status() == WL_CONNECTED)) { wifiBusy = false; WiFi.disconnect(true); WiFi.mode(WIFI_OFF); wifiNextAt = now + (otaOn ? 30000UL : 600000UL); }
}
static String wifiIp() { return (wifiBusy && WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : String(""); }
#else
static void onNtp(struct timeval*) {}
static void netService() {}
static String wifiIp() { return String(""); }
#endif

// ================================================================ serial JSON protocol
static void uploadAbort() {
  if (!uploading) return;
  upFile.close(); LittleFS.remove("/anim.tmp"); uploading = false; gifRestart = true; needRedraw = true;
}
// ---- host keyboard layout: Keyboard.press('z') sends the key that is labelled 'z' on the chosen layout
// USBHIDKeyboard::begin(const uint8_t*) and the KeyboardLayout_xx_xx tables only exist on arduino-esp32
// core 3.0.0+; on older cores (2.0.x, still a common Boards Manager install) those symbols do not exist
// at all, so this whole feature is compiled out there and the pad simply stays on US ASCII mapping.
#if (ESP_ARDUINO_VERSION_MAJOR >= 3) && DC_HAS_HID
#define DC_HAS_KB_LAYOUT 1
// KeyboardLayout_fr_CH and KeyboardLayout_ja_JP were only added to arduino-esp32 in 3.3.8 and 3.3.9
// respectively - every other 3.x release (3.0.0 through 3.3.7/3.3.8, i.e. most real-world installs)
// does not declare them at all, which is a hard compile error, not a runtime fallback. Left out here
// on purpose; everything else in this table has been available since 3.1.0 (the first 3.x release
// with any keyboard-layout support at all).
struct LayoutDef { const char* name; const uint8_t* map; };
static const LayoutDef LAYOUTS[] = {
  {"en_US", KeyboardLayout_en_US}, {"de_DE", KeyboardLayout_de_DE}, {"fr_FR", KeyboardLayout_fr_FR},
  {"es_ES", KeyboardLayout_es_ES}, {"it_IT", KeyboardLayout_it_IT},
  {"pt_PT", KeyboardLayout_pt_PT}, {"pt_BR", KeyboardLayout_pt_BR}, {"sv_SE", KeyboardLayout_sv_SE},
  {"da_DK", KeyboardLayout_da_DK}, {"hu_HU", KeyboardLayout_hu_HU}};
static const uint8_t* layoutByName(const char* n) {
  for (const LayoutDef& l : LAYOUTS) if (!strcmp(l.name, n)) return l.map;
  return nullptr;
}
#else
#define DC_HAS_KB_LAYOUT 0
static const uint8_t* layoutByName(const char* n) { return !strcmp(n, "en_US") ? (const uint8_t*)"" : nullptr; }
#endif
static String kbLayout = "en_US";

static void cmdGifList() {
  JsonDocument d; d["ok"] = true; d["evt"] = "gif_list";
  JsonArray a = d["slots"].to<JsonArray>();
  for (uint8_t i = 0; i < GIF_SLOTS; i++) {
    if (!gifSlotExists(i)) continue;
    char gp[16]; gifPath(i, gp);
    fs::File f = LittleFS.open(gp, "r");
    JsonObject o = a.add<JsonObject>(); o["s"] = i; o["size"] = f ? (uint32_t)f.size() : 0u;
    if (f) f.close();
  }
  d["cur"] = gifSlot; d["rot"] = gifRot; d["max"] = GIF_SLOTS;
  d["fs_free"] = okFs ? fsFreeBytes() : 0;
  sendDoc(d);
}
static void cmdHello() {
  JsonDocument d;
  d["ok"] = true; d["evt"] = "hello"; d["dev"] = "desk-companion"; d["fw"] = FW_VERSION;
  d["mode"] = mode; d["bright"] = brightness; d["os"] = osMac ? "mac" : "win";
  d["gif"] = okFs && gifSlotCount() > 0; d["gifs"] = okFs ? gifSlotCount() : 0; d["gif_rot"] = gifRot;
  d["fs_free"] = okFs ? fsFreeBytes() : 0; d["fs_total"] = okFs ? (uint32_t)LittleFS.totalBytes() : 0;
  d["synced"] = timeSynced; d["layout"] = kbLayout;
  d["hid"] = (bool)DC_HAS_HID; d["disp"] = okDisp; d["fs"] = okFs; d["fs_state"] = FS_STATE_NAME[fsState]; d["safe"] = safeMode; d["led_pin"] = PIN_RGB;
  d["layer"] = curLayer; d["layers"] = LAYERS; d["modes"] = NUM_MODES;
  JsonArray cp = d["caps"].to<JsonArray>();
  cp.add("layers"); cp.add("mouse"); cp.add("host"); cp.add("info"); cp.add("gifslots"); cp.add("factory");
  cp.add("hostx"); cp.add("gestures"); cp.add("dialaccel"); cp.add("clockstyle"); cp.add("saver"); cp.add("nightdim");   // firmware 1.3
  cp.add("screens"); cp.add("pressturn"); cp.add("toggle"); cp.add("wheelmods"); cp.add("ledfx"); cp.add("reminders"); cp.add("habits");  // firmware 1.4
  if (DC_ENABLE_WIFI) cp.add("wifi");
  if (DC_HAS_OTA) cp.add("ota");
  sendDoc(d);
}
static String coreVersionStr() {
  return String(ESP_ARDUINO_VERSION_MAJOR) + "." + String(ESP_ARDUINO_VERSION_MINOR) + "." + String(ESP_ARDUINO_VERSION_PATCH);
}
static void cmdInfo() {
  JsonDocument d;
  d["ok"] = true; d["evt"] = "info"; d["fw"] = FW_VERSION; d["build"] = __DATE__ " " __TIME__;
  d["chip"] = ESP.getChipModel(); d["rev"] = ESP.getChipRevision(); d["cores"] = ESP.getChipCores(); d["cpu_mhz"] = ESP.getCpuFreqMHz();
  d["flash"] = ESP.getFlashChipSize();
  d["heap"] = ESP.getFreeHeap(); d["heap_min"] = ESP.getMinFreeHeap(); d["heap_blk"] = ESP.getMaxAllocHeap(); d["psram"] = ESP.getPsramSize();
  float t = chipTemp(); d["temp"] = isnan(t) ? 0.0f : t;
  d["up_ms"] = millis(); d["reset"] = resetReasonStr(resetReason); d["crashes"] = crashCount; d["safe"] = safeMode;
  d["core"] = coreVersionStr();
#ifdef ARDUINO_USB_MODE
  d["usb_mode"] = ARDUINO_USB_MODE;                     // 0 = USB-OTG (TinyUSB), 1 = hardware CDC / JTAG
#endif
#ifdef ARDUINO_USB_CDC_ON_BOOT
  d["cdc_boot"] = ARDUINO_USB_CDC_ON_BOOT;
#endif
  d["hid"] = (bool)DC_HAS_HID; d["tft"] = (bool)DC_HAS_TFT; d["sim"] = DC_IS_SIM;
  d["ok_prefs"] = okPrefs; d["ok_fs"] = okFs; d["fs_state"] = FS_STATE_NAME[fsState]; d["ok_sprite"] = okSprite; d["ok_disp"] = okDisp;
  d["fs_free"] = okFs ? fsFreeBytes() : 0; d["fs_total"] = okFs ? (uint32_t)LittleFS.totalBytes() : 0;
  d["rx_ms_ago"] = lastRxMs ? (uint32_t)(millis() - lastRxMs) : 0; d["events"] = eventsOn; d["gpio_touched"] = gpioTouched;
  d["led_pin"] = PIN_RGB; d["led_mode"] = ledMode; d["mode"] = mode; d["bright"] = brightness;
  d["safe_why"] = !safeMode ? "" : DC_FORCE_SAFE ? "forced" : "crash_loop"; d["nodisp"] = dispOff; d["disp_why"] = dispWhy;
  d["bl"] = lastBl; d["saver_on"] = saverOn; d["ota"] = otaOn; d["layer"] = curLayer;
  d["ip"] = wifiIp(); d["wifi_build"] = (bool)DC_ENABLE_WIFI;
  d["boot"] = bootLog;
  sendDoc(d);
}
static void cmdLed(JsonDocument& doc) {
  const char* m = doc["mode"] | "";
  if (!doc["hex"].isNull()) {                                   // "#RRGGBB"
    const char* h = doc["hex"] | "";
    if (*h == '#') h++;
    unsigned long v = strtoul(h, nullptr, 16);
    ledUserR = (v >> 16) & 255; ledUserG = (v >> 8) & 255; ledUserB = v & 255; ledMode = LM_SOLID;
  }
  if (!doc["r"].isNull() || !doc["g"].isNull() || !doc["b"].isNull()) {
    ledUserR = (uint8_t)constrain(doc["r"] | 0, 0, 255); ledUserG = (uint8_t)constrain(doc["g"] | 0, 0, 255); ledUserB = (uint8_t)constrain(doc["b"] | 0, 0, 255);
    ledMode = LM_SOLID;
  }
  if (!strcmp(m, "auto")) ledMode = LM_AUTO;
  else if (!strcmp(m, "off")) ledMode = LM_OFF;
  else if (!strcmp(m, "solid")) ledMode = LM_SOLID;
  else if (!strcmp(m, "blink")) ledMode = LM_BLINK;
  else if (!strcmp(m, "rainbow")) ledMode = LM_RAINBOW;
  else if (!strcmp(m, "breathe")) ledMode = LM_BREATHE;
  else if (!strcmp(m, "fire")) ledMode = LM_FIRE;
  else if (*m) { nack("led_mode"); return; }
  if (!doc["alert"].isNull()) {                                 // {"alert":"ff0000","times":3}: a few quick flashes in that colour, then back to what the LED was doing
    unsigned long v = strtoul(doc["alert"] | "", nullptr, 16);
    int times = constrain(doc["times"] | 3, 1, 10);
    alertR = (v >> 16) & 255; alertG = (v >> 8) & 255; alertB = v & 255; alertStart = millis(); alertEnd = alertStart + times * 240UL;
  }
  if (doc["save"] | false) prefs.putUChar("ledm", ledMode == LM_OFF ? 1 : 0);   // only auto/off survive a reboot
  ledNextAt = 0;
  JsonDocument d; d["ok"] = true; d["evt"] = "led"; d["mode"] = ledMode; d["pin"] = PIN_RGB;
  d["r"] = ledUserR; d["g"] = ledUserG; d["b"] = ledUserB;
  sendDoc(d);
}

// ---- GPIO tester (wiring checks from the app's Dev tab)
static const char* pinUse(int p) {
  switch (p) {
    case 7: return "TFT BLK"; case 8: return "TFT CS"; case 9: return "TFT DC"; case 10: return "TFT RES";
    case 11: return "TFT SDA"; case 12: return "TFT SCL"; case 13: return "ENC A"; case 14: return "ENC B"; case 15: return "ENC SW";
    case 1: return "K1"; case 2: return "K2"; case 4: return "K3"; case 5: return "K4"; case 6: return "K5";
    case 21: return "RGB LED"; case 0: return "BOOT button"; case 19: case 20: return "USB"; default: return "";
  }
}
static bool gpioReadable(int p) { return (p >= 0 && p <= 18) || p == 21 || (p >= 33 && p <= 48); }
static bool gpioDrivable(int p) { return (p >= 1 && p <= 18) || (p >= 33 && p <= 42); }
static void cmdGpio(JsonDocument& doc) {
  const char* op = doc["op"] | "read";
  if (!strcmp(op, "scan")) {                                    // every readable pin, current level, no mode change
    JsonDocument d; d["ok"] = true; d["evt"] = "gpio_scan";
    JsonArray a = d["pins"].to<JsonArray>();
    for (int p = 0; p <= 48; p++) if (gpioReadable(p)) { JsonArray e = a.add<JsonArray>(); e.add(p); e.add(digitalRead(p)); }
    sendDoc(d); return;
  }
  int p = doc["pin"] | -1;
  if (!gpioReadable(p)) { nack("pin"); return; }
  const char* use = pinUse(p);
  if (!strcmp(op, "read")) { /* no pin mode change */ }
  else if (!gpioDrivable(p)) { nack("pin_protected"); return; }
  else if (!strcmp(op, "high")) { pinMode(p, OUTPUT); digitalWrite(p, HIGH); gpioTouched = true; }
  else if (!strcmp(op, "low")) { pinMode(p, OUTPUT); digitalWrite(p, LOW); gpioTouched = true; }
  else if (!strcmp(op, "pullup")) { pinMode(p, INPUT_PULLUP); gpioTouched = true; }
  else if (!strcmp(op, "input")) { pinMode(p, INPUT); gpioTouched = true; }
  else { nack("op"); return; }
  JsonDocument d; d["ok"] = true; d["evt"] = "gpio"; d["pin"] = p; d["val"] = digitalRead(p); d["use"] = use;
  if (gpioTouched && *use && strcmp(op, "read")) d["warn"] = "pin is used by the pad - reboot to restore it";
  sendDoc(d);
}
static void cmdInputs() {
  JsonDocument d; d["ok"] = true; d["evt"] = "inputs";
  JsonArray k = d["keys"].to<JsonArray>();
  for (int i = 0; i < 5; i++) k.add(digitalRead(PIN_KEY[i]) == LOW ? 1 : 0);        // 1 = pressed
  d["enc_sw"] = digitalRead(PIN_ENC_SW) == LOW ? 1 : 0; d["enc_a"] = digitalRead(PIN_ENC_A); d["enc_b"] = digitalRead(PIN_ENC_B);
  d["enc_pos"] = encPos;
  sendDoc(d);
}

// ---- display test patterns + screenshot of the frame buffer
static void cmdDisplay(JsonDocument& doc) {
  if (!okSprite) { nack("no_display"); return; }
  const char* t = doc["test"] | "fill";
  if (!strcmp(t, "off")) { dispHoldUntil = 0; if (mode == M_GIF) gifRestart = true; needRedraw = true; ack("display"); return; }
  uint32_t hold = (uint32_t)constrain(doc["hold"] | 8000, 500, 60000);
  spr.setTextDatum(MC_DATUM);
  if (!strcmp(t, "fill")) {
    spr.fillSprite(rgb((uint8_t)constrain(doc["r"] | 0, 0, 255), (uint8_t)constrain(doc["g"] | 0, 0, 255), (uint8_t)constrain(doc["b"] | 0, 0, 255)));
  } else if (!strcmp(t, "bars")) {
    static const uint16_t col[8] = {0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000};
    for (int i = 0; i < 8; i++) spr.fillRect(i * 30, 0, 30, 240, col[i]);
  } else if (!strcmp(t, "grid")) {
    spr.fillSprite(TFT_BLACK);
    for (int v = 0; v <= 240; v += 20) { spr.drawFastHLine(0, v, 240, v == 120 ? C_ACC : C_DIM2); spr.drawFastVLine(v, 0, 240, v == 120 ? C_ACC : C_DIM2); }
    spr.drawCircle(120, 120, 119, TFT_WHITE); spr.drawCircle(120, 120, 60, C_ACC2);
    spr.fillRect(0, 0, 14, 14, TFT_RED); spr.fillRect(226, 0, 14, 14, TFT_GREEN); spr.fillRect(0, 226, 14, 14, TFT_BLUE); spr.fillRect(226, 226, 14, 14, TFT_WHITE);
  } else if (!strcmp(t, "text")) {
    spr.fillSprite(TFT_BLACK);
    spr.setTextColor(TFT_WHITE); spr.drawString("DeskCompanion", 120, 90, 4);
    spr.setTextColor(C_OK); spr.drawString("display OK", 120, 125, 4);
    spr.setTextColor(C_GRAY); char b[40]; snprintf(b, sizeof b, "fw %s  240x240", FW_VERSION); spr.drawString(b, 120, 160, 2);
  } else { nack("pattern"); return; }
  pushScreen();
  dispHoldUntil = millis() + hold;
  ack("display");
}
static void b64enc(const uint8_t* in, size_t n, char* out) {
  static const char* A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  size_t o = 0;
  for (size_t i = 0; i < n; i += 3) {
    uint32_t v = (uint32_t)in[i] << 16;
    if (i + 1 < n) v |= (uint32_t)in[i + 1] << 8;
    if (i + 2 < n) v |= in[i + 2];
    out[o++] = A[(v >> 18) & 63]; out[o++] = A[(v >> 12) & 63];
    out[o++] = (i + 1 < n) ? A[(v >> 6) & 63] : '=';
    out[o++] = (i + 2 < n) ? A[v & 63] : '=';
  }
  out[o] = 0;
}
static void cmdSnapshot() {                              // what the sprite (= what the screen shows, except GIF mode) contains
  if (!okSprite) { nack("no_display"); return; }
  const uint8_t* px = (const uint8_t*)spr.getPointer();
  if (!px) { nack("no_display"); return; }
  const size_t total = 240 * 240 * 2, CH = 360;
  { JsonDocument d; d["ok"] = true; d["evt"] = "snap_begin"; d["w"] = 240; d["h"] = 240; d["fmt"] = "rgb565be"; d["bytes"] = (uint32_t)total; d["chunk"] = (uint32_t)CH; sendDoc(d); }
  static char line[560];
  for (size_t off = 0; off < total; off += CH) {
    size_t n = total - off < CH ? total - off : CH;
    int h = snprintf(line, sizeof line, "{\"evt\":\"snap\",\"o\":%u,\"d\":\"", (unsigned)off);
    b64enc(px + off, n, line + h);
    size_t l = strlen(line);
    line[l++] = '"'; line[l++] = '}'; line[l++] = '\n';
    txRaw((const uint8_t*)line, l);
  }
  txRaw((const uint8_t*)"{\"evt\":\"snap_end\"}\n", 19);
}

static void cmdRun(JsonDocument& doc) {                  // run an action immediately (HID test) without saving it to a key
  if (!DC_HAS_HID) { nack("no_hid"); return; }
  JsonDocument spec;
  spec["type"] = doc["type"]; spec["val"] = doc["val"];
  std::vector<Step> steps;
  if (!parseSpec(spec.as<JsonVariantConst>(), steps)) { nack("spec"); return; }
  size_t n = steps.size();
  macroStart(steps);
  JsonDocument d; d["ok"] = true; d["evt"] = "run"; d["steps"] = (uint32_t)n; sendDoc(d);
}
static void cmdSelftest() {
  JsonDocument d; d["ok"] = true; d["evt"] = "selftest";
  bool nvs = false;
  if (okPrefs) { prefs.putUInt("selftest", 0xC0FFEEu); nvs = prefs.getUInt("selftest", 0) == 0xC0FFEEu; prefs.remove("selftest"); }
  d["nvs"] = nvs;
  bool fs = false;
  if (okFs) {
    fs::File f = LittleFS.open("/selftest.tmp", "w");
    if (f) {
      uint8_t buf[256]; for (int i = 0; i < 256; i++) buf[i] = (uint8_t)(i * 7 + 3);
      bool w = f.write(buf, sizeof buf) == sizeof buf; f.close();
      fs::File r = LittleFS.open("/selftest.tmp", "r");
      if (r) { uint8_t rb[256]; bool rd = r.read(rb, sizeof rb) == sizeof rb && !memcmp(buf, rb, sizeof buf); r.close(); fs = w && rd; }
    }
    LittleFS.remove("/selftest.tmp");
  }
  d["fs"] = fs;
  d["heap_ok"] = ESP.getFreeHeap() > 40000; d["heap"] = ESP.getFreeHeap();
  uint8_t pm = ledMode; ledMode = LM_SOLID;                      // LED: red, green, blue
  const uint8_t cols[3][3] = {{60, 0, 0}, {0, 60, 0}, {0, 0, 60}};
  for (int i = 0; i < 3; i++) { ledRaw(cols[i][0], cols[i][1], cols[i][2]); delay(250); }
  ledMode = pm; ledNextAt = 0;
  d["led"] = "cycled";
  if (okSprite) {
    const uint16_t pc[3] = {TFT_RED, TFT_GREEN, TFT_BLUE};
    for (int i = 0; i < 3; i++) { spr.fillSprite(pc[i]); pushScreen(); delay(250); }
    needRedraw = true; if (mode == M_GIF) gifRestart = true;
    d["display"] = "drawn";
  } else d["display"] = "unavailable";
  d["hid"] = (bool)DC_HAS_HID;
  JsonArray k = d["keys"].to<JsonArray>();
  for (int i = 0; i < 5; i++) k.add(digitalRead(PIN_KEY[i]) == LOW ? 1 : 0);
  sendDoc(d);
}
static void cmdGetKeys(JsonDocument& doc) {                // lets the app verify (or back up) what is really stored on the pad
  int lay = doc["layer"] | 0;
  if (lay < 0 || lay >= LAYERS) { nack("layer"); return; }
  JsonDocument d; d["ok"] = true; d["evt"] = "keys"; d["layer"] = lay; d["cur"] = curLayer; d["layers"] = LAYERS;
  if (!doc["slot"].isNull()) {                              // one slot with its full spec (stored or default)
    int sl = doc["slot"].as<int>();
    if (sl < 1 || sl > 9) { nack("key"); return; }
    const char* gest = doc["gesture"] | "tap";
    if (strcmp(gest, "tap")) {                                // a hold / double-tap action: stored or absent (there is no default)
      char gch = !strcmp(gest, "hold") ? 'h' : !strcmp(gest, "double") ? 'd' : 0;
      if (!gch || sl > 5) { nack("gesture"); return; }
      char gk[10]; gestureKey((uint8_t)lay, (uint8_t)(sl - 1), gch, gk);
      String gj = prefs.getString(gk, "");
      d["s"] = sl; d["g"] = gest; d["set"] = gj.length() > 0;
      if (gj.length()) { JsonDocument sp; if (!deserializeJson(sp, gj)) d["spec"] = sp; }
      sendDoc(d); return;
    }
    char k[8]; slotKey((uint8_t)lay, (uint8_t)(sl - 1), k);
    String js = prefs.getString(k, "");
    d["s"] = sl; d["def"] = js.length() == 0;
    if (sl > 7) {                                             // dial press+turn slots have no default action
      if (js.length()) { JsonDocument sp; if (!deserializeJson(sp, js)) d["spec"] = sp; }
      sendDoc(d); return;
    }
    JsonDocument sp;
    if (deserializeJson(sp, js.length() ? js : String(DEFAULT_SLOT[lay][sl - 1]))) deserializeJson(sp, DEFAULT_SLOT[lay][sl - 1]);
    d["spec"] = sp;
    sendDoc(d); return;
  }
  JsonArray a = d["slots"].to<JsonArray>();
  for (uint8_t i = 0; i < 7; i++) {
    char k[8]; slotKey((uint8_t)lay, i, k);
    String js = prefs.getString(k, "");
    JsonObject o = a.add<JsonObject>();
    o["s"] = i + 1; o["def"] = js.length() == 0; o["len"] = (uint32_t)js.length();
    if (i < 5) { o["h"] = (bool)((gHold[lay] >> i) & 1); o["d"] = (bool)((gDbl[lay] >> i) & 1); }
    o["crc"] = js.length() ? crc32u(0, (const uint8_t*)js.c_str(), js.length()) : 0u;
  }
  { JsonArray pt = d["pt"].to<JsonArray>(); for (uint8_t i = 7; i < 9; i++) { char k[8]; slotKey((uint8_t)lay, i, k); pt.add(prefs.isKey(k)); } }   // dial press+turn right / left set?
  sendDoc(d);
}
static void cmdLayer(JsonDocument& doc) {
  int n = curLayer;
  if (doc["val"].is<const char*>()) {
    String v = doc["val"].as<const char*>();
    if (v == "next") n = (curLayer + 1) % LAYERS; else if (v == "prev") n = (curLayer + LAYERS - 1) % LAYERS; else { nack("layer"); return; }
  } else if (!doc["val"].isNull()) n = doc["val"].as<int>();
  if (n < 0 || n >= LAYERS) { nack("layer"); return; }
  setLayer((uint8_t)n);
  JsonDocument d; d["ok"] = true; d["evt"] = "layer"; d["n"] = curLayer; d["layers"] = LAYERS; sendDoc(d);
}
// {"cmd":"info_cards","cards":[{"k":"m|w|e|c","label":"..","t":"..","a":"..","b":".."}],"badges":[{"name":"mail","n":3}],"rot":6}
static void cmdInfoCards(JsonDocument& doc) {
  JsonArrayConst ca = doc["cards"].as<JsonArrayConst>();
  uint8_t n = 0;
  if (!ca.isNull()) for (JsonObjectConst o : ca) {
    if (n >= 4) break;
    const char* k = o["k"] | "c";
    cards[n].kind = *k ? *k : 'c';
    cards[n].label = o["label"] | ""; cards[n].t = o["t"] | ""; cards[n].a = o["a"] | ""; cards[n].b = o["b"] | "";
    n++;
  }
  if (n != nCards || cardIdx >= (n ? n : 1)) cardIdx = 0;
  nCards = n;
  uint8_t nb = 0;
  JsonArrayConst ba = doc["badges"].as<JsonArrayConst>();
  if (!ba.isNull()) for (JsonObjectConst o : ba) {
    if (nb >= 4) break;
    badges[nb].name = o["name"] | ""; badges[nb].n = (uint16_t)constrain(o["n"].as<int>(), 0, 999);
    nb++;
  }
  nBadges = nb;
  if (!doc["rot"].isNull()) cardRotSec = (uint16_t)constrain(doc["rot"].as<int>(), 0, 120);
  infoRxAt = millis(); cardAt = infoRxAt;
  if (mode == M_INFO) needRedraw = true;
  JsonDocument d; d["ok"] = true; d["evt"] = "info_cards"; d["cards"] = nCards; d["badges"] = nBadges; sendDoc(d);
}
// recovery helpers for the app's "safe mode" / "reset" buttons ("confirm":true is required, "reboot":false skips the restart)
static void cmdFactory(JsonDocument& doc) {
  if (!(doc["confirm"] | false)) { nack("confirm"); return; }
  const char* what = doc["what"] | "settings";
  bool reboot = doc["reboot"] | true;
  if (!strcmp(what, "keys")) {
    for (uint8_t l = 0; l < LAYERS; l++) for (uint8_t i = 0; i < 9; i++) { char k[8]; slotKey(l, i, k); prefs.remove(k); if (i < 5) { char g[10]; gestureKey(l, i, 'h', g); prefs.remove(g); gestureKey(l, i, 'd', g); prefs.remove(g); } }
    loadGestureFlags();
    ack("factory");
  } else if (!strcmp(what, "settings")) {
    prefs.clear();
    ack("factory");
    if (reboot) { delay(150); esp_restart(); }
  } else if (!strcmp(what, "gifs")) {
    if (!okFs) { nack("fs_busy"); return; }
    uploadAbort(); gifClose();
    for (uint8_t i = 0; i < GIF_SLOTS; i++) { char gp[16]; gifPath(i, gp); LittleFS.remove(gp); }
    gifSlot = 0; okFs = false; fsStartAsync(fsPrepTask, FS_PREPARING);
    ack("factory");
  } else nack("what");
}
static void cmdBootOpt(JsonDocument& doc) {
  if (!doc["nodisp"].isNull()) { dispOff = doc["nodisp"].as<bool>(); prefs.putBool("nodisp", dispOff); }
  JsonDocument d; d["ok"] = true; d["evt"] = "boot_opt"; d["nodisp"] = dispOff; d["safe"] = safeMode; sendDoc(d);
}
#if DC_HAS_OTA
static void otaStart() {
  if (otaStarted) return;
  ArduinoOTA.setHostname("deskcompanion");
  if (otaPw.length()) ArduinoOTA.setPassword(otaPw.c_str());
  ArduinoOTA.begin();
  otaStarted = true;
}
static void otaService() {
  if (!otaOn) return;
  if (WiFi.status() == WL_CONNECTED) { if (!otaStarted) otaStart(); ArduinoOTA.handle(); }
}
static void otaStop() { if (otaStarted) { ArduinoOTA.end(); otaStarted = false; } }
#else
static void otaStart() {}
static void otaService() {}
static void otaStop() {}
#endif
#if DC_ENABLE_WIFI
static void cmdOta(JsonDocument& doc) {                 // opt-in Wi-Fi OTA: needs Wi-Fi credentials first; password optional but recommended
  if (!DC_HAS_OTA) { nack("ota_unsupported"); return; }
  bool on = doc["val"] | false;
  if (!doc["pass"].isNull()) { otaPw = doc["pass"] | ""; prefs.putString("otapw", otaPw); }
  if (on && wifiSsid.isEmpty()) { nack("no_wifi"); return; }
  otaOn = on; prefs.putBool("ota", otaOn);
  if (!on) { otaStop(); if (wifiBusy) { WiFi.disconnect(true); WiFi.mode(WIFI_OFF); wifiBusy = false; wifiNextAt = millis() + 6UL * 3600000UL; } }
  else { otaStop(); wifiNextAt = 0; }
  JsonDocument d; d["ok"] = true; d["evt"] = "ota"; d["on"] = otaOn; d["has_pw"] = otaPw.length() > 0; sendDoc(d);
}
static void cmdWifi(JsonDocument& doc) {
  wifiSsid = doc["ssid"] | ""; wifiPass = doc["pass"] | "";
  prefs.putString("ssid", wifiSsid); prefs.putString("wpass", wifiPass);
  wifiNextAt = 0; ack("wifi");
}
#else
static void cmdOta(JsonDocument& doc) { (void)doc; nack("wifi_disabled"); }          // built without Wi-Fi (DC_ENABLE_WIFI = 0)
static void cmdWifi(JsonDocument& doc) { (void)doc; nack("wifi_disabled"); }
#endif

static void cmdInput(JsonDocument& doc) {               // virtual key presses: exercises the real action / UI code from the app
  if (!doc["k"].isNull()) {
    int k = doc["k"] | 0;
    if (k < 1 || k > 5) { nack("key"); return; }
    const char* g = doc["g"] | "tap";
    if (!strcmp(g, "hold") || !strcmp(g, "double")) { evtKey(k, 1); onKeyGesture(k - 1, g[0] == 'h' ? 'h' : 'd'); evtKey(k, 0); ack("input"); return; }
    if (strcmp(g, "tap")) { nack("gesture"); return; }
    evtKey(k, 1); onKey(k - 1); evtKey(k, 0);
  } else if (!doc["turn"].isNull()) {
    int t = doc["turn"] | 0;
    if (t == 0 || t > 20 || t < -20) { nack("turn"); return; }
    turnDtOverride = doc["dt"].isNull() ? -1 : (int32_t)constrain(doc["dt"].as<int>(), 0, 5000);
    lastTurnRuns = 0;
    char pk[8]; slotKey(curLayer, t > 0 ? 7 : 8, pk);
    bool pressed = (doc["press"] | false) && !menuOpen && prefs.isKey(pk);          // "press": the dial is held down while it turns
    encTurned(t);
    if (pressed) onEncPressTurn(t); else onEncSteps(t);
    turnDtOverride = -1;
    JsonDocument r; r["ok"] = true; r["evt"] = "input"; r["runs"] = lastTurnRuns; sendDoc(r); return;
  } else if (doc["click"] | false) onEncClick();
  else if (doc["hold"] | false) onEncLong();
  else { nack("input"); return; }
  ack("input");
}
#ifdef DC_SIM
static bool cmdDebug(const char* cmd) {                  // emulator-only: provoke a panic to test crash-loop / safe mode
  if (!strcmp(cmd, "debug_crash")) { ack("debug_crash"); delay(50); volatile int* p = nullptr; *p = 1; return true; }
  return false;
}
#else
static bool cmdDebug(const char* cmd) { (void)cmd; return false; }
#endif
// ---- {"cmd":"settings", ...}: v1.3 behaviour settings; every field optional, no field = just read. Validates everything before changing anything.
static void settingsReply() {
  JsonDocument d; d["ok"] = true; d["evt"] = "settings";
  d["dial_accel"] = dialAccel; d["clock_style"] = clockStyle; d["saver_s"] = saverSec; d["saver_style"] = saverStyle;
  d["night_on"] = nightOn; d["night_from"] = nightFrom; d["night_to"] = nightTo; d["night_level"] = nightLevel;
  d["bl"] = lastBl; d["saver_on"] = saverOn; d["mode_mask"] = modeMask;
  sendDoc(d);
}
static bool optInt(JsonDocument& doc, const char* key, int lo, int hi, int& out, bool& bad) {
  if (doc[key].isNull()) return false;
  if (!doc[key].is<int>()) { bad = true; return false; }
  int v = doc[key].as<int>();
  if (v < lo || v > hi) { bad = true; return false; }
  out = v; return true;
}
static void cmdSettings(JsonDocument& doc) {
  bool bad = false; int da = dialAccel, cs = clockStyle, ss = saverSec, st = saverStyle, nf = nightFrom, nt = nightTo, nl = nightLevel;
  bool hDa = optInt(doc, "dial_accel", 0, 2, da, bad), hCs = optInt(doc, "clock_style", 0, 3, cs, bad), hSs = optInt(doc, "saver_s", 0, 3600, ss, bad),
       hSt = optInt(doc, "saver_style", 1, 3, st, bad), hNf = optInt(doc, "night_from", 0, 23, nf, bad), hNt = optInt(doc, "night_to", 0, 23, nt, bad),
       hNl = optInt(doc, "night_level", 5, 255, nl, bad);
  int mm = modeMask; bool hMm = optInt(doc, "mode_mask", 1, 0x0FFF, mm, bad);
  bool hNo = !doc["night_on"].isNull();
  if (hNo && !doc["night_on"].is<bool>()) bad = true;
  if (bad) { nack("settings"); return; }
  if (hDa) { dialAccel = (uint8_t)da; prefs.putUChar("dacc", dialAccel); }
  if (hCs) { clockStyle = (uint8_t)cs; prefs.putUChar("cstyle", clockStyle); needRedraw = true; }
  if (hSs) { saverSec = (uint16_t)ss; prefs.putUShort("savs", saverSec); activity(); }
  if (hSt) { saverStyle = (uint8_t)st; prefs.putUChar("savst", saverStyle); needRedraw = true; }
  if (hNf) { nightFrom = (uint8_t)nf; prefs.putUChar("nd_f", nightFrom); }
  if (hNt) { nightTo = (uint8_t)nt; prefs.putUChar("nd_t", nightTo); }
  if (hNl) { nightLevel = (uint8_t)nl; prefs.putUChar("nd_l", nightLevel); }
  if (hNo) { nightOn = doc["night_on"].as<bool>(); prefs.putBool("nd_on", nightOn); }
  if (hMm) { modeMask = (uint16_t)mm; prefs.putUShort("mmask", modeMask); }
  if (hNf || hNt || hNl || hNo) applyBacklight();
  settingsReply();
}
// ---- {"cmd":"gesture_test","hold":true,"dbl":false,"seq":[[1,0],[0,120],...]}: replays [pressed, ms] events through the gesture state
// machine with the given timing and answers with what it would fire, as [[ms,"tap|hold|double"],...]. Does not run any action.
static void cmdGestureTest(JsonDocument& doc) {
  bool hh = doc["hold"] | false, dd = doc["dbl"] | false;
  JsonArrayConst seq = doc["seq"].as<JsonArrayConst>();
  if (seq.isNull() || seq.size() == 0 || seq.size() > 24) { nack("seq"); return; }
  JsonDocument out; out["ok"] = true; out["evt"] = "gesture_test"; JsonArray fired = out["fired"].to<JsonArray>();
  GestureFsm f; uint32_t last = 0;
  auto emit = [&](uint8_t m, uint32_t t) {
    if (m & G_TAP) { JsonArray a = fired.add<JsonArray>(); a.add(t); a.add("tap"); }
    if (m & G_HOLD) { JsonArray a = fired.add<JsonArray>(); a.add(t); a.add("hold"); }
    if (m & G_DOUBLE) { JsonArray a = fired.add<JsonArray>(); a.add(t); a.add("double"); }
  };
  for (JsonVariantConst ev : seq) {
    if (ev.size() != 2) { nack("seq"); return; }
    bool down = ev[0].as<int>() != 0; uint32_t t = ev[1].as<uint32_t>();
    if (t < last) { nack("seq"); return; }
    for (int guard = 0; guard < 4; guard++) {                       // idle moments before this event (the real loop calls step() every ms)
      uint32_t dl = f.deadline(hh);
      if (!dl || dl > t) break;
      emit(f.step(f.down, dl, hh, dd), dl);
    }
    emit(f.step(down, t, hh, dd), t); last = t;
  }
  for (int guard = 0; guard < 4; guard++) { uint32_t dl = f.deadline(hh); if (!dl) break; emit(f.step(f.down, dl, hh, dd), dl); }
  sendDoc(out);
}
// ---- {"cmd":"screens"}: state of the optional screens (for the app and for tests); {"habit"} / {"reminders"} have their own commands
static void cmdScreens(JsonDocument& doc) {
  (void)doc;
  JsonDocument d; d["ok"] = true; d["evt"] = "screens"; d["mask"] = modeMask;
  JsonObject sw = d["sw"].to<JsonObject>(); sw["run"] = swRun; sw["ms"] = swElapsed(); JsonArray l = sw["laps"].to<JsonArray>(); for (int i = 0; i < swLapN; i++) l.add(swLaps[i]);
  JsonObject br = d["br"].to<JsonObject>(); br["run"] = brRun; br["pattern"] = brPattern;
  JsonObject toy = d["toy"].to<JsonObject>(); toy["kind"] = toyKind; toy["result"] = toyResult; toy["rolling"] = (int32_t)(toyRollUntil - millis()) > 0;
  JsonObject rx = d["rx"].to<JsonObject>(); rx["state"] = rxState; rx["ms"] = rxMs; rx["best"] = rxBest;
  JsonObject sn = d["snake"].to<JsonObject>(); sn["run"] = snRun; sn["over"] = snOver; sn["len"] = snLen; sn["dir"] = snDir; sn["best"] = snBest;
  d["reminder_active"] = remActive;
  sendDoc(d);
}
static void habitsReply() {
  habitRoll();
  JsonDocument d; d["ok"] = true; d["evt"] = "habits"; d["synced"] = timeSynced; d["day"] = habDay;
  JsonArray n = d["names"].to<JsonArray>(), t = d["today"].to<JsonArray>(), st = d["streak"].to<JsonArray>();
  for (int i = 0; i < 5; i++) { n.add(habName[i]); t.add(habHist[i] & 1); st.add(habStreak(habHist[i])); }
  sendDoc(d);
}
static void cmdHabits(JsonDocument& doc) {
  if (!doc["names"].isNull()) {
    JsonArrayConst a = doc["names"].as<JsonArrayConst>();
    if (a.isNull() || a.size() != 5) { nack("names"); return; }
    char nm[5][11]; String joined;
    for (int i = 0; i < 5; i++) {
      String v = a[i].as<String>(); v.trim();
      if (!v.length() || v.length() > 10) { nack("names"); return; }
      for (unsigned j = 0; j < v.length(); j++) if ((uint8_t)v[j] < 32 || (uint8_t)v[j] > 126 || v[j] == '|') { nack("names"); return; }
      strncpy(nm[i], v.c_str(), 10); nm[i][10] = 0; joined += v; if (i < 4) joined += '|';
    }
    memcpy(habName, nm, sizeof habName); prefs.putString("hb_n", joined); needRedraw = true;
  }
  if (!doc["toggle"].isNull()) {
    int i = doc["toggle"] | -1;
    if (i < 0 || i > 4) { nack("habit"); return; }
    if (!timeSynced) { nack("no_clock"); return; }
    habitRoll(); habHist[i] ^= 1; habitSave(); needRedraw = true;
  }
  habitsReply();
}
// ---- {"cmd":"reminders","list":[{"m":20,"t":"Look away"}, ...]} (up to 3; m = minutes, 0 = off), {"test":i} shows reminder i now; no field = read
static void remindersReply() {
  JsonDocument d; d["ok"] = true; d["evt"] = "reminders"; d["active"] = remActive;
  JsonArray l = d["list"].to<JsonArray>();
  for (int i = 0; i < 3; i++) { JsonObject o = l.add<JsonObject>(); o["m"] = remArr[i].min; o["t"] = remArr[i].text; }
  sendDoc(d);
}
static void cmdReminders(JsonDocument& doc) {
  if (!doc["list"].isNull()) {
    JsonArrayConst a = doc["list"].as<JsonArrayConst>();
    if (a.isNull() || a.size() > 3) { nack("list"); return; }
    Reminder tmp[3]; String joined;
    for (size_t i = 0; i < 3; i++) {
      int m = 0; String t;
      if (i < a.size()) {
        m = a[i]["m"] | 0; t = a[i]["t"].as<String>(); t.trim();
        if (m < 0 || m > 1440 || t.length() > 16) { nack("list"); return; }
        for (unsigned j = 0; j < t.length(); j++) if ((uint8_t)t[j] < 32 || (uint8_t)t[j] > 126 || t[j] == '|' || t[j] == ';') { nack("list"); return; }
        if (m && !t.length()) { nack("list"); return; }
      }
      tmp[i].min = (uint16_t)m; strncpy(tmp[i].text, t.c_str(), 16); tmp[i].text[16] = 0;
      joined += String(m) + "|" + t; if (i < 2) joined += ';';
    }
    for (int i = 0; i < 3; i++) remArr[i] = tmp[i];                // (nextAt = 0: the countdown restarts)
    prefs.putString("rem", joined);
  }
  if (!doc["test"].isNull()) {
    int i = doc["test"] | -1;
    if (i < 0 || i > 2 || !remArr[i].text[0]) { nack("test"); return; }
    remTrigger((uint8_t)i);
  }
  remindersReply();
}
static void cmdReboot(JsonDocument& doc) {
  const char* m = doc["mode"] | "normal";
  ack("reboot");
  delay(120);
#ifndef DC_SIM
  if (!strcmp(m, "download")) REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);   // ROM USB flasher, no BOOT button needed
#endif
  esp_restart();
}

static void handleLine(const String& line) {
  JsonDocument doc;
  reqId = -1;
  if (deserializeJson(doc, line)) { nack("json"); return; }
  const char* cmd = doc["cmd"] | "";
  reqId = doc["id"] | -1;

  if (!strcmp(cmd, "stats")) {                                  // no reply (1 Hz telemetry)
    hostCpu = (uint8_t)constrain(doc["cpu"].as<int>(), 0, 100);
    hostRam = (uint8_t)constrain(doc["ram"].as<int>(), 0, 100);
    lastStatsMs = millis();
    if (mode == M_TELEM) needRedraw = true;
  }
  else if (!strcmp(cmd, "hello")) cmdHello();
  else if (!strcmp(cmd, "ping")) { JsonDocument d; d["ok"] = true; d["evt"] = "pong"; d["up"] = millis(); if (!doc["t"].isNull()) d["t"] = doc["t"]; sendDoc(d); }
  else if (!strcmp(cmd, "info")) cmdInfo();
  else if (!strcmp(cmd, "led")) cmdLed(doc);
  else if (!strcmp(cmd, "gpio")) cmdGpio(doc);
  else if (!strcmp(cmd, "inputs")) cmdInputs();
  else if (!strcmp(cmd, "events")) { eventsOn = doc["val"] | true; ack("events"); }
  else if (!strcmp(cmd, "display")) cmdDisplay(doc);
  else if (!strcmp(cmd, "snapshot")) cmdSnapshot();
  else if (!strcmp(cmd, "run")) cmdRun(doc);
  else if (!strcmp(cmd, "input")) cmdInput(doc);
  else if (!strcmp(cmd, "getkeys")) cmdGetKeys(doc);
  else if (!strcmp(cmd, "layer")) cmdLayer(doc);
  else if (!strcmp(cmd, "info_cards")) cmdInfoCards(doc);
  else if (!strcmp(cmd, "factory")) cmdFactory(doc);
  else if (!strcmp(cmd, "boot_opt")) cmdBootOpt(doc);
  else if (!strcmp(cmd, "ota")) cmdOta(doc);
  else if (!strcmp(cmd, "safe_retry")) {                         // leave safe mode: clear the crash counter, then boot normally
    rtcCrashCount = 0; crashCount = 0; bool rb = doc["reboot"] | true; ack("safe_retry");
    if (rb) { delay(150); esp_restart(); }
  }
  else if (cmdDebug(cmd)) { }
  else if (!strcmp(cmd, "selftest")) cmdSelftest();
  else if (!strcmp(cmd, "reboot")) cmdReboot(doc);
  else if (!strcmp(cmd, "echo")) { JsonDocument d; d["ok"] = true; d["evt"] = "echo"; d["data"] = doc["data"]; sendDoc(d); }
  else if (!strcmp(cmd, "remap")) {
    int key = doc["key"] | 0;
    if (key < 1 || key > 9) { nack("key"); return; }                  // 8 / 9 = dial pressed + turned right / left (firmware 1.4)
    const char* gest = doc["gesture"] | "tap";
    char gch = !strcmp(gest, "hold") ? 'h' : !strcmp(gest, "double") ? 'd' : 0;
    if (strcmp(gest, "tap") && !gch) { nack("gesture"); return; }
    if (gch && key > 5) { nack("key"); return; }                      // only the five keys have hold / double-tap actions
    int glay = doc["layer"] | 0;
    if (glay < 0 || glay >= LAYERS) { nack("layer"); return; }
    if (gch && (doc["clear"] | false)) {                             // remove a hold / double-tap action again
      char gk[10]; gestureKey((uint8_t)glay, (uint8_t)(key - 1), gch, gk); prefs.remove(gk); loadGestureFlags(); ack("remap"); return;
    }
    if (key >= 8 && (doc["clear"] | false)) {                          // remove a dial press+turn action again
      char pk[8]; slotKey((uint8_t)glay, (uint8_t)(key - 1), pk); prefs.remove(pk); ack("remap"); return;
    }
    JsonDocument spec;
    spec["type"] = doc["type"]; spec["val"] = doc["val"];
    std::vector<Step> tmp;
    if (!parseSpec(spec.as<JsonVariantConst>(), tmp)) { nack("spec"); return; }
    String s; serializeJson(spec, s);
    if (s.length() > 3800) { nack("too_long"); return; }
    int lay = doc["layer"] | 0;
    if (lay < 0 || lay >= LAYERS) { nack("layer"); return; }
    char k[10];
    if (gch) gestureKey((uint8_t)lay, (uint8_t)(key - 1), gch, k); else slotKey((uint8_t)lay, (uint8_t)(key - 1), k);
    if (prefs.putString(k, s) == 0) { nack("nvs_full"); return; }   // NVS (~20KB) can fill up with several large macros
    if (gch) loadGestureFlags();
    ack("remap");
  }
  else if (!strcmp(cmd, "settings")) cmdSettings(doc);
  else if (!strcmp(cmd, "screens")) cmdScreens(doc);
  else if (!strcmp(cmd, "habits")) cmdHabits(doc);
  else if (!strcmp(cmd, "reminders")) cmdReminders(doc);
  else if (!strcmp(cmd, "gesture_test")) cmdGestureTest(doc);
  else if (!strcmp(cmd, "reset_keys")) {
    int only = doc["layer"] | -1;                                    // omitted = every layer
    for (uint8_t l = 0; l < LAYERS; l++) { if (only >= 0 && l != only) continue; for (uint8_t i = 0; i < 9; i++) { char k[8]; slotKey(l, i, k); prefs.remove(k); if (i < 5) { char g[10]; gestureKey(l, i, 'h', g); prefs.remove(g); gestureKey(l, i, 'd', g); prefs.remove(g); } } }
    loadGestureFlags();
    ack("reset_keys");
  }
  else if (!strcmp(cmd, "brightness")) {
    brightness = (uint8_t)constrain(doc["val"].as<int>(), 5, 255); applyBacklight();
    if (brightness != savedBright) { prefs.putUChar("bright", brightness); savedBright = brightness; }
    ack("brightness");
  }
  else if (!strcmp(cmd, "mode")) { setMode((uint8_t)doc["val"].as<int>()); ack("mode"); }
  else if (!strcmp(cmd, "os")) {
    osMac = !strcmp(doc["val"] | "win", "mac") ? 1 : 0; prefs.putUChar("os", osMac); ack("os");
  }
  else if (!strcmp(cmd, "layout")) {
#if DC_HAS_KB_LAYOUT
    const char* n = doc["val"] | "en_US";
    const uint8_t* map = layoutByName(n);
    if (!map) { nack("layout"); return; }
    kbLayout = n; prefs.putString("kbl", kbLayout);
    Keyboard.releaseAll(); Keyboard.begin(map);
    ack("layout");
#else
    nack("layout_unsupported_core");                 // needs arduino-esp32 core 3.0.0+
#endif
  }
  else if (!strcmp(cmd, "time")) {
    struct timeval tv = {(time_t)doc["epoch"].as<uint32_t>(), 0};
    settimeofday(&tv, nullptr);
    int32_t tz = doc["tz"] | 0;
    if (tz != tzOff) { tzOff = tz; prefs.putInt("tz", tzOff); }
    timeSynced = true; needRedraw = true; ack("time");
  }
  else if (!strcmp(cmd, "wifi")) cmdWifi(doc);
  else if (!strcmp(cmd, "media")) {                             // optional: host corrects the local volume/play model
    if (!doc["vol"].isNull()) vol = constrain(doc["vol"].as<int>(), 0, 100);
    if (!doc["playing"].isNull()) playing = doc["playing"].as<bool>();
    if (!doc["muted"].isNull()) muted = doc["muted"].as<bool>();
    needRedraw = true; ack("media");
  }
  else if (!strcmp(cmd, "gif_begin")) {
    uint32_t size = doc["size"] | 0u;
    if (!okFs && fsState == FS_PREPARING) {                      // demo animation being rebuilt after a failed upload: it ends soon
      uint32_t t0 = millis();
      while (!okFs && (uint32_t)(millis() - t0) < 12000) delay(5);
    }
    if (!okFs) { nack("fs_busy"); return; }                      // first boot: storage is still being formatted
    int slot = doc["slot"] | 0;
    if (slot < 0 || slot >= GIF_SLOTS) { nack("slot"); return; }
    uploadAbort();
    gifClose();                                                   // release the file handle before deleting the file
    { char gp[16]; gifPath((uint8_t)slot, gp); LittleFS.remove(gp); }
    upSlot = (uint8_t)slot;
    uint32_t freeB = (uint32_t)(LittleFS.totalBytes() - LittleFS.usedBytes());
    if (size == 0 || size + 8192 > freeB) { gifRestart = true; nack("no_space"); return; }
    upFile = LittleFS.open("/anim.tmp", "w");
    if (!upFile) { nack("fs"); return; }
    upSize = size; upRx = 0; upCrc = 0; upExpect = doc["crc"] | 0u; upSeq = 0; uploading = true; upLast = millis(); needRedraw = true;
    JsonDocument r; r["ok"] = true; r["evt"] = "gif_ready"; r["chunk"] = upChunk; sendDoc(r);
  }
  else if (!strcmp(cmd, "gif_chunk")) {
    if (!uploading) { nack("no_upload"); return; }
    int seq = doc["seq"] | -1;
    const char* b = doc["data"] | "";
    size_t bl = strlen(b);
    if (seq != upSeq) { nack("seq"); return; }
    if (bl > 1400) { uploadAbort(); nack("chunk"); return; }
    int n = b64dec(b, bl, chunkBuf);
    if (n < 0) { uploadAbort(); nack("b64"); return; }
    if (upRx + n > upSize) { uploadAbort(); nack("overflow"); return; }
    if (upFile.write(chunkBuf, n) != (size_t)n) { uploadAbort(); nack("fs_write"); return; }
    upCrc = crc32u(upCrc, chunkBuf, n); upRx += n; upSeq++; upLast = millis();
    JsonDocument r; r["ok"] = true; r["evt"] = "gif_ack"; r["seq"] = seq; r["rx"] = upRx; sendDoc(r);
  }
  else if (!strcmp(cmd, "gif_end")) {
    if (!uploading) { nack("no_upload"); return; }
    upFile.close(); uploading = false; needRedraw = true;
    if (upRx != upSize || upCrc != upExpect) { LittleFS.remove("/anim.tmp"); gifRestart = true; nack("crc"); return; }
    { char gp[16]; gifPath(upSlot, gp); LittleFS.remove(gp); LittleFS.rename("/anim.tmp", gp); }
    gifSlot = upSlot; setMode(M_GIF); gifRestart = true; ack("gif_done");
  }
  else if (!strcmp(cmd, "gif_abort")) { uploadAbort(); ack("gif_abort"); }
  else if (!strcmp(cmd, "gif_delete")) {
    if (!okFs) { nack("fs_busy"); return; }
    int slot = doc["slot"] | 0;
    if (slot < 0 || slot >= GIF_SLOTS) { nack("slot"); return; }
    uploadAbort(); gifClose();
    { char gp[16]; gifPath((uint8_t)slot, gp); LittleFS.remove(gp); }
    if (gifSlot == slot) gifSlot = 0;
    if (slot == 0 || gifSlotCount() == 0) {
      okFs = false;                                              // slot 0 is the default / demo animation: regenerated in the background
      fsStartAsync(fsPrepTask, FS_PREPARING);
    } else gifRestart = true;
    ack("gif_delete");
  }
  else if (!strcmp(cmd, "gif_list")) cmdGifList();
  else if (!strcmp(cmd, "gif_cfg")) {
    if (!doc["rot"].isNull()) { gifRot = (uint16_t)constrain(doc["rot"].as<int>(), 0, 3600); prefs.putUShort("grot", gifRot); gifSlotAt = millis(); }
    if (!doc["slot"].isNull()) {
      int sl = doc["slot"].as<int>();
      if (sl < 0 || sl >= GIF_SLOTS || !gifSlotExists((uint8_t)sl)) { nack("slot"); return; }
      gifSlot = (uint8_t)sl; gifRestart = true;
    }
    cmdGifList();
  }
  else nack("unknown_cmd");
  reqId = -1;
}
static void serialService() {
  int guard = 0;
  while (Serial.available() && guard++ < 4096) {
    char c = (char)Serial.read();
    lastRxMs = millis();
    if (c == '\n') { if (!rxOverflow && rxLine.length()) handleLine(rxLine); rxLine = ""; rxOverflow = false; }
    else if (c != '\r') { if (rxLine.length() < RX_MAX) rxLine += c; else rxOverflow = true; }
  }
}

// ================================================================ setup / loop
static void loadSettings() {
  brightness = prefs.getUChar("bright", 200); if (brightness < 5) brightness = 5;
  savedBright = brightness;
  mode = prefs.getUChar("mode", M_CLOCK); if (mode < 1 || mode > NUM_MODES) mode = M_CLOCK;
  osMac = prefs.getUChar("os", 0);
  kbLayout = prefs.getString("kbl", "en_US"); if (!layoutByName(kbLayout.c_str())) kbLayout = "en_US";
  pomoMinutes = prefs.getUChar("pomo", 25); if (pomoMinutes < 1 || pomoMinutes > 90) pomoMinutes = 25;
  pomoRemain = pomoMinutes * 60000UL;
  tzOff = prefs.getInt("tz", 0);
  gifRot = prefs.getUShort("grot", 0); if (gifRot > 3600) gifRot = 0;
  dispOff = prefs.getBool("nodisp", false);
  dialAccel = prefs.getUChar("dacc", 0); if (dialAccel > 2) dialAccel = 0;
  clockStyle = prefs.getUChar("cstyle", 0); if (clockStyle > 3) clockStyle = 0;
  saverSec = prefs.getUShort("savs", 0); if (saverSec > 3600) saverSec = 0;
  saverStyle = prefs.getUChar("savst", 1); if (saverStyle < 1 || saverStyle > 3) saverStyle = 1;
  nightOn = prefs.getBool("nd_on", false);
  nightFrom = prefs.getUChar("nd_f", 22); if (nightFrom > 23) nightFrom = 22;
  nightTo = prefs.getUChar("nd_t", 7); if (nightTo > 23) nightTo = 7;
  nightLevel = prefs.getUChar("nd_l", 30); if (nightLevel < 5) nightLevel = 30;
  modeMask = prefs.getUShort("mmask", 0x003F) & 0x0FFF; if (!modeMask) modeMask = 0x003F;
  snBest = prefs.getUShort("snkb", 0);
  {                                                               // reminders "min|text;min|text;min|text"
    String r = prefs.getString("rem", ""); int pos = 0;
    for (int i = 0; i < 3 && pos <= (int)r.length(); i++) {
      int semi = r.indexOf(';', pos); if (semi < 0) semi = r.length();
      String part = r.substring(pos, semi); int bar = part.indexOf('|');
      if (bar > 0) { remArr[i].min = (uint16_t)constrain(part.substring(0, bar).toInt(), 0, 1440); strncpy(remArr[i].text, part.substring(bar + 1).c_str(), 16); remArr[i].text[16] = 0; }
      pos = semi + 1;
    }
  }
  {                                                               // habits: names "A|B|C|D|E", day number, 8-day history per habit
    String n = prefs.getString("hb_n", ""); int pos = 0;
    for (int i = 0; i < 5 && pos <= (int)n.length() && n.length(); i++) {
      int bar = n.indexOf('|', pos); if (bar < 0) bar = n.length();
      strncpy(habName[i], n.substring(pos, bar).c_str(), 10); habName[i][10] = 0; pos = bar + 1;
    }
    habDay = prefs.getULong("hb_d", 0);
    for (int i = 0; i < 5; i++) { char k[6]; snprintf(k, sizeof k, "hb_%d", i); habHist[i] = prefs.getUChar(k, 0); }
  }
  otaOn = prefs.getBool("ota", false); otaPw = prefs.getString("otapw", "");
  ledMode = prefs.getUChar("ledm", 0) == 1 ? LM_OFF : LM_AUTO;
  wifiSsid = prefs.getString("ssid", ""); wifiPass = prefs.getString("wpass", "");
  uint32_t e = prefs.getULong("epoch", 0);                       // last known time: clock starts close even without host/NTP
  if (e > 1700000000UL) { struct timeval tv = {(time_t)e, 0}; settimeofday(&tv, nullptr); }
}

void setup() {
  // ---- 1. LED first: the very first visible sign of life (purple), before anything can fail
  ledBoot(24, 0, 24);
  for (int i = 0; i < 5; i++) { keyBtn[i].attach(PIN_KEY[i], INPUT_PULLUP); keyBtn[i].interval(8); }
  encBtn.attach(PIN_ENC_SW, INPUT_PULLUP); encBtn.interval(8);
  pinMode(PIN_ENC_A, INPUT_PULLUP); pinMode(PIN_ENC_B, INPUT_PULLUP);
  backlightInit(); backlightSet(0);

  // ---- 2. crash-loop protection: panic / watchdog / brownout resets are counted in RTC memory
  resetReason = esp_reset_reason();
  bool crashed = resetReason == ESP_RST_PANIC || resetReason == ESP_RST_INT_WDT || resetReason == ESP_RST_TASK_WDT ||
                 resetReason == ESP_RST_WDT || resetReason == ESP_RST_BROWNOUT;
  if (rtcMagic != RTC_MAGIC || resetReason == ESP_RST_POWERON) { rtcMagic = RTC_MAGIC; rtcCrashCount = 0; }
  if (rtcMagic != RTC_MAGIC || resetReason == ESP_RST_POWERON) rtcDispTry = 0;
  rtcCrashCount = crashed ? rtcCrashCount + 1 : 0;
  crashCount = rtcCrashCount;
  safeMode = DC_FORCE_SAFE || crashCount >= 3;
  bootNote(resetReasonStr(resetReason), !crashed);

  // ---- 3. settings, then USB (serial + HID) - no display / filesystem work before the PC can see the device
  okPrefs = prefs.begin("deskcomp", false);
  bootNote("prefs", okPrefs);
  loadSettings();                    // needs kbLayout before Keyboard.begin() below
  loadGestureFlags();
  lastActivity = millis();

  upChunk = (Serial.setRxBufferSize(8192) >= 4096) ? 768 : 128;  // core 2.0.x cannot grow the RX queue after boot -> small chunks
  Serial.begin(115200);
#if DC_HAS_HID
  USB.productName("DeskCompanion");  // CDC was registered by the core at boot, USB itself starts at USB.begin() below
  USB.manufacturerName("DeskCompanion");
#if DC_HAS_KB_LAYOUT
  Keyboard.begin(layoutByName(kbLayout.c_str()));   // host keyboard layout saved on the pad; begin() exactly once
#else
  Keyboard.begin();
#endif
  ConsumerControl.begin();
  Mouse.begin();
  USB.begin();
#endif
  rxLine.reserve(RX_MAX + 16);
  bootNote("usb", true);
  ledBoot(0, 0, 40);                 // blue: USB is up, the app can already talk to the pad

  // ---- 4. filesystem
  gif.begin(BIG_ENDIAN_PIXELS);      // TFT_eSPI::pushPixels() expects big-endian RGB565 (same as the library's TFT_eSPI example)
  fsState = FS_MOUNTING;             // the job itself starts ~3 s later from loop(), after the PC finished enumerating the USB device
  fsStartAt = millis() + 3000;       // (flash erase stalls non-IRAM interrupts for tens of ms - keep that away from USB enumeration)
  bootNote("fs-scheduled", true);
  ledBoot(0, 30, 30);                // cyan: filesystem scheduled

  // ---- 5. display (skipped in safe mode; a failure here never takes the serial link down)
  if (!safeMode && !dispOff) {
#if DC_HAS_TFT
    // The panel start-up runs in its own task with a deadline: if the SPI driver ever stalls here, the pad still comes up
    // (serial link, keys, LED) with the display off and says why ("disp_why") instead of sitting silent after the cyan LED.
    if (rtcDispTry == RTC_DISP_TRY) {                 // the previous start-up never finished (hang -> reset): do not run into it again
      dispWhy = "skipped_after_hang"; bootNote("display-skipped(hung-last-boot)", false);
    } else {
      rtcDispTry = RTC_DISP_TRY; dispInitDone = false;
      TaskHandle_t dispTask = nullptr;
      xTaskCreatePinnedToCore([](void*) {
        tft.init();
        tft.fillScreen(TFT_BLACK);
        spr.setColorDepth(16);
        okSprite = spr.createSprite(240, 240) != nullptr;
        dispInitDone = true;
        vTaskDelete(nullptr);
      }, "dispinit", 6144, nullptr, 1, &dispTask, 0);
      uint32_t t0 = millis();
      while (!dispInitDone && millis() - t0 < 4000) delay(5);
      if (dispInitDone) {
        rtcDispTry = 0; okDisp = okSprite;
        if (!okSprite) dispWhy = "sprite_alloc";
        bootNote("display", okDisp);
      } else {
        if (dispTask) vTaskDelete(dispTask);          // stop the stuck task before the task watchdog trips; rtcDispTry stays set
        okDisp = okSprite = false; dispWhy = "init_hang"; bootNote("display-HANG", false);
      }
    }
#else
    spr.setColorDepth(16);
    okSprite = spr.createSprite(240, 240) != nullptr;
    okDisp = okSprite;
    bootNote("display", okDisp);
#endif
  } else bootNote(safeMode ? "display-skipped(safe-mode)" : "display-skipped(boot_opt)", false);
  ledBoot(30, 24, 0);                // amber: display done

  attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encISR, CHANGE);
  if (mode == M_GIF && okDisp) gifRestart = true;
  else renderFrame();
  applyBacklight();

  ledReady = true;                   // from here the LED shows the heartbeat (green = ok, amber = degraded, red = safe mode)
  ledNextAt = 0;
  bootNote("ready", true);
}

void loop() {
  serialService();
  inputsService();
  macroTick();
  pomoTick();
  snakeTick();
  reactTick();
  remTick();
  netService();
  otaService();
  ledService();
  uint32_t now = millis();
  if (mode == M_INFO && nCards > 1 && cardRotSec && (uint32_t)(now - cardAt) >= (uint32_t)cardRotSec * 1000UL) { cardIdx = (cardIdx + 1) % nCards; cardAt = now; needRedraw = true; }

  if (fsStartAt && (int32_t)(now - fsStartAt) >= 0) { fsStartAt = 0; fsStartAsync(fsTask, FS_MOUNTING); }
  { static uint32_t svAt = 0, ndAt = 0;                         // screensaver start / night dimming follow the clock, twice a second / every 15 s
    if ((uint32_t)(now - svAt) >= 500) {
      svAt = now;
      bool can = saverSec > 0 && okSprite && mode != M_GIF && !menuOpen && !uploading && !dispHoldUntil && !(mode == M_POMO && pomoState == PS_RUN) &&
                 !remActive && !(mode == M_STOPW && swRun) && !(mode == M_BREATH && brRun) && !(mode == M_SNAKE && snRun) && !(mode == M_REACT && (rxState == 1 || rxState == 2));
      bool want = can && (uint32_t)(now - lastActivity) >= (uint32_t)saverSec * 1000UL;
      if (want != saverOn) { saverOn = want; needRedraw = true; }
    }
    if ((uint32_t)(now - ndAt) >= 15000) { ndAt = now; if (nightOn && effBright() != lastBl) applyBacklight(); }
  }
  if (uploading && now - upLast > 6000) uploadAbort();           // host vanished mid-upload
  if (menuOpen && now - menuTouched > MENU_TIMEOUT_MS) menuCloseNow();
  if (timeSynced && now - lastEpochSave > 1800000UL) { lastEpochSave = now; prefs.putULong("epoch", (uint32_t)time(nullptr)); }
  if (dispHoldUntil && (int32_t)(now - dispHoldUntil) >= 0) {     // a Dev-tab test pattern timed out: back to the normal screen
    dispHoldUntil = 0; needRedraw = true; if (mode == M_GIF) gifRestart = true;
  }
  if (mode == M_CLOCK && !menuOpen && !uploading) {
    time_t s = time(nullptr) + tzOff;
    if (s != lastClockSec) { lastClockSec = s; needRedraw = true; }
  }

  if (okDisp && !dispHoldUntil) {
    bool gifMode = (mode == M_GIF && !menuOpen && !uploading);
    if (gifMode) gifService();
    if (!(gifMode && gifOpen)) {
      if (needRedraw || now - lastRender >= renderInterval()) { needRedraw = false; lastRender = now; renderFrame(); }
    }
  }
  delay(1);
}
