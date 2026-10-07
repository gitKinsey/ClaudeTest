// ================================================================================================================
//  Firmware 2.0: extra USB classes (ideas 45 / 46 / 47) - MIDI controller, game controller, read-only drive.  Each one only exists when the core ships its class
//  (__has_include) and is switched on with {"cmd":"usbmode"} + a reboot (the USB descriptors cannot change while the pad is running).
// ================================================================================================================
#if DC_HAS_MIDI
static USBMIDI midiDev;
#endif
#if DC_HAS_PAD
static USBHIDGamepad padDev;
#endif
static uint8_t usbxBoot = 0;                                              // bit 0 MIDI, 1 gamepad, 2 drive: what this boot registered (saved value "usbx" applies after the next reboot)
static bool midiOn = false, padOn = false;                                // run time: the keys / dial act as a MIDI controller / game controller
static uint8_t midiBase = 36, midiCh = 1, midiCc = 20, midiVel = 100, midiVal[LAYERS] = {64, 64, 64};
static int16_t padAxis = 0; static uint8_t padStep = 8; static uint32_t padBtn = 0;
static void padSend() {
#if DC_HAS_PAD
  if (usbxBoot & 2) padDev.send((int8_t)constrain((int)padAxis, -127, 127), 0, 0, 0, 0, 0, 0, padBtn);
#else
  (void)padStep;
#endif
}
static bool usbModeActive() { return midiOn || padOn; }
static void usbModeKey(int i, bool down) {
  if (midiOn) {
#if DC_HAS_MIDI
    int note = midiBase + curLayer * 5 + i; if (note > 127) note = 127;
    if (down) midiDev.noteOn((uint8_t)note, midiVel, midiCh); else midiDev.noteOff((uint8_t)note, 0, midiCh);
#endif
  } else if (padOn) {
    uint32_t bit = 1UL << (i + curLayer * 5); if (down) padBtn |= bit; else padBtn &= ~bit; padSend();
  }
}
static bool usbModeDial(int steps) {
  if (midiOn) {
#if DC_HAS_MIDI
    int v = midiVal[curLayer] + steps; v = constrain(v, 0, 127); midiVal[curLayer] = (uint8_t)v; midiDev.controlChange((uint8_t)(midiCc + curLayer), (uint8_t)v, midiCh);
#endif
    return true;
  }
  if (padOn) { padAxis = (int16_t)constrain((int)padAxis + steps * padStep, -127, 127); padSend(); return true; }
  return false;
}
static bool usbModeClick() {
  if (midiOn) {
#if DC_HAS_MIDI
    static bool t = false; t = !t; midiDev.controlChange((uint8_t)(midiCc + 10), t ? 127 : 0, midiCh);
#endif
    return true;
  }
  if (padOn) { padAxis = 0; padBtn |= (1UL << 15); padSend(); padBtn &= ~(1UL << 15); padSend(); return true; }       // the dial button recentres the axis (and taps button 16)
  return false;
}

// ---- the read-only drive: a 128 KB FAT12 image of README.TXT, SETTINGS.JSON, KEYS.JSON and LIFETIME.JSON, built at boot / on "refresh"
static std::vector<uint8_t> driveImg;
static void put16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t* p, uint32_t v) { put16(p, (uint16_t)v); put16(p + 2, (uint16_t)(v >> 16)); }
static void fat12Set(uint8_t* fat, int n, uint16_t v) {
  int o = n * 3 / 2;
  if (n & 1) { fat[o] = (uint8_t)((fat[o] & 0x0F) | ((v << 4) & 0xF0)); fat[o + 1] = (uint8_t)(v >> 4); }
  else { fat[o] = (uint8_t)v; fat[o + 1] = (uint8_t)((fat[o + 1] & 0xF0) | ((v >> 8) & 0x0F)); }
}
static String driveKeysJson() {
  JsonDocument d; JsonArray ls = d["layers"].to<JsonArray>();
  for (uint8_t l = 0; l < LAYERS; l++) {
    JsonObject lo = ls.add<JsonObject>(); JsonObject sl = lo["slots"].to<JsonObject>(); JsonObject gs = lo["gestures"].to<JsonObject>();
    for (uint8_t i = 0; i < 15; i++) {
      char k[8]; slotKey(l, i, k); String js = prefs.getString(k, ""); char n[4]; snprintf(n, sizeof n, "%u", i + 1);
      if (js.length()) { JsonDocument sp; if (!deserializeJson(sp, js)) sl[n] = sp; }
      else if (i < 7) { JsonDocument sp; if (!deserializeJson(sp, DEFAULT_SLOT[l][i])) sl[n] = sp; }
      if (i < 5) for (const char* g = "hdtjks"; *g; g++) { char gk[10]; gestureKey(l, i, *g, gk); String gj = prefs.getString(gk, ""); if (gj.length()) { char name[8]; snprintf(name, sizeof name, "%u%c", i + 1, *g); JsonDocument sp; if (!deserializeJson(sp, gj)) gs[name] = sp; } }
    }
  }
  String s; serializeJson(d, s); return s;
}
static bool driveBuild() {
  const int SEC = 512, TOTAL = 256, FATSEC = 1, ROOTSEC = 2, DATA0 = 1 + 2 * FATSEC + ROOTSEC;
  driveImg.assign((size_t)SEC * TOTAL, 0); uint8_t* b = driveImg.data();
  b[0] = 0xEB; b[1] = 0x3C; b[2] = 0x90; memcpy(b + 3, "DESKCOMP", 8); put16(b + 11, SEC); b[13] = 1; put16(b + 14, 1); b[16] = 2; put16(b + 17, 32); put16(b + 19, TOTAL); b[21] = 0xF8; put16(b + 22, FATSEC);
  put16(b + 24, 32); put16(b + 26, 1); b[36] = 0x80; b[38] = 0x29; put32(b + 39, 0xDC200001u); memcpy(b + 43, "DESKCOMPAN ", 11); memcpy(b + 54, "FAT12   ", 8); b[510] = 0x55; b[511] = 0xAA;
  uint8_t* fat = b + SEC; fat[0] = 0xF8; fat[1] = 0xFF; fat[2] = 0xFF;
  uint8_t* root = b + SEC * (1 + 2 * FATSEC);
  JsonDocument sd; sd["fw"] = FW_VERSION; sd["proto"] = PROTO_LEVEL; sd["mode"] = mode; sd["brightness"] = brightness; sd["theme"] = themeIdx; sd["layer"] = curLayer; sd["rotation"] = rotation; sd["dial_accel"] = dialAccel; sd["clock_style"] = clockStyle;
  sd["saver_s"] = saverSec; sd["saver_style"] = saverStyle; sd["mode_mask"] = modeMask; sd["key_repeat_mask"] = repeatMask; sd["legend"] = legendMode; sd["usb_extra"] = prefs.getUChar("usbx", 0);
  String settings; serializeJson(sd, settings);
  JsonDocument ld; ld["dial_cw"] = lt.dialCw; ld["dial_ccw"] = lt.dialCcw; ld["dial_click"] = lt.dialClick; ld["minutes"] = lt.minutes; ld["boots"] = lt.boots; JsonArray pr = ld["presses"].to<JsonArray>(); for (int i = 0; i < 5; i++) pr.add(swPress[i]);
  String life; serializeJson(ld, life);
  String files[4] = {String("DeskCompanion backup drive (read-only)\r\nSETTINGS.JSON - the main settings\r\nKEYS.JSON     - every key action of the three layers\r\nLIFETIME.JSON - presses, dial turns, hours of use\r\nThe files are a snapshot taken when the pad started; eject and re-plug to refresh.\r\n"),
                     settings, driveKeysJson(), life};
  static const char* const NAMES[4][2] = {{"README  ", "TXT"}, {"SETTINGS", "JSN"}, {"KEYS    ", "JSN"}, {"LIFETIME", "JSN"}};
  int cluster = 2, maxCluster = (TOTAL - DATA0) + 1;
  memcpy(root, "DESKCOMPAN ", 11); root[11] = 0x08;
  for (int f = 0; f < 4; f++) {
    uint8_t* e = root + 32 * (f + 1); memcpy(e, NAMES[f][0], 8); memcpy(e + 8, NAMES[f][1], 3); e[11] = 0x21;
    int len = (int)files[f].length(), need = (len + SEC - 1) / SEC; if (!need) need = 1;
    if (cluster + need - 1 > maxCluster) { len = (maxCluster - cluster + 1) * SEC; need = maxCluster - cluster + 1; if (need <= 0) { len = 0; need = 0; } }   // too big for the image: cut
    put16(e + 26, (uint16_t)(need ? cluster : 0)); put32(e + 28, (uint32_t)len);
    for (int k = 0; k < need; k++) { fat12Set(fat, cluster + k, k + 1 == need ? 0xFFF : (uint16_t)(cluster + k + 1)); memcpy(b + SEC * (DATA0 + cluster - 2 + k), files[f].c_str() + k * SEC, (size_t)min(SEC, len - k * SEC)); }
    cluster += need;
  }
  memcpy(b + SEC * (1 + FATSEC), fat, SEC * FATSEC);                                                         // second FAT
  return true;
}
#if DC_HAS_MSC
static USBMSC msc;
static int32_t mscOnRead(uint32_t lba, uint32_t offset, void* buffer, uint32_t bufsize) { size_t at = (size_t)lba * 512 + offset; if (at + bufsize > driveImg.size()) return -1; memcpy(buffer, driveImg.data() + at, bufsize); return (int32_t)bufsize; }
static int32_t mscOnWrite(uint32_t, uint32_t, uint8_t*, uint32_t) { return -1; }                           // read-only: writing the pad's configuration through a drive is not supported
static bool mscOnStartStop(uint8_t, bool, bool) { return true; }
#endif
static void usbExtraBegin() {                                              // before USB.begin(): register the classes the saved setting asks for
  uint8_t want = prefs.getUChar("usbx", 0); usbxBoot = 0;
#if DC_HAS_MIDI
  if (want & 1) { midiDev.begin(); usbxBoot |= 1; }
#endif
#if DC_HAS_PAD
  if (want & 2) { padDev.begin(); usbxBoot |= 2; }
#endif
#if DC_HAS_MSC
  if ((want & 4) && driveBuild()) { msc.vendorID("DeskComp"); msc.productID("Backup"); msc.productRevision("1.0"); msc.onRead(mscOnRead); msc.onWrite(mscOnWrite); msc.onStartStop(mscOnStartStop); msc.mediaPresent(true); msc.begin(256, 512); usbxBoot |= 4; }
#endif
  midiBase = prefs.getUChar("midib", 36); midiCh = prefs.getUChar("midich", 1); midiCc = prefs.getUChar("midicc", 20); midiVel = prefs.getUChar("midiv", 100); padStep = prefs.getUChar("padst", 8);
  midiOn = (usbxBoot & 1) && prefs.getBool("midion", false); padOn = (usbxBoot & 2) && prefs.getBool("padon", false);
}
static bool cmdUsb(const char* cmd, JsonDocument& doc) {
  if (!strcmp(cmd, "usbmode")) {                                           // {"midi":bool,"gamepad":bool,"drive":bool} = classes registered at the NEXT boot; {"midi_on":bool} / {"pad_on":bool} = what the keys do right now
    uint8_t want = prefs.getUChar("usbx", 0);
    static const char* const K[3] = {"midi", "gamepad", "drive"}; bool have[3] = {DC_HAS_MIDI != 0, DC_HAS_PAD != 0, DC_HAS_MSC != 0};
    for (int i = 0; i < 3; i++) if (!doc[K[i]].isNull()) {
      if (!doc[K[i]].is<bool>()) { nack(K[i]); return true; }
      if (doc[K[i]].as<bool>() && !have[i]) { nack("not_supported"); return true; }
    }
    bool mo = !doc["midi_on"].isNull(), po = !doc["pad_on"].isNull();
    if ((mo && !doc["midi_on"].is<bool>()) || (po && !doc["pad_on"].is<bool>())) { nack("bool"); return true; }
    if ((mo && doc["midi_on"].as<bool>() && !(usbxBoot & 1)) || (po && doc["pad_on"].as<bool>() && !(usbxBoot & 2))) { nack("not_active"); return true; }
    if ((mo && doc["midi_on"].as<bool>() && ((po && doc["pad_on"].as<bool>()) || (!po && padOn))) || (po && doc["pad_on"].as<bool>() && !mo && midiOn)) { nack("exclusive"); return true; }
    for (int i = 0; i < 3; i++) if (!doc[K[i]].isNull()) { if (doc[K[i]].as<bool>()) want |= (uint8_t)(1 << i); else want &= (uint8_t)~(1 << i); }
    prefs.putUChar("usbx", want);
    if (mo) { midiOn = doc["midi_on"].as<bool>(); prefs.putBool("midion", midiOn); if (midiOn) { padOn = false; prefs.putBool("padon", false); } }
    if (po) { padOn = doc["pad_on"].as<bool>(); prefs.putBool("padon", padOn); if (padOn) { midiOn = false; prefs.putBool("midion", false); padAxis = 0; padBtn = 0; } }
    if (!doc["base"].isNull()) { int v = doc["base"] | -1; if (v < 0 || v > 120) { nack("base"); return true; } midiBase = (uint8_t)v; prefs.putUChar("midib", midiBase); }
    if (!doc["channel"].isNull()) { int v = doc["channel"] | 0; if (v < 1 || v > 16) { nack("channel"); return true; } midiCh = (uint8_t)v; prefs.putUChar("midich", midiCh); }
    if (!doc["cc"].isNull()) { int v = doc["cc"] | -1; if (v < 0 || v > 115) { nack("cc"); return true; } midiCc = (uint8_t)v; prefs.putUChar("midicc", midiCc); }
    if (!doc["velocity"].isNull()) { int v = doc["velocity"] | 0; if (v < 1 || v > 127) { nack("velocity"); return true; } midiVel = (uint8_t)v; prefs.putUChar("midiv", midiVel); }
    if (!doc["axis_step"].isNull()) { int v = doc["axis_step"] | 0; if (v < 1 || v > 64) { nack("axis_step"); return true; } padStep = (uint8_t)v; prefs.putUChar("padst", padStep); }
    JsonDocument d; d["ok"] = true; d["evt"] = "usbmode"; d["midi"] = (want & 1) != 0; d["gamepad"] = (want & 2) != 0; d["drive"] = (want & 4) != 0; d["active"] = usbxBoot; d["reboot_needed"] = want != usbxBoot;
    d["midi_on"] = midiOn; d["pad_on"] = padOn; d["base"] = midiBase; d["channel"] = midiCh; d["cc"] = midiCc; d["velocity"] = midiVel; d["axis_step"] = padStep; d["axis"] = padAxis;
    JsonArray av = d["available"].to<JsonArray>(); for (int i = 0; i < 3; i++) if (have[i]) av.add(K[i]);
    sendDoc(d); return true;
  }
  if (!strcmp(cmd, "usbdrive")) {
    const char* op = doc["op"] | "state";
    if (!(usbxBoot & 4) && strcmp(op, "state")) { nack("not_active"); return true; }
    if (!strcmp(op, "refresh")) driveBuild();
    else if (!strcmp(op, "image")) {                                        // base64 of a slice of the image (for backups / tests); 'off' and 'n' are bytes, at most 1500 a time
      int off = doc["off"] | 0, n = doc["n"] | 1024; if (off < 0 || n < 1 || n > 1500 || (size_t)off >= driveImg.size()) { nack("range"); return true; }
      if ((size_t)(off + n) > driveImg.size()) n = (int)driveImg.size() - off;
      static const char* B = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; String s; const uint8_t* p = driveImg.data() + off;
      for (int i = 0; i < n; i += 3) { uint32_t v = (uint32_t)p[i] << 16 | (i + 1 < n ? (uint32_t)p[i + 1] << 8 : 0) | (i + 2 < n ? p[i + 2] : 0); s += B[(v >> 18) & 63]; s += B[(v >> 12) & 63]; s += i + 1 < n ? B[(v >> 6) & 63] : '='; s += i + 2 < n ? B[v & 63] : '='; }
      JsonDocument d; d["ok"] = true; d["evt"] = "usbdrive"; d["off"] = off; d["n"] = n; d["total"] = (uint32_t)driveImg.size(); d["d"] = s; sendDoc(d); return true;
    } else if (strcmp(op, "state")) { nack("op"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "usbdrive"; d["active"] = (usbxBoot & 4) != 0; d["size"] = (uint32_t)driveImg.size(); d["readonly"] = true; sendDoc(d); return true;
  }
  return false;
}
