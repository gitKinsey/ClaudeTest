// ================================================================================================================
//  Firmware 2.0 apps, part 2: TOTP codes (19), timers (25-27), calendar / world clock / sun and moon (28-30).  Included by apps.h.
// ================================================================================================================

// ================================================================ screen 24: TOTP codes (idea 19) - secrets live in flash, shown only after the PIN
struct TotpAcc { char name[11]; uint8_t key[32]; uint8_t kl; uint8_t digits; uint8_t period; bool used; };
static TotpAcc totpAcc[10]; static uint8_t totpSel = 0, totpN = 0;
static void totpLoad() {
  totpN = 0;
  for (int i = 0; i < 10; i++) {
    totpAcc[i].used = false; char k[6]; snprintf(k, sizeof k, "tp%d", i); String v = prefs.getString(k, "");
    if (!v.length()) continue;
    JsonDocument d; if (deserializeJson(d, v)) continue;
    strncpy(totpAcc[i].name, d["n"] | "", 10); totpAcc[i].name[10] = 0;
    int kl = base32Decode(d["s"] | "", totpAcc[i].key, 32); if (kl <= 0) continue;
    totpAcc[i].kl = (uint8_t)kl; totpAcc[i].digits = (uint8_t)(d["d"] | 6); totpAcc[i].period = (uint8_t)(d["p"] | 30); totpAcc[i].used = true; totpN++;
  }
  if (!totpAcc[totpSel].used) totpSel = 0;
}
static uint32_t totpNow() { return (uint32_t)time(nullptr); }
static String totpCode(int i, uint64_t t) {
  char f[8], b[12]; snprintf(f, sizeof f, "%%0%uu", totpAcc[i].digits); snprintf(b, sizeof b, f, (unsigned)totpAt(totpAcc[i].key, totpAcc[i].kl, t, totpAcc[i].period, totpAcc[i].digits));
  return String(b);
}
static void sceneTotp() {
  if (pinBlocks(true)) { scenePinLock(); return; }
  apFrame("2FA CODES");
  if (!totpN) { spr.setTextColor(C_GRAY); spr.drawString("NO ACCOUNTS", 120, 110, 4); spr.setTextColor(C_DIM2); spr.drawString("add them in the app", 120, 140, 2); return; }
  if (!timeSynced) { spr.setTextColor(C_WARN); spr.drawString("CLOCK NOT SET", 120, 110, 4); return; }
  const TotpAcc& a = totpAcc[totpSel]; uint32_t t = totpNow(); int rem = a.period - (int)(t % a.period);
  spr.setTextColor(C_TXT); spr.drawString(a.name, 120, 70, 4);
  String c = totpCode(totpSel, t); String sp = c.substring(0, c.length() / 2) + " " + c.substring(c.length() / 2);
  spr.setTextColor(rem <= 5 ? C_WARN : C_ACC); spr.drawString(sp.c_str(), 120, 122, 6);
  arcBand(120, 120, 118, 110, 0, 360.0f * rem / a.period, rem <= 5 ? C_WARN : C_ACC);
  char b[16]; snprintf(b, sizeof b, "%d / %d  %ds", totpSel + 1, totpN, rem); spr.setTextColor(C_DIM2); spr.drawString(b, 120, 168, 2);
  spr.drawString("CLICK TYPES THE CODE", 120, 196, 1);
}
static bool totpDial(int steps) {
  if (pinBlocks(true)) return pinDial(steps);
  if (totpN) for (int k = 0; k < 10; k++) { totpSel = (uint8_t)((((int)totpSel + (steps > 0 ? 1 : 9)) % 10)); if (totpAcc[totpSel].used) break; }
  needRedraw = true; return true;
}
static bool totpClick() {
  if (pinBlocks(true)) return pinClick();
  if (totpN && timeSynced) apType(totpCode(totpSel, totpNow()));
  return true;
}
static bool totpKey(int i) { if (pinBlocks(true)) return pinKey(i); if (i == 0) return totpDial(1); return false; }
static uint16_t totpInterval() { return 250; }

// ================================================================ screens 27: timers - metronome (25), interval timer (26), decision wheel (27)
static uint8_t tmPage = 0;
static uint16_t moBpm = 120; static bool moRun = false; static uint8_t moBeat = 0, moBpb = 4; static uint32_t moNext = 0, moPulse = 0, moTaps[4]; static uint8_t moTapN = 0;
static uint16_t ivWork = 20, ivRest = 10, ivRounds = 8; static uint8_t ivPhase = 0, ivRound = 0, ivField = 0; static uint32_t ivEnd = 0, ivPauseLeft = 0; static bool ivPaused = false;
static uint8_t dcKind = 0; static int dcResult = 0; static uint8_t dcSegs = 8; static float dcAng = 0, dcVel = 0; static uint32_t dwAt = 0; static bool dcSpin = false;
static void timersEnter() { needRedraw = true; }
static void ivBegin(uint8_t phase, uint32_t now) {
  ivPhase = phase; ivEnd = now + 1000UL * (phase == 1 ? ivWork : ivRest);
  if (phase == 1) ledFlash(0, 70, 0, 400); else if (phase == 2) ledFlash(70, 0, 0, 400); else if (phase == 3) ledFlash(0, 40, 70, 800);
  needRedraw = true;
}
static void dcFinish() {
  dcSpin = false; dcVel = 0;
  float seg = 360.0f / dcSegs; dcResult = (int)(fmodf(360.0f - fmodf(dcAng, 360.0f) + 360.0f, 360.0f) / seg) % dcSegs + 1;
  needRedraw = true;
}
static void timersTick() {
  uint32_t now = millis();
  if (moRun && (int32_t)(now - moNext) >= 0) {
    bool acc = moBeat % moBpb == 0; ledFlash(acc ? 0 : 25, acc ? 70 : 25, acc ? 70 : 25, 60);
    moPulse = now; moBeat++; moNext += 60000UL / moBpm; if ((int32_t)(now - moNext) > 0) moNext = now + 60000UL / moBpm; needRedraw = true;
  }
  if (ivPhase == 1 || ivPhase == 2) if (!ivPaused && (int32_t)(now - ivEnd) >= 0) {
    if (ivPhase == 1) { if (ivRound >= ivRounds) ivBegin(3, now); else ivBegin(2, now); }
    else { ivRound++; ivBegin(1, now); }
  }
  if (dcSpin) {
    uint32_t dt = now - dwAt; if (dt >= 20) { dwAt = now; dcAng += dcVel * dt / 1000.0f; dcVel *= powf(0.35f, dt / 1000.0f); if (fabsf(dcVel) < 6.0f) dcFinish(); needRedraw = true; }
  }
}
static uint16_t timersInterval() { return (moRun || ivPhase == 1 || ivPhase == 2 || dcSpin) ? 40 : 1000; }
static void sceneTimers() {
  uint32_t now = millis();
  static const char* const T[3] = {"METRONOME", "INTERVAL", "DECIDE"};
  apFrame(T[tmPage]);
  char b[24];
  if (tmPage == 0) {
    float p = moRun ? constrain(1.0f - (now - moPulse) / 220.0f, 0.0f, 1.0f) : 0;
    arcBand(120, 120, 118, 108 - 0, 0, 360, C_DIM);
    if (p > 0) arcBand(120, 120, 118, 100, 0, 360, (moBeat - 1) % moBpb == 0 ? C_ACC : C_ACC2);
    snprintf(b, sizeof b, "%u", moBpm); spr.setTextColor(C_TXT); spr.drawString(b, 120, 108, 6);
    spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "BPM   %u/4", moBpb); spr.drawString(b, 120, 150, 2);
    spr.setTextColor(C_DIM2); spr.drawString(moRun ? "K1 STOP K2 TAP K3 BEATS" : "K1 START K2 TAP K3 BEATS", 120, 190, 1);
  } else if (tmPage == 1) {
    uint16_t col = ivPhase == 1 ? C_OK : ivPhase == 2 ? C_RED : ivPhase == 3 ? C_ACC : C_GRAY;
    uint32_t left = ivPhase == 1 || ivPhase == 2 ? (ivPaused ? ivPauseLeft : (uint32_t)max<int32_t>(0, (int32_t)(ivEnd - now))) : 0;
    if (ivPhase == 1 || ivPhase == 2) { uint32_t tot = 1000UL * (ivPhase == 1 ? ivWork : ivRest); arcBand(120, 120, 118, 106, 0, 360.0f * left / tot, col); }
    spr.setTextColor(col); spr.drawString(ivPhase == 0 ? "READY" : ivPhase == 1 ? "WORK" : ivPhase == 2 ? "REST" : "DONE", 120, 70, 4);
    if (ivPhase == 1 || ivPhase == 2) { snprintf(b, sizeof b, "%lu", (unsigned long)(left / 1000 + 1)); spr.setTextColor(C_TXT); spr.drawString(b, 120, 120, 6); snprintf(b, sizeof b, "ROUND %u/%u", ivRound + (ivPhase == 1 ? 1 : 0), ivRounds); spr.setTextColor(C_GRAY); spr.drawString(b, 120, 160, 2); }
    else { snprintf(b, sizeof b, "%us / %us x %u", ivWork, ivRest, ivRounds); spr.setTextColor(C_TXT); spr.drawString(b, 120, 120, 2);
      spr.setTextColor(C_ACC); spr.drawString(ivField == 0 ? "EDIT: WORK" : ivField == 1 ? "EDIT: REST" : "EDIT: ROUNDS", 120, 150, 2); }
    spr.setTextColor(C_DIM2); spr.drawString("K1 GO K2 PAUSE K3 FIELD", 120, 190, 1);
  } else {
    if (dcKind == 2) {
      for (int i = 0; i < dcSegs; i++) { float a0 = dcAng + i * 360.0f / dcSegs; float a1 = a0 + 360.0f / dcSegs - 1; uint8_t r, g, bb; hsv2rgb((uint16_t)(i * 360 / dcSegs), &r, &g, &bb); arcBand(120, 120, 100, 50, fmodf(a0, 360), fmodf(a0, 360) + (a1 - a0), rgb(r / 2, g / 2, bb / 2)); }
      spr.fillTriangle(120, 14, 110, 4, 130, 4, C_TXT);
    }
    snprintf(b, sizeof b, "%s", dcKind == 0 ? (dcResult == 1 ? "HEADS" : dcResult == 2 ? "TAILS" : "COIN") : dcKind == 1 ? "D20" : "SPIN");
    spr.setTextColor(C_GRAY); spr.drawString(b, 120, dcKind == 2 ? 120 : 80, 2);
    if (dcResult && dcKind != 0) { snprintf(b, sizeof b, "%d", dcResult); spr.setTextColor(C_ACC); spr.drawString(b, 120, dcKind == 2 ? 140 : 124, dcKind == 2 ? 4 : 6); }
    spr.setTextColor(C_DIM2); spr.drawString("K1 COIN K2 D20 K3 WHEEL K4 SEGS", 120, 196, 1);
  }
  spr.setTextColor(C_DIM2); spr.drawString("K5 NEXT", 120, 212, 1);
}
static bool timersKey(int i) {
  if (i == 4) { tmPage = (uint8_t)((tmPage + 1) % 3); needRedraw = true; return true; }
  uint32_t now = millis();
  if (tmPage == 0) {
    if (i == 0) { moRun = !moRun; moBeat = 0; moNext = now; }
    else if (i == 1) {
      if (moTapN && now - moTaps[moTapN - 1] > 2000) moTapN = 0;
      if (moTapN < 4) moTaps[moTapN++] = now; else { for (int k = 0; k < 3; k++) moTaps[k] = moTaps[k + 1]; moTaps[3] = now; }
      if (moTapN >= 2) { uint32_t avg = (moTaps[moTapN - 1] - moTaps[0]) / (moTapN - 1); if (avg) moBpm = (uint16_t)constrain(60000UL / avg, 30UL, 240UL); }
    }
    else if (i == 2) moBpb = (uint8_t)(moBpb >= 7 ? 2 : moBpb + 1);
    else return false;
  } else if (tmPage == 1) {
    if (i == 0) { if (ivPhase == 1 || ivPhase == 2 || ivPhase == 3) { ivPhase = 0; ivPaused = false; } else { ivRound = 1; ivPaused = false; ivBegin(1, now); } }
    else if (i == 1) { if (ivPhase == 1 || ivPhase == 2) { if (!ivPaused) { ivPaused = true; ivPauseLeft = (uint32_t)max<int32_t>(0, (int32_t)(ivEnd - now)); } else { ivPaused = false; ivEnd = now + ivPauseLeft; } } }
    else if (i == 2) ivField = (uint8_t)((ivField + 1) % 3);
    else return false;
  } else {
    if (i == 0) { dcKind = 0; dcResult = 1 + (int)(apRand() & 1); }
    else if (i == 1) { dcKind = 1; dcResult = 1 + (int)(apRand() % 20); }
    else if (i == 2) { dcKind = 2; dcResult = 0; }
    else if (i == 3) { dcSegs = (uint8_t)(dcSegs >= 12 ? 3 : dcSegs + 1); }
    else return false;
  }
  needRedraw = true; return true;
}
static bool timersDial(int steps) {
  if (tmPage == 0) moBpm = (uint16_t)constrain((int)moBpm + steps, 30, 240);
  else if (tmPage == 1) { if (ivPhase == 0 || ivPhase == 3) { if (ivField == 0) ivWork = (uint16_t)constrain((int)ivWork + steps * 5, 5, 600); else if (ivField == 1) ivRest = (uint16_t)constrain((int)ivRest + steps * 5, 0, 600); else ivRounds = (uint16_t)constrain((int)ivRounds + steps, 1, 99); } }
  else if (dcKind == 2) { dcVel += steps * 220.0f; if (fabsf(dcVel) > 1800) dcVel = dcVel > 0 ? 1800 : -1800; if (!dcSpin) { dcSpin = true; dwAt = millis(); dcResult = 0; } }
  needRedraw = true; return true;
}
static bool timersClick() {
  if (tmPage == 0) return timersKey(0);
  if (tmPage == 1) return timersKey(0);
  if (dcKind == 2) { dcVel = 700 + (apRand() % 600); dcSpin = true; dwAt = millis(); dcResult = 0; }
  else timersKey(dcKind == 0 ? 0 : 1);
  needRedraw = true; return true;
}

// ================================================================ screen 28: calendar (idea 28), world clock (29), sun and moon (30)
struct CalMonth { int16_t y; int8_t m; uint32_t mask; };
static CalMonth calMon[3]; static uint8_t calPage = 0; static int8_t calOff = 0;
struct WZone { char n[7]; int16_t off; bool used; };
static WZone wz[4]; static float geoLat = 0, geoLon = 0; static bool geoSet = false;
static int32_t daysFromCivil(int y, unsigned m, unsigned d) { y -= m <= 2; int era = (y >= 0 ? y : y - 399) / 400; unsigned yoe = (unsigned)(y - era * 400); unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1; unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; return era * 146097 + (int)doe - 719468; }
static int daysInMonth(int y, int m) { static const uint8_t D[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}; return m == 2 && ((y % 4 == 0 && y % 100 != 0) || y % 400 == 0) ? 29 : D[m - 1]; }
static void calLoad() {
  for (int i = 0; i < 3; i++) { calMon[i].y = 0; calMon[i].m = 0; calMon[i].mask = 0; }
  String v = prefs.getString("calev", ""); JsonDocument d;
  if (v.length() && !deserializeJson(d, v)) { int i = 0; for (JsonVariantConst x : d.as<JsonArrayConst>()) { if (i >= 3) break; calMon[i].y = (int16_t)(x["y"] | 0); calMon[i].m = (int8_t)(x["m"] | 0); calMon[i].mask = x["k"] | 0u; i++; } }
  v = prefs.getString("wzones", ""); for (int i = 0; i < 4; i++) wz[i].used = false;
  if (v.length() && !deserializeJson(d, v)) { int i = 0; for (JsonVariantConst x : d.as<JsonArrayConst>()) { if (i >= 4) break; strncpy(wz[i].n, x["n"] | "", 6); wz[i].n[6] = 0; wz[i].off = (int16_t)(x["o"] | 0); wz[i].used = true; i++; } }
  geoSet = prefs.isKey("geo"); if (geoSet) { geoLat = prefs.getInt("geo", 0) / 100.0f; geoLon = prefs.getInt("geo2", 0) / 100.0f; }
}
static bool sunTimes(int y, int m, int d, double lat, double lon, double& rise, double& set) {   // unix seconds of sunrise / sunset on that UTC date (NOAA sunrise equation); false = polar day / night
  double n = (double)(daysFromCivil(y, (unsigned)m, (unsigned)d)) + 2440587.5 + 0.5 - 2451545.0 + 0.0008;
  n = floor(n);
  double js = n - lon / 360.0;
  double M = fmod(357.5291 + 0.98560028 * js, 360.0); double Mr = M * DEG_TO_RAD;
  double C = 1.9148 * sin(Mr) + 0.02 * sin(2 * Mr) + 0.0003 * sin(3 * Mr);
  double lam = fmod(M + C + 180.0 + 102.9372, 360.0) * DEG_TO_RAD;
  double jt = 2451545.0 + js + 0.0053 * sin(Mr) - 0.0069 * sin(2 * lam);
  double sd = sin(lam) * sin(23.4397 * DEG_TO_RAD), cd = cos(asin(sd));
  double cw = (sin(-0.833 * DEG_TO_RAD) - sin(lat * DEG_TO_RAD) * sd) / (cos(lat * DEG_TO_RAD) * cd);
  if (cw < -1 || cw > 1) return false;
  double w = acos(cw) * 180.0 / M_PI;
  rise = (jt - w / 360.0 - 2440587.5) * 86400.0; set = (jt + w / 360.0 - 2440587.5) * 86400.0; return true;
}
static float moonAge(double unixSec) { double jd = unixSec / 86400.0 + 2440587.5; double a = fmod(jd - 2451550.1, 29.530588853); if (a < 0) a += 29.530588853; return (float)a; }
static void sceneCal() {
  apFrame(calPage == 0 ? "CALENDAR" : calPage == 1 ? "WORLD CLOCK" : "SUN & MOON");
  if (!timeSynced) { spr.setTextColor(C_WARN); spr.drawString("CLOCK NOT SET", 120, 112, 4); spr.setTextColor(C_DIM2); spr.drawString("K5 NEXT", 120, 212, 1); return; }
  time_t nowT = time(nullptr); struct tm lt; localTm(lt); char b[32];
  if (calPage == 0) {
    int y = lt.tm_year + 1900, m = lt.tm_mon + 1 + calOff; while (m > 12) { m -= 12; y++; } while (m < 1) { m += 12; y--; }
    static const char* const MN[12] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};
    snprintf(b, sizeof b, "%s %d", MN[m - 1], y); spr.setTextColor(C_TXT); spr.drawString(b, 120, 46, 2);
    int first = (int)(((daysFromCivil(y, (unsigned)m, 1) % 7) + 10) % 7);       // 0 = Monday (1970-01-01 was a Thursday)
    uint32_t mask = 0; for (int i = 0; i < 3; i++) if (calMon[i].y == y && calMon[i].m == m) mask = calMon[i].mask;
    static const char* const WD = "MTWTFSS"; spr.setTextColor(C_DIM2); for (int c = 0; c < 7; c++) { char w[2] = {WD[c], 0}; spr.drawString(w, 50 + c * 23, 64, 1); }
    for (int d = 1; d <= daysInMonth(y, m); d++) {
      int idx = first + d - 1, cx = 50 + (idx % 7) * 23, cy = 82 + (idx / 7) * 21;
      bool today = d == lt.tm_mday && m == lt.tm_mon + 1 && y == lt.tm_year + 1900;
      if (today) spr.fillCircle(cx, cy, 10, C_ACC);
      snprintf(b, sizeof b, "%d", d); spr.setTextColor(today ? C_BG : C_TXT); spr.drawString(b, cx, cy, 1);
      if ((mask >> (d - 1)) & 1) spr.fillCircle(cx, cy + 9, 2, today ? C_BG : C_ACC2);
    }
  } else if (calPage == 1) {
    int n = 0;
    for (int i = 0; i < 4; i++) {
      if (!wz[i].used) continue;
      time_t t = nowT + (time_t)wz[i].off * 60; struct tm z; gmtime_r(&t, &z);
      int y = 66 + n * 30; spr.setTextDatum(ML_DATUM); spr.setTextColor(C_GRAY); spr.drawString(wz[i].n, 46, y, 2);
      spr.setTextDatum(MR_DATUM); snprintf(b, sizeof b, "%02d:%02d", z.tm_hour, z.tm_min); spr.setTextColor(C_TXT); spr.drawString(b, 194, y, 4); n++;
    }
    spr.setTextDatum(MC_DATUM);
    if (!n) { spr.setTextColor(C_GRAY); spr.drawString("NO ZONES", 120, 112, 4); spr.setTextColor(C_DIM2); spr.drawString("add them in the app", 120, 140, 2); }
  } else {
    if (!geoSet) { spr.setTextColor(C_GRAY); spr.drawString("NO LOCATION", 120, 112, 4); spr.setTextColor(C_DIM2); spr.drawString("set it in the app", 120, 140, 2); }
    else {
      double rs, ss; time_t day0 = nowT - (nowT % 86400); struct tm u; gmtime_r(&nowT, &u);
      if (sunTimes(u.tm_year + 1900, u.tm_mon + 1, u.tm_mday, geoLat, geoLon, rs, ss)) {
        float f = constrain((float)((nowT - rs) / (ss - rs)), 0.0f, 1.0f);
        for (int i = 0; i <= 36; i++) { float a = M_PI * i / 36.0f; spr.drawPixel(120 - lroundf(80 * cosf(a)), 130 - lroundf(70 * sinf(a)), C_DIM2); }
        float a = M_PI * f; spr.fillCircle(120 - lroundf(80 * cosf(a)), 130 - lroundf(70 * sinf(a)), 8, nowT >= rs && nowT <= ss ? C_WARN : C_DIM2);
        spr.drawFastHLine(30, 130, 180, C_GRAY);
        time_t r = (time_t)rs + (time_t)tzOff, s2 = (time_t)ss + (time_t)tzOff; struct tm rt, st; gmtime_r(&r, &rt); gmtime_r(&s2, &st);
        snprintf(b, sizeof b, "%02d:%02d", rt.tm_hour, rt.tm_min); spr.setTextColor(C_TXT); spr.drawString(b, 56, 146, 2);
        snprintf(b, sizeof b, "%02d:%02d", st.tm_hour, st.tm_min); spr.drawString(b, 184, 146, 2);
      } else { spr.setTextColor(C_GRAY); spr.drawString("NO SUNRISE TODAY", 120, 100, 2); }
      float age = moonAge((double)nowT), ill = (1.0f - cosf(2 * M_PI * age / 29.530588853f)) / 2.0f;
      spr.fillCircle(120, 182, 16, C_DIM2); int sh = lroundf((1.0f - ill) * 32); if (age < 14.77f) spr.fillCircle(120 - 16 + sh / 2, 182, 16 - sh / 3, C_BG); else spr.fillCircle(120 + 16 - sh / 2, 182, 16 - sh / 3, C_BG);
      snprintf(b, sizeof b, "%d%%", (int)lroundf(ill * 100)); spr.setTextColor(C_GRAY); spr.drawString(b, 168, 182, 2);
    }
  }
  spr.setTextColor(C_DIM2); spr.drawString("K5 NEXT", 120, 214, 1);
}
static bool calKey(int i) { if (i == 4) { calPage = (uint8_t)((calPage + 1) % 3); needRedraw = true; return true; } if (i == 0 && calPage == 0) { calOff = 0; needRedraw = true; return true; } return false; }
static bool calDial(int s) { if (calPage == 0) { calOff = (int8_t)constrain((int)calOff + s, -24, 24); needRedraw = true; } return true; }
static bool calClick() { if (calPage == 0) { calOff = 0; needRedraw = true; } return true; }
static uint16_t calInterval() { return 30000; }

#include "apps_games.h"
