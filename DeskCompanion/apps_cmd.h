// ================================================================================================================
//  Firmware 2.0: the commands of the apps in apps.h, the service loop and the overlays.  Included by DeskCompanion.ino right after apps.h.
//  Every command answers {"ok":true,"evt":...} or {"ok":false,"err":...} and validates everything before it changes anything.
// ================================================================================================================
static void confirmCancel() { confirmOn = false; confirmSpec = ""; needRedraw = true; showToast("CANCELLED"); }
static void confirmRun() {
  String sp = confirmSpec; uint16_t sid = confirmSid;
  confirmOn = false; confirmSpec = ""; needRedraw = true;
  confirmBypass = true; runSpecJson(sp, nullptr, sid); confirmBypass = false;
  JsonDocument d; d["evt"] = "confirm"; d["what"] = "run"; apEvt(d);
}
static void strLeave() { if (strActive) strStop(); }
static void extLoad() {                                           // after loadSettings(): everything the apps keep in flash
  apRng ^= esp_random() | 1u;
  pinSet = prefs.getBool("pinon", false) && prefs.isKey("pinh"); pinAll = prefs.getBool("pinall", false); pinIdleS = prefs.getUShort("pinidle", 0); pinLocked = pinSet;
  snipLoadCount(); radLoad(); totpLoad(); skLoad(); calLoad(); sysLoad();
}
static void extService() {
  uint32_t now = millis();
  if (!macroRun) macroLong = false;
  if (confirmOn && (uint32_t)(now - confirmAt) > 6000) confirmCancel();
  { static uint8_t lastSusp = 0; if (usbWasSuspended != lastSusp) { lastSusp = usbWasSuspended; crumb(10, usbWasSuspended); applyBacklight(); } }          // the PC went to sleep / woke up
  if (pinSet) {                                                    // auto-lock: after the idle time, and as soon as the PC goes to sleep
    if (!pinLocked && pinIdleS && (uint32_t)(now - lastActivity) >= (uint32_t)pinIdleS * 1000UL) apPinLock();
    static uint8_t wasSusp = 0;
    if (usbWasSuspended && !wasSusp && !pinLocked) apPinLock();
    wasSusp = usbWasSuspended;
  }
}
static void extOverlays() {
  uint32_t now = millis();
  if (legendMode && mode <= 20 && !gameBusy() && (mode == M_CLOCK || mode == M_POMO || mode == M_MEDIA || mode == M_TELEM || mode == M_INFO || mode == M_VIZ || mode == M_STOPW || mode == M_BREATH || mode == M_TOYS || mode == M_HABIT)) {
    uint16_t lc = curLayer == 0 ? C_ACC : curLayer == 1 ? C_ACC2 : C_WARN;
    spr.setTextDatum(MC_DATUM);
    for (int i = 0; i < 5; i++) {
      int cx = 30 + i * 45; uint8_t ic[72]; char ik[6]; apIconKey(curLayer, (uint8_t)i, ik);
      if (legendMode == 2 && apIconLoad(ik, ic)) apIconDraw(cx - 12, 164, ic, lc);
      else { char b[10]; if (lbl[curLayer][i][0]) snprintf(b, sizeof b, "%s", lbl[curLayer][i]); else snprintf(b, sizeof b, "K%d", i + 1); spr.setTextColor(lc); spr.drawString(b, cx, 176, 1); }
      spr.fillRect(cx - 8, 190, 16, 2, lc);
    }
  }
  ripplesDraw();
  if (macroRun && macroLong && macroRingOn && macro.size()) arcBand(120, 120, 119, 113, 0, 360.0f * macroIdx / macro.size(), C_OK);
  if (confirmOn) {
    spr.fillRoundRect(24, 76, 192, 88, 14, C_DIM); spr.drawRoundRect(24, 76, 192, 88, 14, C_WARN);
    spr.setTextDatum(MC_DATUM); spr.setTextColor(C_WARN); spr.drawString("CONFIRM?", 120, 102, 4);
    spr.setTextColor(C_TXT); spr.drawString("HOLD THE DIAL 1 S", 120, 134, 2); spr.setTextColor(C_DIM2); spr.drawString("ANY KEY = CANCEL", 120, 152, 1);
    if (encHeld) arcBand(120, 120, 119, 109, 0, 360.0f * min<uint32_t>(1000, now - encDownAt) / 1000.0f, C_WARN);
  }
}

// ---- state of the current app (read-only; the app's own UI and the tests use it)
static void appStateJson(JsonObject o) {
  o["mode"] = mode; o["name"] = appName(mode); o["locked"] = pinLocked;
  switch (mode) {
    case M_SNIPS: o["n"] = snipN; o["sel"] = snipSel; { String l, t; if (snipGet(snipSel, l, t)) { o["label"] = l; o["text"] = t; } } break;
    case M_RADIAL: o["sel"] = radSel; o["used"] = radSlots[radSel].used; o["label"] = radSlots[radSel].l; break;
    case M_TYPER: o["page"] = tyPage; o["buf"] = tyPage == 2 ? moBuf : tyBuf; o["sel"] = tySel; o["shift"] = tyShift; o["code"] = moCode; break;
    case M_TOTP: o["n"] = totpN; o["sel"] = totpSel; if (totpN && timeSynced && !pinBlocks(true)) { o["code"] = totpCode(totpSel, totpNow()); o["rem"] = totpAcc[totpSel].period - (int)(totpNow() % totpAcc[totpSel].period); } break;
    case M_SKETCH: { o["x"] = skX; o["y"] = skY; o["pen"] = skPen; o["erase"] = skErase; o["vert"] = skVert; int c = 0; for (int i = 0; i < 200; i++) c += __builtin_popcount(skBits[i]); o["count"] = c; } break;
    case M_CALC: o["page"] = calcPage; o["entry"] = calcEntry; o["acc"] = calcAcc; o["op"] = String(calcOp ? calcOp : ' '); o["err"] = calcErr; o["sel"] = CALC_TOK[calcSel];
      o["unit"] = unitVal; o["pair"] = unitPair; o["rev"] = unitRev; o["conv"] = unitConv(unitVal, unitPair, unitRev); break;
    case M_TIMERS: o["page"] = tmPage; o["bpm"] = moBpm; o["run"] = moRun; o["beat"] = moBeat; o["bpb"] = moBpb; o["iv"] = ivPhase; o["round"] = ivRound; o["work"] = ivWork; o["rest"] = ivRest; o["rounds"] = ivRounds;
      o["field"] = ivField; o["paused"] = ivPaused; o["dkind"] = dcKind; o["result"] = dcResult; o["segs"] = dcSegs; o["spin"] = dcSpin; o["vel"] = dcVel; break;
    case M_CAL: o["page"] = calPage; o["off"] = calOff; o["geo"] = geoSet;
      if (calPage == 1 && timeSynced) { JsonArray za = o["zones"].to<JsonArray>(); time_t nowT = time(nullptr); for (int i = 0; i < 4; i++) if (wz[i].used) { time_t t = nowT + (time_t)wz[i].off * 60; struct tm z; gmtime_r(&t, &z); char b[8]; snprintf(b, sizeof b, "%02d:%02d", z.tm_hour, z.tm_min); JsonObject e = za.add<JsonObject>(); e["n"] = wz[i].n; e["t"] = b; } }
      break;
    default: appGameState(o); break;
  }
}
static bool apAsciiText(const char* t, size_t maxLen, bool nl) {
  size_t n = 0; for (; *t; t++, n++) { uint8_t c = (uint8_t)*t; if (c < 32 && !(nl && c == '\n')) return false; if (c > 126) return false; }
  return n <= maxLen;
}
static bool cmdExt(const char* cmd, JsonDocument& doc) {
  if (cmdSys(cmd, doc) || cmdUsb(cmd, doc)) return true;
  // ---------------------------------------------------------------- snippets (cap "snippets")
  if (!strcmp(cmd, "snippets")) {
    const char* op = doc["op"] | "list";
    if (!strcmp(op, "set")) {
      JsonArrayConst it = doc["items"].as<JsonArrayConst>(); int at = doc["at"] | 0;
      if (it.isNull() || it.size() == 0 || it.size() > 12 || at < 0 || at + (int)it.size() > SNIP_MAX) { nack("items"); return true; }
      for (JsonVariantConst v : it) { const char* l = v["l"] | ""; const char* t = v["t"] | ""; if (!*l || !apAsciiText(l, 14, false) || !*t || !apAsciiText(t, 120, true)) { nack("items"); return true; } }
      int i = at;
      for (JsonVariantConst v : it) { char k[8]; snipKey(i++, k); String val = String(v["l"].as<const char*>()) + "\t" + String(v["t"].as<const char*>()); if (!prefs.putString(k, val)) { nack("nvs_full"); return true; } }
      int total = doc["total"].isNull() ? max(snipN, at + (int)it.size()) : (doc["total"] | 0);
      if (total < at + (int)it.size() || total > SNIP_MAX) { nack("total"); return true; }
      prefs.putUShort("snn", (uint16_t)total); snipLoadCount(); needRedraw = true;
    } else if (!strcmp(op, "clear")) {
      for (int i = 0; i < SNIP_MAX; i++) { char k[8]; snipKey(i, k); if (prefs.isKey(k)) prefs.remove(k); }
      prefs.putUShort("snn", 0); snipN = 0; snipSel = 0; needRedraw = true;
    } else if (strcmp(op, "list")) { nack("op"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "snippets"; d["n"] = snipN; JsonArray a = d["labels"].to<JsonArray>();
    int from = doc["from"] | 0, cnt = min(20, (int)(doc["n"] | 20));
    for (int i = from; i < snipN && i < from + cnt; i++) { String l, t; if (snipGet(i, l, t)) a.add(l); else a.add(""); }
    sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- radial launcher (cap "radial")
  if (!strcmp(cmd, "radial")) {
    if (!doc["slots"].isNull()) {
      JsonArrayConst sl = doc["slots"].as<JsonArrayConst>();
      if (sl.isNull() || sl.size() > 8) { nack("slots"); return true; }
      String out[8];
      int i = 0;
      for (JsonVariantConst v : sl) {
        if (v.isNull() || (v.is<JsonObjectConst>() && v.as<JsonObjectConst>().size() == 0)) { i++; continue; }
        const char* l = v["l"] | ""; if (!*l || !apAsciiText(l, 8, false)) { nack("slots"); return true; }
        JsonDocument spec; spec["type"] = v["a"]["type"]; spec["val"] = v["a"]["val"]; std::vector<Step> tmp;
        if (!parseSpec(spec.as<JsonVariantConst>(), tmp)) { nack("spec"); return true; }
        JsonDocument w; w["l"] = l; w["a"] = spec; serializeJson(w, out[i]); i++;
      }
      for (int k = 0; k < 8; k++) { char key[6]; snprintf(key, sizeof key, "rl%d", k); if (out[k].length()) prefs.putString(key, out[k]); else prefs.remove(key); }
      radLoad(); needRedraw = true;
    }
    JsonDocument d; d["ok"] = true; d["evt"] = "radial"; JsonArray a = d["slots"].to<JsonArray>();
    for (int i = 0; i < 8; i++) { if (!radSlots[i].used) { a.add<JsonObject>(); continue; } JsonObject o = a.add<JsonObject>(); o["l"] = radSlots[i].l; JsonDocument sp; if (!deserializeJson(sp, radSlots[i].spec)) o["a"] = sp; }
    sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- 24x24 key / radial icons (cap "icons")
  if (!strcmp(cmd, "icon")) {
    const char* op = doc["op"] | "list"; const char* tg = doc["target"] | "key";
    if (strcmp(op, "list")) {
      char key[6];
      if (!strcmp(tg, "key")) { int l = doc["layer"] | 0, k = doc["key"] | 0; if (l < 0 || l >= LAYERS || k < 1 || k > 5) { nack("key"); return true; } apIconKey((uint8_t)l, (uint8_t)(k - 1), key); }
      else if (!strcmp(tg, "radial")) { int s = doc["slot"] | -1; if (s < 0 || s > 7) { nack("slot"); return true; } snprintf(key, sizeof key, "ri%d", s); }
      else { nack("target"); return true; }
      if (!strcmp(op, "set")) {
        const char* h = doc["bits"] | ""; uint8_t b[72];
        if (strlen(h) != 144 || !apHex(h, b, 72)) { nack("bits"); return true; }
        if (!prefs.putBytes(key, b, 72)) { nack("nvs_full"); return true; }
      } else if (!strcmp(op, "clear")) prefs.remove(key); else { nack("op"); return true; }
      needRedraw = true;
    }
    JsonDocument d; d["ok"] = true; d["evt"] = "icons"; JsonArray ks = d["keys"].to<JsonArray>();
    for (uint8_t l = 0; l < LAYERS; l++) { JsonArray r = ks.add<JsonArray>(); for (uint8_t k = 0; k < 5; k++) { char key[6]; apIconKey(l, k, key); r.add(prefs.isKey(key)); } }
    JsonArray rd = d["radial"].to<JsonArray>(); for (int i = 0; i < 8; i++) { char key[6]; snprintf(key, sizeof key, "ri%d", i); rd.add(prefs.isKey(key)); }
    sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- TOTP accounts (cap "totp"); secrets are never sent back
  if (!strcmp(cmd, "totp")) {
    const char* op = doc["op"] | "list";
    if (!strcmp(op, "set")) {
      if (pinBlocks(true)) { nack("locked"); return true; }
      JsonArrayConst it = doc["items"].as<JsonArrayConst>(); int at = doc["at"] | 0;
      if (it.isNull() || it.size() == 0 || at < 0 || at + (int)it.size() > 10) { nack("items"); return true; }
      for (JsonVariantConst v : it) {
        uint8_t kb[32]; const char* n = v["n"] | ""; int kl = base32Decode(v["s"] | "", kb, 32); int dg = v["d"] | 6, pr = v["p"] | 30;
        if (!*n || !apAsciiText(n, 10, false) || kl < 10 || dg < 6 || dg > 8 || pr < 15 || pr > 120) { nack("items"); return true; }
      }
      int i = at; for (JsonVariantConst v : it) { char k[6]; snprintf(k, sizeof k, "tp%d", i++); JsonDocument w; w["n"] = v["n"]; w["s"] = v["s"]; w["d"] = v["d"] | 6; w["p"] = v["p"] | 30; String s; serializeJson(w, s); if (!prefs.putString(k, s)) { nack("nvs_full"); return true; } }
      totpLoad(); needRedraw = true;
    } else if (!strcmp(op, "clear")) {
      if (pinBlocks(true)) { nack("locked"); return true; }
      for (int i = 0; i < 10; i++) { char k[6]; snprintf(k, sizeof k, "tp%d", i); prefs.remove(k); } totpLoad(); needRedraw = true;
    } else if (!strcmp(op, "code")) {
      int i = doc["i"] | 0; if (pinBlocks(true)) { nack("locked"); return true; }
      if (i < 0 || i > 9 || !totpAcc[i].used) { nack("i"); return true; }
      uint64_t t = doc["t"].isNull() ? (uint64_t)totpNow() : doc["t"].as<uint64_t>();
      JsonDocument d; d["ok"] = true; d["evt"] = "totp_code"; d["code"] = totpCode(i, t); d["rem"] = totpAcc[i].period - (int)(t % totpAcc[i].period); sendDoc(d); return true;
    } else if (strcmp(op, "list")) { nack("op"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "totp"; d["n"] = totpN; JsonArray a = d["names"].to<JsonArray>();
    for (int i = 0; i < 10; i++) { if (totpAcc[i].used) { JsonObject o = a.add<JsonObject>(); o["i"] = i; o["n"] = totpAcc[i].name; o["d"] = totpAcc[i].digits; o["p"] = totpAcc[i].period; } }
    sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- PIN lock (cap "pin"): the PIN is only ever typed on the pad
  if (!strcmp(cmd, "pin")) {
    const char* op = doc["op"] | "state";
    auto digits = [](const char* p) { size_t n = strlen(p); if (n < 4 || n > 8) return false; for (; *p; p++) if (*p < '0' || *p > '9') return false; return true; };
    if (!strcmp(op, "set")) {
      const char* np = doc["pin"] | ""; const char* old = doc["old"] | "";
      if (!digits(np)) { nack("pin"); return true; }
      if (pinSet && (pinLocked || !apPinCheck(old))) { nack("old_pin"); return true; }
      uint32_t salt = esp_random(); prefs.putUInt("pins", salt); prefs.putUInt("pinh", apPinHash(np, salt)); pinSet = true; prefs.putBool("pinon", true);
    } else if (!strcmp(op, "clear")) {
      if (!pinSet) { nack("no_pin"); return true; }
      if (pinLocked || !apPinCheck(doc["pin"] | "")) { nack("old_pin"); return true; }
      pinSet = false; pinLocked = false; prefs.putBool("pinon", false); prefs.remove("pins"); prefs.remove("pinh");
    } else if (!strcmp(op, "lock")) { if (!pinSet) { nack("no_pin"); return true; } apPinLock(); }
    else if (!strcmp(op, "config")) {
      if (pinSet && (pinLocked || !apPinCheck(doc["pin"] | ""))) { nack("old_pin"); return true; }
      if (!doc["all"].isNull()) { pinAll = doc["all"] | false; prefs.putBool("pinall", pinAll); }
      if (!doc["idle"].isNull()) { int v = doc["idle"] | 0; if (v < 0 || v > 3600) { nack("idle"); return true; } pinIdleS = (uint16_t)v; prefs.putUShort("pinidle", pinIdleS); }
    } else if (strcmp(op, "state")) { nack("op"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "pin"; d["set"] = pinSet; d["locked"] = pinLocked; d["all"] = pinAll; d["idle"] = pinIdleS; d["fails"] = pinFails; sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- calendar dots, world clock, location (cap "calendar")
  if (!strcmp(cmd, "cal")) {
    JsonArrayConst ms = doc["months"].as<JsonArrayConst>();
    if (ms.isNull() || ms.size() > 3) { nack("months"); return true; }
    JsonDocument keep; JsonArray out = keep.to<JsonArray>();
    for (JsonVariantConst m : ms) {
      int y = m["y"] | 0, mo = m["m"] | 0; JsonArrayConst dd = m["days"].as<JsonArrayConst>();
      if (y < 2000 || y > 2100 || mo < 1 || mo > 12 || dd.isNull()) { nack("months"); return true; }
      uint32_t mask = 0; for (JsonVariantConst d : dd) { int x = d.as<int>(); if (x < 1 || x > daysInMonth(y, mo)) { nack("days"); return true; } mask |= (1UL << (x - 1)); }
      JsonObject o = out.add<JsonObject>(); o["y"] = y; o["m"] = mo; o["k"] = mask;
    }
    String js; serializeJson(keep, js); prefs.putString("calev", js); calLoad(); needRedraw = true; ack("cal"); return true;
  }
  if (!strcmp(cmd, "worldclock")) {
    JsonArrayConst zs = doc["zones"].as<JsonArrayConst>();
    if (zs.isNull() || zs.size() > 4) { nack("zones"); return true; }
    JsonDocument keep; JsonArray out = keep.to<JsonArray>();
    for (JsonVariantConst z : zs) { const char* n = z["n"] | ""; int o = z["o"] | 0; if (!*n || !apAsciiText(n, 6, false) || o < -720 || o > 840) { nack("zones"); return true; } JsonObject e = out.add<JsonObject>(); e["n"] = n; e["o"] = o; }
    String js; serializeJson(keep, js); prefs.putString("wzones", js); calLoad(); needRedraw = true; ack("worldclock"); return true;
  }
  if (!strcmp(cmd, "geo")) {
    if (doc["lat"].isNull() || doc["lon"].isNull()) { nack("geo"); return true; }
    float la = doc["lat"] | 0.0f, lo = doc["lon"] | 0.0f;
    if (la < -90 || la > 90 || lo < -180 || lo > 180) { nack("geo"); return true; }
    prefs.putInt("geo", (int32_t)lroundf(la * 100)); prefs.putInt("geo2", (int32_t)lroundf(lo * 100)); calLoad(); needRedraw = true;
    double rs = 0, ss = 0; time_t n = time(nullptr); struct tm u; gmtime_r(&n, &u);
    JsonDocument d; d["ok"] = true; d["evt"] = "geo"; bool ok = sunTimes(u.tm_year + 1900, u.tm_mon + 1, u.tm_mday, la, lo, rs, ss);
    d["sun"] = ok; if (ok) { d["rise"] = (uint32_t)rs; d["set"] = (uint32_t)ss; } d["moon_age"] = moonAge((double)n); sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- the pixel sketch (cap "sketch")
  if (!strcmp(cmd, "sketch")) {
    const char* op = doc["op"] | "get";
    if (!strcmp(op, "set")) { const char* h = doc["bits"] | ""; uint8_t b[200]; if (strlen(h) != 400 || !apHex(h, b, 200)) { nack("bits"); return true; } memcpy(skBits, b, 200); needRedraw = true; if (doc["save"] | false) prefs.putBytes("skt", skBits, 200); }
    else if (!strcmp(op, "clear")) { memset(skBits, 0, sizeof skBits); needRedraw = true; }
    else if (strcmp(op, "get")) { nack("op"); return true; }
    JsonDocument d; d["ok"] = true; d["evt"] = "sketch"; d["bits"] = apToHex(skBits, 200); d["saved"] = prefs.isKey("skt"); sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- PC-rendered screen (cap "stream"): {"cmd":"stream","op":"start|stop|stats"} and {"cmd":"tiles","t":[[tx,ty,"base64"],...],"end":true}
  if (!strcmp(cmd, "stream")) {
    const char* op = doc["op"] | "stats";
    if (!strcmp(op, "start")) { if (!strStart()) { nack("no_mem"); return true; } setMode(M_STREAM); }
    else if (!strcmp(op, "stop")) strStop();
    else if (strcmp(op, "stats")) { nack("op"); return true; }
    uint32_t el = strFirstAt ? (uint32_t)(millis() - strFirstAt) : 0;
    JsonDocument d; d["ok"] = true; d["evt"] = "stream"; d["active"] = strActive; d["frames"] = strFrames; d["tiles"] = strTiles; d["bytes"] = strBytes; d["ms"] = el; d["heap"] = ESP.getFreeHeap(); sendDoc(d); return true;
  }
  if (!strcmp(cmd, "tiles")) {
    if (!strActive || mode != M_STREAM) { nack("not_streaming"); return true; }
    JsonArrayConst t = doc["t"].as<JsonArrayConst>();
    if (t.isNull() || t.size() == 0 || t.size() > 12) { nack("tiles"); return true; }
    static uint8_t tmp[520]; uint32_t bytes = 0;
    for (int pass = 0; pass < 2; pass++) {                              // pass 0 validates every tile, pass 1 draws them: a bad tile changes nothing
      for (JsonVariantConst v : t) {
        if (!v.is<JsonArrayConst>() || v.size() != 3) { nack("tiles"); return true; }
        int n = b64Decode(v[2] | "", tmp, sizeof tmp);
        if (n < 0 || !strTileOk(v[0] | -1, v[1] | -1, tmp, n)) { nack("tile"); return true; }
        if (pass) { strTile(v[0] | 0, v[1] | 0, tmp, n); bytes += (uint32_t)n; }
      }
    }
    strTiles += t.size(); strBytes += bytes; if (doc["end"] | false) strFrames++;
    strLastAt = millis(); needRedraw = true;
    JsonDocument d; d["ok"] = true; d["evt"] = "tiles"; d["n"] = t.size(); sendDoc(d); return true;
  }
  // ---------------------------------------------------------------- {"cmd":"app","op":"state"}: what the current app screen is doing (read-only); seed / set are test hooks of the simulator and the native build
  if (!strcmp(cmd, "app")) {
    const char* op = doc["op"] | "state";
    if (!strcmp(op, "state")) { JsonDocument d; d["ok"] = true; d["evt"] = "app"; appStateJson(d.to<JsonObject>()); d["ok"] = true; d["evt"] = "app"; sendDoc(d); return true; }
#if defined(DC_SIM) || defined(DC_NATIVE)
    if (!strcmp(op, "seed")) { apRng = (uint32_t)(doc["n"] | 1) | 1u; ack("app"); return true; }
    if (extTestHook(op, doc)) return true;
#endif
    nack("op"); return true;
  }
  return false;
}
