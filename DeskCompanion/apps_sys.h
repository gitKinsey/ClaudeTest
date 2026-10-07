// ================================================================================================================
//  Firmware 2.0: system helpers - crash breadcrumbs (50), lifetime statistics (49), LED event language (44), per-screen brightness / screensaver (43),
//  typing ripples (33), pet reactions (37), word clock + more clock faces (31), more screensavers (32), anti-aliased rings (42), text folding (41).
//  Included by DeskCompanion.ino after apps_cmd.h.
// ================================================================================================================

// ================================================================ crash breadcrumbs: a ring of the last 48 events in RTC memory (survives panics / watchdog / software resets)
struct Crumb { uint32_t ms; uint8_t kind, a; uint16_t b; };
struct CrumbRing { uint32_t magic; uint16_t head, n; uint32_t boots; Crumb ring[48]; };
static RTC_NOINIT_ATTR CrumbRing rtcCr;
static Crumb crPrev[48]; static uint16_t crPrevN = 0; static uint32_t crPrevBoots = 0;
static const uint32_t CR_MAGIC = 0xC0FFEE48u;
static void crumb(uint8_t kind, uint8_t a, uint16_t b) {
  if (rtcCr.magic != CR_MAGIC) return;
  Crumb& c = rtcCr.ring[rtcCr.head]; c.ms = millis(); c.kind = kind; c.a = a; c.b = b;
  rtcCr.head = (uint16_t)((rtcCr.head + 1) % 48); if (rtcCr.n < 48) rtcCr.n++;
}
static void crumbBoot(uint8_t reason, uint16_t crashes) {
  crPrevN = 0;
  if (rtcCr.magic == CR_MAGIC && reason != ESP_RST_POWERON && rtcCr.n <= 48) {      // a reset that kept RTC memory: keep what the pad was doing before it
    uint16_t start = rtcCr.n < 48 ? 0 : rtcCr.head;
    for (uint16_t i = 0; i < rtcCr.n; i++) crPrev[crPrevN++] = rtcCr.ring[(start + i) % 48];
    crPrevBoots = rtcCr.boots;
  } else { rtcCr.boots = 0; crPrevBoots = 0; }
  rtcCr.magic = CR_MAGIC; rtcCr.head = 0; rtcCr.n = 0; rtcCr.boots++;
  crumb(8, reason, crashes);
}
static void crumbText(const Crumb& c, char* out, size_t n) {
  switch (c.kind) {
    case 1: snprintf(out, n, "K%u L%u", c.a + 1, c.b + 1); break;
    case 2: snprintf(out, n, "K%u %s", c.a + 1, c.b == 'h' ? "HOLD" : c.b == 'd' ? "DOUBLE" : c.b == 't' ? "TRIPLE" : c.b == 'j' ? "HOLD1S" : c.b == 'k' ? "HOLD2S" : "SHIFT"); break;
    case 3: snprintf(out, n, "DIAL%s%u", c.a ? "+" : "-", c.b); break;
    case 4: snprintf(out, n, "DIALCLICK"); break;
    case 5: snprintf(out, n, "SCREEN %u", c.a); break;
    case 6: snprintf(out, n, "LAYER %u", c.a + 1); break;
    case 7: snprintf(out, n, "CMD %c%c%c", (char)c.a, (char)(c.b & 255), (char)(c.b >> 8)); break;
    case 8: snprintf(out, n, "BOOT r%u c%u", c.a, c.b); break;
    case 9: snprintf(out, n, "MACRO %u", c.a); break;
    case 10: snprintf(out, n, c.a ? "USB SUSPEND" : "USB RESUME"); break;
    case 11: snprintf(out, n, "ERR %u", c.a); break;
    case 12: snprintf(out, n, "PIN %s", c.a ? "LOCK" : "UNLOCK"); break;
    default: snprintf(out, n, "? %u", c.kind); break;
  }
  for (char* p = out; *p; p++) if (*p == '\n' || *p == '"' || *p == '\\' || (uint8_t)*p < 32 || (uint8_t)*p > 126) *p = '?';
}
static void crumbsJson(JsonArray a, const Crumb* r, uint16_t n, uint16_t head, bool wrapped) {
  uint16_t start = wrapped ? head : 0;
  for (uint16_t i = 0; i < n; i++) { const Crumb& c = r[(start + i) % 48]; JsonObject o = a.add<JsonObject>(); char t[24]; crumbText(c, t, sizeof t); o["ms"] = c.ms; o["k"] = c.kind; o["s"] = t; }
}
static String crumbsTail(int n) {                                  // the last n events before the previous reset, in one short line (the boot log / diagnostics page)
  String s; int from = (int)crPrevN > n ? (int)crPrevN - n : 0;
  for (int i = from; i < (int)crPrevN; i++) { char t[24]; crumbText(crPrev[i], t, sizeof t); if (s.length()) s += ", "; s += t; }
  return s;
}

// ================================================================ lifetime statistics (idea 49): saved to flash every 10 minutes and on request
struct LtStats { uint32_t dialCw, dialCcw, dialClick, minutes, boots; };
static LtStats lt; static bool ltDirty = false; static uint32_t ltSaveAt = 0, ltMinAt = 0;
static void ltLoad() { memset(&lt, 0, sizeof lt); if (prefs.getBytes("life", &lt, sizeof lt) != sizeof lt) memset(&lt, 0, sizeof lt); lt.boots++; ltDirty = true; ltMinAt = millis(); }
static void ltSave() { prefs.putBytes("life", &lt, sizeof lt); ltDirty = false; ltSaveAt = millis(); }
static void ltDial(int steps) { if (steps > 0) lt.dialCw += (uint32_t)steps; else lt.dialCcw += (uint32_t)(-steps); ltDirty = true; }
static void ltClick() { lt.dialClick++; ltDirty = true; }
static void ltTick() {
  uint32_t now = millis();
  if ((uint32_t)(now - ltMinAt) >= 60000UL) { ltMinAt += 60000UL; lt.minutes++; ltDirty = true; }
  if (ltDirty && (uint32_t)(now - ltSaveAt) >= 600000UL) ltSave();
}

// ================================================================ LED event language (idea 44): named blink patterns with a priority; higher priority pre-empts, the rest queue
struct LedStep { uint8_t r, g, b; uint16_t ms; };
struct LedEv { char name[11]; uint8_t prio, reps, n; LedStep st[6]; bool used; };
static LedEv ledEvs[8];
static int8_t levActive = -1; static uint8_t levStep = 0, levRepsLeft = 0; static uint32_t levStepEnd = 0; static int8_t levQueue[4]; static uint8_t levQn = 0; static uint32_t levPlayed = 0;
static bool apColor(const char* h, uint8_t& r, uint8_t& g, uint8_t& b) { if (*h == '#') h++; uint8_t v[3]; if (strlen(h) != 6 || !apHex(h, v, 3)) return false; r = v[0]; g = v[1]; b = v[2]; return true; }
static void levLoad() {
  for (int i = 0; i < 8; i++) ledEvs[i].used = false;
  String js = prefs.getString("levs", ""); JsonDocument d; if (!js.length() || deserializeJson(d, js)) return;
  int i = 0;
  for (JsonVariantConst v : d.as<JsonArrayConst>()) {
    if (i >= 8) break; LedEv& e = ledEvs[i]; strncpy(e.name, v["n"] | "", 10); e.name[10] = 0; e.prio = (uint8_t)(v["p"] | 1); e.reps = (uint8_t)(v["rep"] | 1); e.n = 0;
    for (JsonVariantConst s : v["steps"].as<JsonArrayConst>()) { if (e.n >= 6) break; uint8_t r = 0, g = 0, b = 0; apColor(s["c"] | "000000", r, g, b); e.st[e.n].r = r; e.st[e.n].g = g; e.st[e.n].b = b; e.st[e.n].ms = (uint16_t)(s["ms"] | 200); e.n++; }
    e.used = e.n > 0; i++;
  }
}
static void levStart(int idx) { levActive = (int8_t)idx; levStep = 0; levRepsLeft = ledEvs[idx].reps; levStepEnd = millis() + ledEvs[idx].st[0].ms; levPlayed++; }
static void levTrigger(int idx) {
  if (levActive < 0) { levStart(idx); return; }
  if (ledEvs[idx].prio > ledEvs[levActive].prio) { levStart(idx); levQn = 0; return; }              // a more important event pre-empts the running one
  for (uint8_t i = 0; i < levQn; i++) if (levQueue[i] == idx) return;                                  // already waiting
  if (levQn < 4) levQueue[levQn++] = (int8_t)idx;
}
static bool ledEventActive(uint8_t& r, uint8_t& g, uint8_t& b) {
  if (levActive < 0) return false;
  uint32_t now = millis();
  LedEv& e = ledEvs[levActive];
  while ((int32_t)(now - levStepEnd) >= 0) {
    levStep++;
    if (levStep >= e.n) { if (--levRepsLeft > 0) levStep = 0;
      else {                                                                                            // finished: the highest priority waiting event (oldest first) is next
        levActive = -1;
        if (levQn) { int best = 0; for (uint8_t i = 1; i < levQn; i++) if (ledEvs[levQueue[i]].prio > ledEvs[levQueue[best]].prio) best = i; int idx = levQueue[best]; for (uint8_t i = best; i + 1 < levQn; i++) levQueue[i] = levQueue[i + 1]; levQn--; levStart(idx); e = ledEvs[levActive]; now = millis(); continue; }
        return false;
      } }
    levStepEnd += e.st[levStep].ms;
  }
  r = e.st[levStep].r / 3; g = e.st[levStep].g / 3; b = e.st[levStep].b / 3;
  return true;
}

// ================================================================ typing ripples (idea 33): every key press sends a shock wave across the screen
struct Ripple { uint32_t at; uint8_t key; bool on; };
static Ripple ripples[6];
static void ripplesAdd(int key) { for (int i = 0; i < 6; i++) if (!ripples[i].on) { ripples[i].on = true; ripples[i].at = millis(); ripples[i].key = (uint8_t)key; return; } ripples[0].at = millis(); ripples[0].key = (uint8_t)key; }
static bool ripplesActive() { uint32_t now = millis(); for (int i = 0; i < 6; i++) if (ripples[i].on) { if ((uint32_t)(now - ripples[i].at) > 800) ripples[i].on = false; else return true; } return false; }
static void ripplesDraw() {
  if (!ripplesOn) return;
  uint32_t now = millis();
  for (int i = 0; i < 6; i++) if (ripples[i].on) {
    uint32_t age = now - ripples[i].at; if (age > 800) { ripples[i].on = false; continue; }
    int r = (int)(age / 5); int cx = 30 + ripples[i].key * 45, cy = 200;
    uint8_t v = (uint8_t)(255 - age * 255 / 800); uint8_t hr, hg, hb; hsv2rgb((uint16_t)(ripples[i].key * 60 + 190), &hr, &hg, &hb);
    uint16_t col = rgb((uint8_t)(hr * v / 255), (uint8_t)(hg * v / 255), (uint8_t)(hb * v / 255));
    spr.drawCircle(cx, cy, r, col); if (r > 2) spr.drawCircle(cx, cy, r - 1, col);
  }
}
static uint16_t effSaverSec() { int16_t v = scrSaver[mode]; return v < 0 ? 0 : v > 0 ? (uint16_t)v : saverSec; }

// ================================================================ settings that live here (staged: validate first, apply after)
static bool extStHas[6]; static bool extStBool[4]; static uint8_t extStBright[33]; static int16_t extStSaver[33];
static bool extSettingsParse(JsonDocument& doc, bool& bad) {
  bool any = false;
  static const char* const BN[4] = {"suspend_dim", "ripples", "aa", "fold_text"};
  for (int i = 0; i < 4; i++) { extStHas[i] = !doc[BN[i]].isNull(); if (extStHas[i]) { any = true; if (!doc[BN[i]].is<bool>()) bad = true; else extStBool[i] = doc[BN[i]].as<bool>(); } }
  extStHas[4] = !doc["scr_bright"].isNull(); extStHas[5] = !doc["scr_saver"].isNull();
  if (extStHas[4]) { any = true; JsonArrayConst a = doc["scr_bright"].as<JsonArrayConst>(); if (a.isNull() || a.size() != 32) bad = true; else for (int i = 0; i < 32; i++) { if (!a[i].is<int>() || a[i].as<int>() < 0 || a[i].as<int>() > 200 || (a[i].as<int>() > 0 && a[i].as<int>() < 5)) bad = true; else extStBright[i + 1] = (uint8_t)a[i].as<int>(); } }
  if (extStHas[5]) { any = true; JsonArrayConst a = doc["scr_saver"].as<JsonArrayConst>(); if (a.isNull() || a.size() != 32) bad = true; else for (int i = 0; i < 32; i++) { if (!a[i].is<int>() || a[i].as<int>() < -1 || a[i].as<int>() > 3600) bad = true; else extStSaver[i + 1] = (int16_t)a[i].as<int>(); } }
  return any;
}
static void extSettingsApply() {
  if (extStHas[0]) { suspDimOn = extStBool[0]; prefs.putBool("suspd", suspDimOn); applyBacklight(); }
  if (extStHas[1]) { ripplesOn = extStBool[1]; prefs.putBool("ripl", ripplesOn); }
  if (extStHas[2]) { aaOn = extStBool[2]; prefs.putBool("aa", aaOn); needRedraw = true; }
  if (extStHas[3]) { foldOn = extStBool[3]; prefs.putBool("fold", foldOn); }
  if (extStHas[4]) { memcpy(scrBright + 1, extStBright + 1, 32); prefs.putBytes("sbr", scrBright + 1, 32); applyBacklight(); }
  if (extStHas[5]) { memcpy(scrSaver + 1, extStSaver + 1, 32 * sizeof(int16_t)); prefs.putBytes("ssv", scrSaver + 1, 32 * sizeof(int16_t)); }
}
static void extSettingsReply(JsonDocument& d) {
  d["suspend_dim"] = suspDimOn; d["ripples"] = ripplesOn; d["aa"] = aaOn; d["fold_text"] = foldOn;
  JsonArray b = d["scr_bright"].to<JsonArray>(), s = d["scr_saver"].to<JsonArray>(); for (int i = 1; i <= 32; i++) { b.add(scrBright[i]); s.add(scrSaver[i]); }
}
static void sysLoad() {
  memset(scrBright, 0, sizeof scrBright); memset(scrSaver, 0, sizeof scrSaver);
  foldOn = prefs.getBool("fold", false); suspDimOn = prefs.getBool("suspd", true); ripplesOn = prefs.getBool("ripl", false); aaOn = prefs.getBool("aa", false);
  uint8_t b[32]; if (prefs.getBytes("sbr", b, 32) == 32) memcpy(scrBright + 1, b, 32);
  int16_t s[32]; if (prefs.getBytes("ssv", s, sizeof s) == sizeof s) memcpy(scrSaver + 1, s, sizeof s);
  levLoad(); ltLoad();
}

// ================================================================ pet reactions (idea 37): sweats at high CPU, sleeps at night, cheers when CI passes, sulks when it fails
static bool petNightSlept = false;
static bool petSweats() { return lastStatsMs && (uint32_t)(millis() - lastStatsMs) < 5000 && hostCpu >= 85; }
static void petReactTick() {
  static uint32_t chk = 0; uint32_t now = millis();
  if ((uint32_t)(now - chk) < 1000) return; chk = now;
  if (!timeSynced) return;
  struct tm t; localTm(t); bool night = t.tm_hour >= 23 || t.tm_hour < 7;
  if (night && !petSleep && !petNightSlept && (uint32_t)(now - lastActivity) > 10000 && !petSweats()) { petSleep = true; petNightSlept = true; needRedraw = true; }
  if (!night) petNightSlept = false;
}

// ================================================================ word clock (idea 31): "IT IS HALF PAST TEN", the sentence is lit in an 11 x 10 letter grid
static const char* const WC_GRID[10] = {"ITLISASAMPM", "ACQUARTERDC", "TWENTYFIVEX", "HALFBTENFTO", "PASTERUNINE", "ONESIXTHREE", "FOURFIVETWO", "EIGHTELEVEN", "SEVENTWELVE", "TENSEOCLOCK"};
struct WcWord { const char* w; uint8_t r, c, n; };
static const WcWord WC_IT = {"IT", 0, 0, 2}, WC_IS = {"IS", 0, 3, 2}, WC_A = {"A", 1, 0, 1}, WC_QUARTER = {"QUARTER", 1, 2, 7}, WC_TWENTY = {"TWENTY", 2, 0, 6}, WC_FIVE_M = {"FIVE", 2, 6, 4},
                      WC_HALF = {"HALF", 3, 0, 4}, WC_TEN_M = {"TEN", 3, 5, 3}, WC_TO = {"TO", 3, 9, 2}, WC_PAST = {"PAST", 4, 0, 4}, WC_OCLOCK = {"OCLOCK", 9, 5, 6};
static const WcWord WC_HOURS[12] = {{"ONE", 5, 0, 3}, {"TWO", 6, 8, 3}, {"THREE", 5, 6, 5}, {"FOUR", 6, 0, 4}, {"FIVE", 6, 4, 4}, {"SIX", 5, 3, 3}, {"SEVEN", 8, 0, 5}, {"EIGHT", 7, 0, 5}, {"NINE", 4, 7, 4}, {"TEN", 9, 0, 3}, {"ELEVEN", 7, 5, 6}, {"TWELVE", 8, 5, 6}};
static int wcWords(int h, int m, const WcWord** out) {                     // the words that make up the time, in reading order
  int n = 0, m5 = ((m + 2) / 5) * 5; if (m5 >= 60) { m5 = 0; h++; }
  out[n++] = &WC_IT; out[n++] = &WC_IS;
  bool to = m5 > 30; int hh = to ? h + 1 : h;
  switch (m5) {
    case 5: case 55: out[n++] = &WC_FIVE_M; break;
    case 10: case 50: out[n++] = &WC_TEN_M; break;
    case 15: case 45: out[n++] = &WC_A; out[n++] = &WC_QUARTER; break;
    case 20: case 40: out[n++] = &WC_TWENTY; break;
    case 25: case 35: out[n++] = &WC_TWENTY; out[n++] = &WC_FIVE_M; break;
    case 30: out[n++] = &WC_HALF; break;
    default: break;
  }
  if (m5 == 0) { out[n++] = &WC_HOURS[(hh + 11) % 12]; out[n++] = &WC_OCLOCK; }
  else { out[n++] = to ? &WC_TO : &WC_PAST; out[n++] = &WC_HOURS[(hh + 11) % 12]; }
  return n;
}
static String wcSentence(int h, int m) { const WcWord* w[8]; int n = wcWords(h, m, w); String s; for (int i = 0; i < n; i++) { if (i) s += ' '; s += w[i]->w; } return s; }
static uint32_t clockFlipAt = 0;
static void sceneClockExtra(const struct tm& t) {
  spr.fillSprite(C_BG); spr.setTextDatum(MC_DATUM); char b[8];
  if (clockStyle == 8) {                                                  // word clock
    bool lit[10][11]; memset(lit, 0, sizeof lit); const WcWord* w[8]; int n = wcWords(t.tm_hour, t.tm_min, w);
    for (int i = 0; i < n; i++) for (int k = 0; k < w[i]->n; k++) lit[w[i]->r][w[i]->c + k] = true;
    for (int r = 0; r < 10; r++) for (int c = 0; c < 11; c++) { char ch[2] = {WC_GRID[r][c], 0}; spr.setTextColor(lit[r][c] ? C_ACC : C_DIM2); spr.drawString(ch, 41 + c * 16, 48 + r * 16, 2); }
    return;
  }
  if (clockStyle == 7) {                                                  // nixie tubes: four glowing orange digits
    snprintf(b, sizeof b, "%02d%02d", t.tm_hour, t.tm_min);
    for (int i = 0; i < 4; i++) {
      int x = 24 + i * 50 + (i >= 2 ? 8 : 0); spr.fillRoundRect(x, 70, 44, 90, 14, rgb(40, 22, 10)); spr.drawRoundRect(x, 70, 44, 90, 14, rgb(120, 60, 20));
      char d[2] = {b[i], 0}; spr.setTextColor(rgb(110, 40, 0)); spr.drawString(d, x + 23, 116, 7); spr.setTextColor(rgb(255, 150, 40)); spr.drawString(d, x + 22, 115, 7);
    }
    spr.fillCircle(124, 100, 3, rgb(255, 140, 30)); spr.fillCircle(124, 130, 3, rgb(255, 140, 30));
    return;
  }
  static uint8_t lastMin = 255; static char prev[8] = "";                    // style 6: split-flap cards, the new minute flips in
  snprintf(b, sizeof b, "%02d%02d", t.tm_hour, t.tm_min);
  if (t.tm_min != lastMin) { lastMin = (uint8_t)t.tm_min; clockFlipAt = millis(); }
  uint32_t age = millis() - clockFlipAt;
  for (int i = 0; i < 4; i++) {
    int x = 22 + i * 52 + (i >= 2 ? 8 : 0); bool flipping = age < 300 && prev[i] && prev[i] != b[i];
    spr.fillRoundRect(x, 68, 46, 100, 8, C_DIM); spr.drawFastHLine(x, 118, 46, C_BG);
    char d[2] = {b[i], 0}; spr.setTextColor(C_TXT);
    if (flipping) { int h = (int)(50 * (age < 150 ? 1.0f - age / 150.0f : (age - 150) / 150.0f)); char o[2] = {prev[i], 0}; spr.drawString(age < 150 ? o : d, x + 23, 118, 6); spr.fillRect(x, 118 - h, 46, 2 * h, age < 150 ? C_DIM : C_DIM); spr.setTextColor(C_TXT); }
    else spr.drawString(d, x + 23, 118, 6);
  }
  if (age >= 300) memcpy(prev, b, 5);
  spr.fillCircle(124, 100, 3, C_ACC); spr.fillCircle(124, 136, 3, C_ACC);
}
static bool clockFlipping() { return (uint32_t)(millis() - clockFlipAt) < 400; }

// ================================================================ more screensavers (idea 32): 8 sketch, 9 plasma, 10 lava lamp, 11 matrix rain, 12 Lissajous, 13 aurora
static void saverExtra(uint8_t st) {
  uint32_t now = millis(); float t = now / 1000.0f;
  spr.fillSprite(C_BG);
  if (st == 8) {                                                            // the pixel sketch drifts slowly across the screen (no burn-in)
    int ox = 20 + (int)(10 * sinf(t / 17.0f)), oy = 20 + (int)(10 * cosf(t / 23.0f));
    for (int y = 0; y < 40; y++) for (int x = 0; x < 40; x++) if (skGet(x, y)) spr.fillRect(ox + x * 5, oy + y * 5, 4, 4, C_ACC);
  } else if (st == 9) {                                                     // plasma, 4 x 4 pixel blocks
    for (int by = 0; by < 60; by++) for (int bx = 0; bx < 60; bx++) {
      float v = sinf(bx * 0.19f + t) + sinf(by * 0.23f - t * 0.7f) + sinf((bx + by) * 0.12f + t * 0.5f) + sinf(sqrtf((float)((bx - 30) * (bx - 30) + (by - 30) * (by - 30))) * 0.25f - t);
      uint8_t r, g, b; hsv2rgb((uint16_t)(((int)((v + 4.0f) * 45.0f) + (int)(t * 20)) % 360), &r, &g, &b); spr.fillRect(bx * 4, by * 4, 4, 4, rgb(r / 2, g / 2, b / 2));
    }
  } else if (st == 10) {                                                    // lava lamp: four metaballs
    float bx[4], by[4]; for (int i = 0; i < 4; i++) { bx[i] = 30 + 20 * sinf(t * (0.3f + i * 0.11f) + i * 1.7f); by[i] = 30 + 22 * cosf(t * (0.23f + i * 0.07f) + i * 2.3f); }
    for (int y = 0; y < 60; y++) for (int x = 0; x < 60; x++) {
      float f = 0; for (int i = 0; i < 4; i++) { float dx = x - bx[i], dy = y - by[i]; f += 90.0f / (dx * dx + dy * dy + 1.0f); }
      if (f > 1.0f) { int l = (int)constrain((f - 1.0f) * 120.0f, 0.0f, 255.0f); spr.fillRect(x * 4, y * 4, 4, 4, rgb(255, (uint8_t)(40 + l / 4), (uint8_t)(l / 8))); }
    }
  } else if (st == 11) {                                                    // matrix rain: 24 columns of falling glyphs
    static int16_t head[24]; static uint8_t spd[24]; static bool init = false;
    if (!init) { init = true; for (int i = 0; i < 24; i++) { head[i] = (int16_t)(sRand() % 24); spd[i] = (uint8_t)(1 + sRand() % 3); } }
    for (int c = 0; c < 24; c++) {
      head[c] = (int16_t)(head[c] + spd[c] * 0.1f * 3); if (head[c] > 30) { head[c] = (int16_t)-(int)(sRand() % 10); spd[c] = (uint8_t)(1 + sRand() % 3); }
      for (int k = 0; k < 10; k++) { int row = head[c] - k; if (row < 0 || row > 23) continue; char g[2] = {(char)('0' + (sRand() % 10)), 0}; uint8_t v = (uint8_t)(255 - k * 24); spr.setTextColor(rgb(0, v, (uint8_t)(v / 5))); spr.setTextDatum(MC_DATUM); spr.drawString(g, 5 + c * 10, 5 + row * 10, 1); }
    }
  } else if (st == 12) {                                                    // Lissajous curve with a fading tail
    for (int i = 0; i < 160; i++) { float u = t * 0.8f - i * 0.025f; int x = 120 + (int)(100 * sinf(3 * u + 0.5f)), y = 120 + (int)(100 * sinf(2 * u)); uint8_t v = (uint8_t)(255 - i * 255 / 160); uint8_t r, g, b; hsv2rgb((uint16_t)((int)(t * 30) % 360), &r, &g, &b); spr.fillCircle(x, y, 2, rgb(r * v / 255, g * v / 255, b * v / 255)); }
  } else {                                                                  // aurora: green and violet curtains
    for (int x = 0; x < 240; x += 3) {
      float h = 60 + 30 * sinf(x * 0.04f + t * 0.6f) + 20 * sinf(x * 0.11f - t * 0.9f); int top = 120 - (int)h;
      for (int y = top; y < top + 70; y += 2) { float k = 1.0f - (y - top) / 70.0f; spr.drawFastHLine(x, y, 3, rgb((uint8_t)(80 * k * (0.5f + 0.5f * sinf(x * 0.03f + t))), (uint8_t)(255 * k), (uint8_t)(120 * k))); }
    }
  }
}

// ================================================================ text folding (idea 41): Latin-1 / Latin Extended-A / Greek / Cyrillic -> the closest ASCII, so names and translations are never rejected
static const char* const FOLD_L1[64] = {"A", "A", "A", "A", "Ae", "A", "AE", "C", "E", "E", "E", "E", "I", "I", "I", "I", "D", "N", "O", "O", "O", "O", "Oe", "x", "O", "U", "U", "U", "Ue", "Y", "Th", "ss",
                                        "a", "a", "a", "a", "ae", "a", "ae", "c", "e", "e", "e", "e", "i", "i", "i", "i", "d", "n", "o", "o", "o", "o", "oe", "/", "o", "u", "u", "u", "ue", "y", "th", "y"};
static const char* const FOLD_GR[25] = {"a", "b", "g", "d", "e", "z", "e", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s", "s", "t", "u", "f", "ch", "ps", "o"};
static const char* const FOLD_CY[64] = {"A", "B", "V", "G", "D", "E", "Zh", "Z", "I", "J", "K", "L", "M", "N", "O", "P", "R", "S", "T", "U", "F", "Kh", "Ts", "Ch", "Sh", "Shch", "", "Y", "", "E", "Yu", "Ya",
                                        "a", "b", "v", "g", "d", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"};
static String foldText(const char* s) {
  String o;
  while (*s) {
    uint8_t c = (uint8_t)*s;
    if (c < 0x80) { o += (char)c; s++; continue; }
    uint32_t cp = 0; int len = 1;
    if ((c & 0xE0) == 0xC0 && (s[1] & 0xC0) == 0x80) { cp = ((c & 0x1F) << 6) | (s[1] & 0x3F); len = 2; }
    else if ((c & 0xF0) == 0xE0 && (s[1] & 0xC0) == 0x80 && (s[2] & 0xC0) == 0x80) { cp = ((c & 0x0F) << 12) | ((s[1] & 0x3F) << 6) | (s[2] & 0x3F); len = 3; }
    else if ((c & 0xF8) == 0xF0) { len = 4; for (int i = 1; i < 4 && (s[i] & 0xC0) == 0x80; i++) { } cp = 0; }
    const char* r = nullptr;
    if (cp >= 0xC0 && cp <= 0xFF) r = FOLD_L1[cp - 0xC0];
    else if (cp >= 0x100 && cp <= 0x17F) { static const char* const X = "AaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjJjKkkLlLlLlLlLlNnNnNnnNnOoOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzs"; if (cp - 0x100 < strlen(X)) { static char one[2]; one[0] = X[cp - 0x100]; one[1] = 0; r = one; } }
    else if (cp >= 0x391 && cp <= 0x3A9) { static char b[4]; const char* g = FOLD_GR[cp - 0x391]; b[0] = (char)(g[0] >= 'a' && g[0] <= 'z' ? g[0] - 32 : g[0]); strncpy(b + 1, g + 1, 2); b[3] = 0; r = b; }
    else if (cp >= 0x3B1 && cp <= 0x3C9) r = FOLD_GR[cp - 0x3B1];
    else if (cp >= 0x410 && cp <= 0x44F) r = FOLD_CY[cp - 0x410];
    else if (cp == 0x401) r = "Yo"; else if (cp == 0x451) r = "yo";
    else if (cp == 0x20AC) r = "EUR"; else if (cp == 0x2013 || cp == 0x2014) r = "-"; else if (cp == 0x2018 || cp == 0x2019) r = "'"; else if (cp == 0x201C || cp == 0x201D) r = "\""; else if (cp == 0x2026) r = "..."; else if (cp == 0xB0) r = "deg";
    o += r ? r : "?"; s += len;
  }
  return o;
}

// ================================================================ commands of this file
static bool cmdSys(const char* cmd, JsonDocument& doc) {
  if (!strcmp(cmd, "crumbs")) {
    if (!strcmp(doc["op"] | "get", "clear")) { crPrevN = 0; rtcCr.head = 0; rtcCr.n = 0; }
    JsonDocument d; d["ok"] = true; d["evt"] = "crumbs"; d["boots"] = rtcCr.boots; d["prev_boots"] = crPrevBoots;
    crumbsJson(d["prev"].to<JsonArray>(), crPrev, crPrevN, 0, false);
    crumbsJson(d["now"].to<JsonArray>(), rtcCr.ring, rtcCr.n, rtcCr.head, rtcCr.n >= 48);
    d["tail"] = crumbsTail(6); sendDoc(d); return true;
  }
  if (!strcmp(cmd, "lifetime")) {
    const char* op = doc["op"] | "get";
    if (!strcmp(op, "reset")) { uint32_t b = lt.boots; memset(&lt, 0, sizeof lt); lt.boots = b; for (int i = 0; i < 5; i++) swPress[i] = swChatter[i] = 0; swDirty = true; swhService(true); ltDirty = true; ltSave(); }
    else if (!strcmp(op, "save")) { ltSave(); swDirty = true; swhService(true); }
    else if (strcmp(op, "get")) { nack("op"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "lifetime"; JsonArray p = d["presses"].to<JsonArray>(), c = d["chatter"].to<JsonArray>(); for (int i = 0; i < 5; i++) { p.add(swPress[i]); c.add(swChatter[i]); }
    d["dial_cw"] = lt.dialCw; d["dial_ccw"] = lt.dialCcw; d["dial_click"] = lt.dialClick; d["minutes"] = lt.minutes; d["boots"] = lt.boots; d["up_s"] = millis() / 1000; sendDoc(d); return true;
  }
  if (!strcmp(cmd, "led_events")) {
    if (!doc["events"].isNull()) {
      JsonArrayConst ev = doc["events"].as<JsonArrayConst>();
      if (ev.isNull() || ev.size() > 8) { nack("events"); return true; }
      for (JsonVariantConst v : ev) {
        const char* n = v["n"] | ""; int p = v["p"] | 1, rep = v["rep"] | 1; JsonArrayConst st = v["steps"].as<JsonArrayConst>();
        if (!*n || !apAsciiText(n, 10, false) || p < 1 || p > 9 || rep < 1 || rep > 20 || st.isNull() || st.size() < 1 || st.size() > 6) { nack("events"); return true; }
        for (JsonVariantConst s : st) { uint8_t r, g, b; int ms = s["ms"] | 0; if (!apColor(s["c"] | "", r, g, b) || ms < 20 || ms > 5000) { nack("events"); return true; } }
      }
      String js; serializeJson(ev, js); if (js.length() > 1800) { nack("too_long"); return true; }
      if (ev.size()) prefs.putString("levs", js); else prefs.remove("levs");
      levLoad(); levActive = -1; levQn = 0;
    }
    JsonDocument d; d["ok"] = true; d["evt"] = "led_events"; JsonArray a = d["names"].to<JsonArray>(); for (int i = 0; i < 8; i++) if (ledEvs[i].used) { JsonObject o = a.add<JsonObject>(); o["n"] = ledEvs[i].name; o["p"] = ledEvs[i].prio; }
    sendDoc(d); return true;
  }
  if (!strcmp(cmd, "led_event")) {
    if (doc["cancel"] | false) { levActive = -1; levQn = 0; }
    else {
      const char* n = doc["name"] | ""; int idx = -1; for (int i = 0; i < 8; i++) if (ledEvs[i].used && !strcmp(ledEvs[i].name, n)) idx = i;
      if (idx < 0) { nack("event"); return true; }
      levTrigger(idx);
    }
    JsonDocument d; d["ok"] = true; d["evt"] = "led_event"; d["active"] = levActive >= 0 ? ledEvs[levActive].name : ""; JsonArray q = d["queue"].to<JsonArray>(); for (uint8_t i = 0; i < levQn; i++) q.add(ledEvs[levQueue[i]].name); d["played"] = levPlayed;
    sendDoc(d); return true;
  }
  if (!strcmp(cmd, "pet_event")) {
    const char* k = doc["kind"] | "";
    if (!strcmp(k, "cheer")) { petCheerUntil = millis() + 4000; petSleep = false; petHap = (uint8_t)min(100, petHap + 10); petSave(); ledFlash(0, 60, 20, 300); }
    else if (!strcmp(k, "sad")) { petSadUntil = millis() + 5000; petHap = (uint8_t)(petHap > 10 ? petHap - 10 : 0); petSave(); }
    else if (*k) { nack("kind"); return true; }
    needRedraw = true;
    JsonDocument d; d["ok"] = true; d["evt"] = "pet_event"; d["cheer"] = (int32_t)(petCheerUntil - millis()) > 0; d["sad"] = (int32_t)(petSadUntil - millis()) > 0; d["sweat"] = petSweats(); d["sleep"] = petSleep; d["fun"] = petHap; sendDoc(d); return true;
  }
  if (!strcmp(cmd, "clockwords")) {
    struct tm t; localTm(t); int h = doc["h"] | t.tm_hour, m = doc["m"] | t.tm_min;
    if (h < 0 || h > 23 || m < 0 || m > 59) { nack("time"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "clockwords"; d["text"] = wcSentence(h, m); sendDoc(d); return true;
  }
  if (!strcmp(cmd, "fold")) {                                                // what the pad would show for a text: used by the app to preview names with accents, Greek, Cyrillic ...
    String f = foldText(doc["text"] | ""); JsonDocument d; d["ok"] = true; d["evt"] = "fold"; d["text"] = f; sendDoc(d); return true;
  }
  return false;
}
