// ================================================================================================================
//  Firmware 2.0: on-pad apps (screens 21-32) and the helpers behind them.  Included by DeskCompanion.ino right before setup(),
//  so everything in the sketch above is visible here; the hooks in the sketch reach these functions through forward declarations.
//  One table (APPS) describes every screen; the sketch only calls appScene / appKey / appDial / appClick / appTick / appInterval / appEnter.
// ================================================================================================================
static const uint8_t APP_FIRST = 21;
struct AppDef {
  const char* name; void (*scene)(); bool (*key)(int); bool (*dial)(int); bool (*click)(); void (*tick)(); uint16_t (*interval)(); void (*enter)();
};

// ---- small shared helpers -------------------------------------------------------------------------------------
static uint32_t apRng = 0x2545F491u;
static uint32_t apRand() { apRng ^= apRng << 13; apRng ^= apRng >> 17; apRng ^= apRng << 5; return apRng; }
static void apFrame(const char* title) {
  spr.fillSprite(C_BG);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY); spr.drawString(title, 120, 26, 2);
}
static void apType(const String& s, bool enter = false) {        // type text over USB HID through the macro engine (one step, so it is never stacked)
  if (!s.length() && !enter) return;
  std::vector<Step> st; Step t; t.t = ST_TEXT; t.text = s; if (enter) t.text += '\n';
  st.push_back(t); macroStart(st);
}
static bool apHex(const char* h, uint8_t* out, size_t n) {
  for (size_t i = 0; i < n; i++) {
    int v = 0;
    for (int k = 0; k < 2; k++) { char c = h[i * 2 + k]; int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1; if (d < 0) return false; v = v * 16 + d; }
    out[i] = (uint8_t)v;
  }
  return true;
}
static String apToHex(const uint8_t* p, size_t n) { static const char* H = "0123456789abcdef"; String s; for (size_t i = 0; i < n; i++) { s += H[p[i] >> 4]; s += H[p[i] & 15]; } return s; }
static void apIconDraw(int x, int y, const uint8_t* bits, uint16_t col) {   // 24x24 mono, 3 bytes per row, MSB first
  for (int r = 0; r < 24; r++) for (int c = 0; c < 24; c++) if (bits[r * 3 + c / 8] & (0x80 >> (c & 7))) spr.drawPixel(x + c, y + r, col);
}
static bool apIconLoad(const char* key, uint8_t* out) { return prefs.isKey(key) && prefs.getBytes(key, out, 72) == 72; }
static void apIconKey(uint8_t lay, uint8_t k, char* out) { snprintf(out, 6, "i%u%u", lay, k); }
static void apEvt(JsonDocument& d) { if (!hostActive()) return; int32_t keep = reqId; reqId = -1; sendDoc(d); reqId = keep; }

// ================================================================ SHA-1 / HMAC / base32 / TOTP (no library; checked against RFC 6238 in the native tests)
struct Sha1 {
  uint32_t h[5]; uint64_t len; uint8_t buf[64]; uint8_t n;
  static uint32_t rol(uint32_t v, int s) { return (v << s) | (v >> (32 - s)); }
  void init() { h[0] = 0x67452301; h[1] = 0xEFCDAB89; h[2] = 0x98BADCFE; h[3] = 0x10325476; h[4] = 0xC3D2E1F0; len = 0; n = 0; }
  void block(const uint8_t* p) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++) w[i] = ((uint32_t)p[i * 4] << 24) | ((uint32_t)p[i * 4 + 1] << 16) | ((uint32_t)p[i * 4 + 2] << 8) | p[i * 4 + 3];
    for (int i = 16; i < 80; i++) w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4];
    for (int i = 0; i < 80; i++) {
      uint32_t f, k;
      if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; } else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
      else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; } else { f = b ^ c ^ d; k = 0xCA62C1D6; }
      uint32_t t = rol(a, 5) + f + e + k + w[i]; e = d; d = c; c = rol(b, 30); b = a; a = t;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e;
  }
  void add(const uint8_t* p, size_t l) { len += l; while (l--) { buf[n++] = *p++; if (n == 64) { block(buf); n = 0; } } }
  void done(uint8_t* out) {
    uint64_t bits = len * 8; uint8_t pad = 0x80; add(&pad, 1); pad = 0; while (n != 56) add(&pad, 1);
    uint8_t lb[8]; for (int i = 0; i < 8; i++) lb[i] = (uint8_t)(bits >> (56 - 8 * i)); add(lb, 8);
    for (int i = 0; i < 5; i++) { out[i * 4] = (uint8_t)(h[i] >> 24); out[i * 4 + 1] = (uint8_t)(h[i] >> 16); out[i * 4 + 2] = (uint8_t)(h[i] >> 8); out[i * 4 + 3] = (uint8_t)h[i]; }
  }
};
static void hmacSha1(const uint8_t* key, size_t kl, const uint8_t* msg, size_t ml, uint8_t* out) {
  uint8_t k[64] = {0}, ip[64], op[64], inner[20];
  if (kl > 64) { Sha1 s; s.init(); s.add(key, kl); s.done(k); } else memcpy(k, key, kl);
  for (int i = 0; i < 64; i++) { ip[i] = k[i] ^ 0x36; op[i] = k[i] ^ 0x5c; }
  Sha1 a; a.init(); a.add(ip, 64); a.add(msg, ml); a.done(inner);
  Sha1 b; b.init(); b.add(op, 64); b.add(inner, 20); b.done(out);
}
static int base32Decode(const char* s, uint8_t* out, int maxn) {  // RFC 4648, case-insensitive, spaces / '=' / '-' ignored; -1 on a bad character
  int bits = 0, acc = 0, n = 0;
  for (; *s; s++) {
    char c = *s; if (c == ' ' || c == '=' || c == '-') continue;
    int v = c >= 'A' && c <= 'Z' ? c - 'A' : c >= 'a' && c <= 'z' ? c - 'a' : c >= '2' && c <= '7' ? c - '2' + 26 : -1;
    if (v < 0) return -1;
    acc = (acc << 5) | v; bits += 5;
    if (bits >= 8) { if (n >= maxn) return -1; out[n++] = (uint8_t)((acc >> (bits - 8)) & 255); bits -= 8; }
  }
  return n;
}
static uint32_t totpAt(const uint8_t* key, int kl, uint64_t unixSec, uint32_t period, uint8_t digits) {
  uint64_t ctr = unixSec / period; uint8_t m[8], h[20];
  for (int i = 0; i < 8; i++) m[i] = (uint8_t)(ctr >> (56 - 8 * i));
  hmacSha1(key, kl, m, 8, h);
  int o = h[19] & 15;
  uint32_t bin = ((uint32_t)(h[o] & 0x7f) << 24) | ((uint32_t)h[o + 1] << 16) | ((uint32_t)h[o + 2] << 8) | h[o + 3];
  uint32_t mod = 1; for (uint8_t i = 0; i < digits; i++) mod *= 10;
  return bin % mod;
}

// ================================================================ PIN lock (idea 20): a salted hash in flash, entered with the dial; 5 wrong tries = a growing lock-out
static uint16_t pinIdleS = 0;                                               // auto-lock after this many idle seconds (0 = never)
static uint8_t  pinBuf[8], pinN = 0, pinCur = 0, pinFails = 0;
static uint32_t pinLockoutUntil = 0;
static uint32_t apPinHash(const char* pin, uint32_t salt) {
  Sha1 s; s.init(); s.add((const uint8_t*)&salt, 4); s.add((const uint8_t*)pin, strlen(pin)); uint8_t o[20]; s.done(o);
  return ((uint32_t)o[0] << 24) | ((uint32_t)o[1] << 16) | ((uint32_t)o[2] << 8) | o[3];
}
static bool apPinCheck(const char* pin) { return pinSet && apPinHash(pin, prefs.getUInt("pins", 0)) == prefs.getUInt("pinh", 1); }
static void apPinLock() { if (!pinSet) return; pinLocked = true; pinN = 0; pinCur = 0; needRedraw = true; }
static void apPinEvt(const char* what) { JsonDocument d; d["evt"] = "pin"; d["what"] = what; d["locked"] = pinLocked; apEvt(d); }
static bool apPinTry() {                                                   // the digits typed on the dial
  char b[9]; for (int i = 0; i < pinN; i++) b[i] = (char)('0' + pinBuf[i]); b[pinN] = 0; pinN = 0; pinCur = 0;
  if ((int32_t)(pinLockoutUntil - millis()) > 0) { showToast("WAIT"); return false; }
  if (apPinCheck(b)) { pinLocked = false; pinFails = 0; needRedraw = true; apPinEvt("unlocked"); return true; }
  if (++pinFails >= 5) { pinLockoutUntil = millis() + 30000UL * (pinFails - 4); showToast("LOCKED OUT"); } else showToast("WRONG PIN");
  apPinEvt("wrong"); needRedraw = true; return false;
}
static bool pinBlocks(bool secretScreen) { return pinLocked && (pinAll || secretScreen); }
static void scenePinLock() {
  apFrame("PIN");
  char b[12]; for (int i = 0; i < 8; i++) b[i] = i < pinN ? '*' : '-'; b[8] = 0;
  spr.setTextColor(C_TXT); spr.drawString(b, 120, 78, 4);
  spr.setTextColor(C_ACC); char d[3]; snprintf(d, sizeof d, "%u", pinCur); spr.drawString(d, 120, 128, 6);
  spr.setTextColor(C_DIM2); spr.drawString("DIAL = DIGIT  CLICK = ADD", 120, 176, 1); spr.drawString("K4 DEL  K5 ENTER", 120, 190, 1);
  if ((int32_t)(pinLockoutUntil - millis()) > 0) { spr.setTextColor(C_RED); snprintf(b, sizeof b, "WAIT %us", (unsigned)((pinLockoutUntil - millis()) / 1000 + 1)); spr.drawString(b, 120, 210, 2); }
}
static bool pinKey(int i) {                                                // keys while the PIN screen is up
  if (i == 3 && pinN) { pinN--; needRedraw = true; }
  else if (i == 4) apPinTry();
  needRedraw = true; return true;
}
static bool pinDial(int steps) { pinCur = (uint8_t)((((int)pinCur + steps) % 10 + 10) % 10); needRedraw = true; return true; }
static bool pinClick() { if (pinN < 8) pinBuf[pinN++] = pinCur; needRedraw = true; return true; }

// ================================================================ screen 21: snippets (idea 15) - up to 100 snippets, dial scrolls, click types
static const int SNIP_MAX = 100;
static int snipN = 0, snipSel = 0;
static void snipKey(int i, char* out) { snprintf(out, 8, "sn%d", i); }
static bool snipGet(int i, String& label, String& text) {
  char k[8]; snipKey(i, k); String v = prefs.getString(k, "");
  int t = v.indexOf('\t'); if (t < 0) return false;
  label = v.substring(0, t); text = v.substring(t + 1); return true;
}
static void snipLoadCount() { snipN = prefs.getUShort("snn", 0); if (snipN > SNIP_MAX) snipN = 0; if (snipSel >= snipN) snipSel = 0; }
static void sceneSnips() {
  apFrame("SNIPPETS");
  if (pinBlocks(true)) { spr.setTextColor(C_WARN); spr.drawString("LOCKED", 120, 110, 4); return; }
  if (!snipN) { spr.setTextColor(C_GRAY); spr.drawString("NO SNIPPETS", 120, 100, 4); spr.setTextColor(C_DIM2); spr.drawString("add them in the app", 120, 130, 2); return; }
  char b[16]; snprintf(b, sizeof b, "%d / %d", snipSel + 1, snipN); spr.setTextColor(C_DIM2); spr.drawString(b, 120, 46, 2);
  for (int r = -1; r <= 1; r++) {
    int idx = snipSel + r; if (idx < 0 || idx >= snipN) continue;
    String l, t; if (!snipGet(idx, l, t)) continue;
    int y = 110 + r * 34;
    if (r == 0) { spr.fillRoundRect(20, y - 15, 200, 30, 10, C_ACC); spr.setTextColor(C_BG); } else spr.setTextColor(C_GRAY);
    spr.drawString(l.c_str(), 120, y, 2);
  }
  String l, t; if (snipGet(snipSel, l, t)) { t.replace('\n', ' '); if (t.length() > 24) t = t.substring(0, 23) + "~"; spr.setTextColor(C_DIM2); spr.drawString(t.c_str(), 120, 176, 1); }
  spr.setTextColor(C_DIM2); spr.drawString("CLICK TYPES   K3 +ENTER", 120, 200, 1);
}
static bool snipTypeSel(bool enter) { String l, t; if (pinBlocks(true) || !snipGet(snipSel, l, t)) return false; apType(t, enter); return true; }
static bool snipDial(int steps) { if (snipN) { snipSel = (int)((((long)snipSel + steps) % snipN + snipN) % snipN); needRedraw = true; } return true; }
static bool snipClick() { snipTypeSel(false); return true; }
static bool snipKeyHandler(int i) {
  if (i == 0) snipDial(-10); else if (i == 1) snipDial(10); else if (i == 2) snipTypeSel(true); else if (i == 3) { snipSel = 0; needRedraw = true; } else return false;
  return true;
}

// ================================================================ screen 22: radial launcher (idea 16) - 8 slots on a ring
struct RadSlot { char l[9]; String spec; bool used = false; };
static RadSlot radSlots[8]; static uint8_t radSel = 0;
static void radLoad() {
  for (int i = 0; i < 8; i++) {
    radSlots[i].used = false; radSlots[i].l[0] = 0; radSlots[i].spec = "";
    char k[6]; snprintf(k, sizeof k, "rl%d", i); String v = prefs.getString(k, "");
    if (!v.length()) continue;
    JsonDocument d; if (deserializeJson(d, v)) continue;
    strncpy(radSlots[i].l, d["l"] | "", 8); radSlots[i].l[8] = 0;
    serializeJson(d["a"], radSlots[i].spec); radSlots[i].used = true;
  }
}
static void sceneRadial() {
  spr.fillSprite(C_BG);
  for (int i = 0; i < 8; i++) {
    float c = i * 45.0f; bool sel = i == radSel;
    arcBand(120, 120, 118, 70, c - 21, c + 21, sel ? C_ACC : radSlots[i].used ? C_DIM2 : C_DIM);
    float a = c * DEG_TO_RAD; int x = 120 + lroundf(94 * sinf(a)), y = 120 - lroundf(94 * cosf(a));
    char ik[6]; snprintf(ik, sizeof ik, "ri%d", i); uint8_t ic[72];
    if (apIconLoad(ik, ic)) apIconDraw(x - 12, y - 12, ic, sel ? C_BG : C_TXT);
    else if (radSlots[i].used) { spr.setTextDatum(MC_DATUM); spr.setTextColor(sel ? C_BG : C_TXT); spr.drawString(radSlots[i].l, x, y, 1); }
  }
  spr.fillCircle(120, 120, 62, C_BG); spr.drawCircle(120, 120, 62, C_DIM2);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(pinBlocks(true) ? C_WARN : C_TXT);
  spr.drawString(pinBlocks(true) ? "LOCKED" : radSlots[radSel].used ? radSlots[radSel].l : "EMPTY", 120, 120, 2);
}
static bool radDial(int steps) { radSel = (uint8_t)((((int)radSel + steps) % 8 + 8) % 8); needRedraw = true; return true; }
static bool radClick() { if (radSlots[radSel].used && !pinBlocks(true)) runSpecJson(radSlots[radSel].spec, nullptr, LAYERS * 15 * 8 - 2); return true; }
static bool radKey(int i) { if (i >= 5) return false; radSel = (uint8_t)(i < 4 ? i * 2 : 7); needRedraw = true; return true; }   // K1..K5 jump to slots 1, 3, 5, 7, 8

// ================================================================ screen 23: typer (ideas 17 / 18 / 38) - dial keyboard, number / PIN typer, Morse
static uint8_t tyPage = 0;                                                  // 0 keyboard, 1 digits, 2 morse
static String tyBuf; static int tySel = 0; static bool tyShift = false;
static const char TY_CHARS[] = "abcdefghijklmnopqrstuvwxyz0123456789 .,-_@!?/:;+=#$%&*()";
static String moBuf, moCode; static uint32_t moLastAt = 0;
static const char* const MORSE[] = {".-", "-...", "-.-.", "-..", ".", "..-.", "--.", "....", "..", ".---", "-.-", ".-..", "--", "-.", "---", ".--.", "--.-", ".-.", "...", "-", "..-", "...-", ".--", "-..-", "-.--", "--..",
                                    "-----", ".----", "..---", "...--", "....-", ".....", "-....", "--...", "---..", "----."};
static char morseDecode(const String& c) {
  for (int i = 0; i < 36; i++) if (c == MORSE[i]) return i < 26 ? (char)('a' + i) : (char)('0' + i - 26);
  return 0;
}
static void moCommit() { if (!moCode.length()) return; char c = morseDecode(moCode); moCode = ""; moBuf += c ? c : '?'; needRedraw = true; }
static void sceneTyper() {
  static const char* const T[3] = {"DIAL KEYBOARD", "NUMBERS", "MORSE"};
  apFrame(T[tyPage]);
  if (pinBlocks(true)) { spr.setTextColor(C_WARN); spr.drawString("LOCKED", 120, 110, 4); return; }
  spr.setTextColor(C_TXT); String shown = tyPage == 2 ? moBuf : tyBuf; if (shown.length() > 16) shown = "~" + shown.substring(shown.length() - 15);
  spr.drawString(shown.c_str(), 120, 56, 2);
  if (tyPage == 0) {
    int n = (int)strlen(TY_CHARS);
    for (int r = -2; r <= 2; r++) {
      int idx = ((tySel + r) % n + n) % n; char c = TY_CHARS[idx]; if (tyShift && c >= 'a' && c <= 'z') c = (char)(c - 32);
      char b[3] = {c == ' ' ? '_' : c, 0, 0};
      if (r == 0) { spr.fillCircle(120, 122, 22, C_ACC); spr.setTextColor(C_BG); spr.drawString(b, 120, 122, 4); }
      else { spr.setTextColor(r == -1 || r == 1 ? C_GRAY : C_DIM2); spr.drawString(b, 120 + r * 44, 122, 2); }
    }
    spr.setTextColor(C_DIM2); spr.drawString("K1 DEL K2 CAPS K3 SPC K4 SEND", 120, 176, 1);
  } else if (tyPage == 1) {
    char d[3]; snprintf(d, sizeof d, "%d", tySel % 10); spr.setTextColor(C_ACC); spr.drawString(d, 120, 124, 6);
    spr.setTextColor(C_DIM2); spr.drawString("K1 DEL K3 ENTER K4 TYPE", 120, 176, 1);
  } else {
    spr.setTextColor(C_ACC); spr.drawString(moCode.length() ? moCode.c_str() : " ", 120, 124, 4);
    spr.setTextColor(C_DIM2); spr.drawString("K1 DIT K2 DAH K3 GAP K4 SEND", 120, 176, 1);
  }
  spr.setTextColor(C_DIM2); spr.drawString("K5 NEXT PAGE", 120, 196, 1);
}
static bool tyDial(int steps) {
  if (pinBlocks(true) || tyPage == 2) return true;
  int n = tyPage == 0 ? (int)strlen(TY_CHARS) : 10; tySel = (int)((((long)tySel + steps) % n + n) % n); needRedraw = true; return true;
}
static bool tyClick() {
  if (pinBlocks(true) || tyPage == 2) return true;
  if (tyBuf.length() < 60) { char c = tyPage == 0 ? TY_CHARS[tySel] : (char)('0' + tySel % 10); if (tyShift && c >= 'a' && c <= 'z') c = (char)(c - 32); tyBuf += c; }
  needRedraw = true; return true;
}
static bool tyKey(int i) {
  if (i == 4) { tyPage = (uint8_t)((tyPage + 1) % 3); tySel = 0; needRedraw = true; return true; }
  if (pinBlocks(true)) return true;
  if (tyPage == 2) {
    if (i == 0 || i == 1) { if (moCode.length() < 6) moCode += i == 0 ? '.' : '-'; moLastAt = millis(); }
    else if (i == 2) { if (moCode.length()) moCommit(); else moBuf += ' '; }
    else if (i == 3) { moCommit(); apType(moBuf); moBuf = ""; }
    needRedraw = true; return true;
  }
  if (i == 0) { if (tyBuf.length()) tyBuf.remove(tyBuf.length() - 1); }
  else if (i == 1) tyShift = !tyShift;
  else if (i == 2) { if (tyPage == 0) tyBuf += ' '; else { apType(tyBuf, true); tyBuf = ""; } }
  else if (i == 3) { apType(tyBuf); tyBuf = ""; }
  needRedraw = true; return true;
}
static void tyTick() { if (mode == 23 && tyPage == 2 && moCode.length() && (uint32_t)(millis() - moLastAt) > 900) moCommit(); }

// ================================================================ screen 25: pixel sketch (idea 23) - 40x40 grid, the saved drawing becomes a screensaver
static uint8_t skBits[200]; static uint8_t skX = 20, skY = 20; static bool skPen = false, skErase = false, skVert = false;
static bool skGet(int x, int y) { int i = y * 40 + x; return skBits[i >> 3] & (1 << (i & 7)); }
static void skSet(int x, int y, bool v) { int i = y * 40 + x; if (v) skBits[i >> 3] |= (1 << (i & 7)); else skBits[i >> 3] &= (uint8_t)~(1 << (i & 7)); }
static void skDraw(int ox, int oy, int cell, uint16_t col) { for (int y = 0; y < 40; y++) for (int x = 0; x < 40; x++) if (skGet(x, y)) spr.fillRect(ox + x * cell, oy + y * cell, cell, cell, col); }
static void sceneSketch() {
  spr.fillSprite(C_BG);
  spr.drawRect(19, 19, 202, 202, C_DIM2);
  skDraw(20, 20, 5, C_TXT);
  spr.drawRect(20 + skX * 5 - 1, 20 + skY * 5 - 1, 7, 7, skPen ? (skErase ? C_RED : C_OK) : C_ACC);
  spr.setTextDatum(MC_DATUM); spr.setTextColor(C_GRAY); spr.drawString(skVert ? "Y" : "X", 120, 8, 1);
}
static void skMove(int dx, int dy) {                              // one cell at a time, so a fast turn draws a continuous line
  int n = dx ? abs(dx) : abs(dy), sx = dx > 0 || dy > 0 ? 1 : -1;
  for (int i = 0; i < n; i++) { skX = (uint8_t)constrain((int)skX + (dx ? sx : 0), 0, 39); skY = (uint8_t)constrain((int)skY + (dy ? sx : 0), 0, 39); if (skPen) skSet(skX, skY, !skErase); }
  needRedraw = true;
}
static bool skDial(int steps) { if (skVert) skMove(0, steps); else skMove(steps, 0); return true; }
static bool skClick() { skVert = !skVert; needRedraw = true; return true; }
static bool skKey(int i) {
  if (i == 0) { skPen = !skPen; if (skPen) skSet(skX, skY, !skErase); }
  else if (i == 1) skErase = !skErase;
  else if (i == 2) memset(skBits, 0, sizeof skBits);
  else if (i == 3) prefs.putBytes("skt", skBits, sizeof skBits);
  else if (i == 4) { if (prefs.isKey("skt")) prefs.getBytes("skt", skBits, sizeof skBits); }
  needRedraw = true; return true;
}
static void skLoad() { if (prefs.isKey("skt")) prefs.getBytes("skt", skBits, sizeof skBits); }

// ================================================================ screen 26: calculator and unit converter (idea 24)
static const char* const CALC_TOK[] = {"7", "8", "9", "/", "4", "5", "6", "*", "1", "2", "3", "-", "0", ".", "=", "+", "C", "+/-", "<"};
static const int CALC_N = 19;
static int calcSel = 0; static String calcEntry = "0"; static double calcAcc = 0; static char calcOp = 0; static bool calcFresh = true, calcErr = false; static uint8_t calcPage = 0;
static const char* const UNIT_A[] = {"km", "kg", "C", "L", "cm", "m"}; static const char* const UNIT_B[] = {"mi", "lb", "F", "gal", "in", "ft"};
static uint8_t unitPair = 0; static bool unitRev = false; static double unitVal = 0;
static double unitConv(double v, uint8_t p, bool rev) {
  switch (p) {
    case 0: return rev ? v / 0.621371192 : v * 0.621371192;
    case 1: return rev ? v / 2.20462262 : v * 2.20462262;
    case 2: return rev ? (v - 32.0) * 5.0 / 9.0 : v * 9.0 / 5.0 + 32.0;
    case 3: return rev ? v / 0.264172052 : v * 0.264172052;
    case 4: return rev ? v / 0.393700787 : v * 0.393700787;
    default: return rev ? v / 3.28083989 : v * 3.28083989;
  }
}
static double calcValue() { return atof(calcEntry.c_str()); }
static String calcFmt(double v) { char b[24]; snprintf(b, sizeof b, "%.10g", v); return String(b); }
static void calcApply() {
  double b = calcValue(), r = b;
  if (calcOp == '+') r = calcAcc + b; else if (calcOp == '-') r = calcAcc - b; else if (calcOp == '*') r = calcAcc * b;
  else if (calcOp == '/') { if (b == 0) { calcErr = true; return; } r = calcAcc / b; }
  calcAcc = r; calcEntry = calcFmt(r);
}
static void calcPress(const char* t) {
  if (calcErr && strcmp(t, "C")) return;
  if ((t[0] >= '0' && t[0] <= '9') || t[0] == '.') {
    if (calcFresh) { calcEntry = t[0] == '.' ? "0." : String(t); calcFresh = false; }
    else if (t[0] == '.' ? calcEntry.indexOf('.') < 0 : true) { if (calcEntry == "0" && t[0] != '.') calcEntry = t; else if (calcEntry.length() < 12) calcEntry += t; }
  } else if (!strcmp(t, "C")) { calcEntry = "0"; calcAcc = 0; calcOp = 0; calcFresh = true; calcErr = false; }
  else if (!strcmp(t, "<")) { if (!calcFresh && calcEntry.length() > 1) calcEntry.remove(calcEntry.length() - 1); else { calcEntry = "0"; calcFresh = true; } }
  else if (!strcmp(t, "+/-")) { if (calcEntry != "0") { if (calcEntry[0] == '-') calcEntry.remove(0, 1); else calcEntry = "-" + calcEntry; } }
  else if (!strcmp(t, "=")) { if (calcOp) { calcApply(); calcOp = 0; calcFresh = true; } }
  else { if (calcOp && !calcFresh) calcApply(); else calcAcc = calcValue(); calcOp = t[0]; calcFresh = true; }
  needRedraw = true;
}
static void sceneCalc() {
  apFrame(calcPage ? "CONVERT" : "CALC");
  if (calcPage) {
    char b[40]; double out = unitConv(unitVal, unitPair, unitRev);
    spr.setTextColor(C_TXT); snprintf(b, sizeof b, "%.4g %s", unitVal, unitRev ? UNIT_B[unitPair] : UNIT_A[unitPair]); spr.drawString(b, 120, 84, 4);
    spr.setTextColor(C_GRAY); spr.drawString("=", 120, 116, 2);
    spr.setTextColor(C_ACC); snprintf(b, sizeof b, "%.5g %s", out, unitRev ? UNIT_A[unitPair] : UNIT_B[unitPair]); spr.drawString(b, 120, 148, 4);
    spr.setTextColor(C_DIM2); spr.drawString("DIAL VALUE  K1 PAIR  K2 SWAP", 120, 196, 1); return;
  }
  spr.setTextColor(calcErr ? C_RED : C_TXT); spr.drawString(calcErr ? "ERROR" : calcEntry.c_str(), 120, 56, 4);
  if (calcOp) { char o[2] = {calcOp, 0}; spr.setTextColor(C_GRAY); spr.drawString(o, 200, 56, 2); }
  for (int r = -2; r <= 2; r++) {
    int idx = ((calcSel + r) % CALC_N + CALC_N) % CALC_N;
    if (r == 0) { spr.fillRoundRect(86, 106, 68, 36, 12, C_ACC); spr.setTextColor(C_BG); spr.drawString(CALC_TOK[idx], 120, 124, 4); }
    else { spr.setTextColor(r == -1 || r == 1 ? C_GRAY : C_DIM2); spr.drawString(CALC_TOK[idx], 120 + r * 50, 124, 2); }
  }
  spr.setTextColor(C_DIM2); spr.drawString("K1 DEL K2 C K3 = K4 CONVERT", 120, 190, 1);
}
static bool calcDial(int steps) {
  if (calcPage) { double st = fabs(unitVal) >= 100 ? 10 : 1; unitVal += steps * st; needRedraw = true; return true; }
  calcSel = (calcSel + steps % CALC_N + CALC_N) % CALC_N; needRedraw = true; return true;
}
static bool calcClick() { if (!calcPage) calcPress(CALC_TOK[calcSel]); else { unitVal = 0; needRedraw = true; } return true; }
static bool calcKey(int i) {
  if (i == 3) { calcPage ^= 1; needRedraw = true; return true; }
  if (calcPage) { if (i == 0) unitPair = (uint8_t)((unitPair + 1) % 6); else if (i == 1) unitRev = !unitRev; else return false; needRedraw = true; return true; }
  if (i == 0) calcPress("<"); else if (i == 1) calcPress("C"); else if (i == 2) calcPress("="); else return false;
  return true;
}

// ================================================================ the table + the hooks the sketch calls
static void appTBD() { apFrame("COMING SOON"); }
static bool appNoKey(int) { return false; }
static bool appNoDial(int) { return false; }
static bool appNoClick() { return false; }
static void appNoTick() {}
static uint16_t appSlow() { return 1000; }
static void appNoEnter() {}
#include "apps_more.h"
static const AppDef APPS[12] = {
  {"SNIPPETS", sceneSnips, snipKeyHandler, snipDial, snipClick, appNoTick, appSlow, snipLoadCount},
  {"RADIAL", sceneRadial, radKey, radDial, radClick, appNoTick, appSlow, radLoad},
  {"TYPER", sceneTyper, tyKey, tyDial, tyClick, tyTick, appSlow, appNoEnter},
  {"TOTP", sceneTotp, totpKey, totpDial, totpClick, appNoTick, totpInterval, appNoEnter},
  {"SKETCH", sceneSketch, skKey, skDial, skClick, appNoTick, appSlow, skLoad},
  {"CALC", sceneCalc, calcKey, calcDial, calcClick, appNoTick, appSlow, appNoEnter},
  {"TIMERS", sceneTimers, timersKey, timersDial, timersClick, timersTick, timersInterval, timersEnter},
  {"CALENDAR", sceneCal, calKey, calDial, calClick, appNoTick, calInterval, appNoEnter},
  {"BLOCKS", sceneBlocks, blocksKey, blocksDial, blocksClick, blocksTick, blocksInterval, appNoEnter},
  {"PUZZLES", scenePuzzle, puzzleKey, puzzleDial, puzzleClick, puzzleTick, puzzleInterval, appNoEnter},
  {"SPACE", sceneSpace, spaceKey, spaceDial, spaceClick, spaceTick, spaceInterval, appNoEnter},
  {"STREAM", sceneStream, streamKey, streamDial, streamClick, appNoTick, streamInterval, appNoEnter},
};
static const AppDef* appCur() { return (mode >= APP_FIRST && mode < APP_FIRST + 12) ? &APPS[mode - APP_FIRST] : nullptr; }
static void appScene() { const AppDef* a = appCur(); if (a) a->scene(); else sceneGifMsg(); }
static bool appKey(int i) { const AppDef* a = appCur(); if (!a) return false; if (pinLocked && pinAll) return pinKey(i); return a->key(i); }
static bool appDial(int s) { const AppDef* a = appCur(); if (!a) return false; if (pinLocked && pinAll) return pinDial(s); return a->dial(s); }
static bool appClick() { const AppDef* a = appCur(); if (!a) return false; if (pinLocked && pinAll) return pinClick(); return a->click(); }
static void appTick() { const AppDef* a = appCur(); if (a) a->tick(); }
static uint32_t appInterval() { const AppDef* a = appCur(); return a ? a->interval() : 100000; }
static void appEnter(uint8_t m) { if (m >= APP_FIRST && m < APP_FIRST + 12) APPS[m - APP_FIRST].enter(); }
static const char* appName(uint8_t m) { return (m >= APP_FIRST && m < APP_FIRST + 12) ? APPS[m - APP_FIRST].name : ""; }
