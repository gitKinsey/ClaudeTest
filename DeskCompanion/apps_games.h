// ================================================================================================================
//  Firmware 2.0 apps, part 3: falling blocks (34), puzzles - 2048 / memory / tic-tac-toe / minesweeper (35), space game (36), PC-rendered stream (39 / 40).
//  Included by apps_more.h.  Every game keeps all its state in plain variables so the tests (cmd "app") can read and set it.
// ================================================================================================================

// ================================================================ screen 29: falling blocks (tetris-like): dial moves, click rotates
static const uint16_t BK_SHAPES[7][4] = {                                   // 4x4 bitmaps per rotation, row 0 = top, bit 3 = leftmost; I O T S Z J L
  {0x0F00, 0x2222, 0x00F0, 0x4444}, {0x6600, 0x6600, 0x6600, 0x6600}, {0x4E00, 0x4640, 0x0E40, 0x4C40}, {0x6C00, 0x4620, 0x06C0, 0x8C40},
  {0xC600, 0x2640, 0x0C60, 0x4C80}, {0x8E00, 0x6440, 0x0E20, 0x44C0}, {0x2E00, 0x4460, 0x0E80, 0xC440}};
static uint16_t bkRows[20]; static int8_t bkT = 0, bkR = 0, bkX = 3, bkY = 0, bkNext = 1, bkHold = -1; static bool bkHeld = false;
static uint32_t bkScore = 0; static uint16_t bkLines = 0; static uint8_t bkState = 0; static uint32_t bkFallAt = 0;      // state: 0 ready, 1 running, 2 game over, 3 paused
static bool bkFits(int t, int r, int x, int y) {
  uint16_t m = BK_SHAPES[t][r & 3];
  for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++) if (m & (0x8000 >> (row * 4 + col))) {
    int bx = x + col, by = y + row;
    if (bx < 0 || bx > 9 || by > 19) return false;
    if (by >= 0 && (bkRows[by] & (1 << bx))) return false;
  }
  return true;
}
static uint8_t bkLevel() { return (uint8_t)min<int>(15, bkLines / 10); }
static uint32_t bkDelay() { return 800 - bkLevel() * 50; }
static bool bkSpawn(int t) {
  bkT = (int8_t)t; bkR = 0; bkX = 3; bkY = 0; bkHeld = false; bkNext = (int8_t)(apRand() % 7);
  if (!bkFits(bkT, bkR, bkX, bkY)) { bkState = 2; needRedraw = true; return false; }
  return true;
}
static void bkNew() { memset(bkRows, 0, sizeof bkRows); bkScore = 0; bkLines = 0; bkHold = -1; bkState = 1; bkNext = (int8_t)(apRand() % 7); bkSpawn(apRand() % 7); bkFallAt = millis() + bkDelay(); needRedraw = true; }
static void bkLock() {
  uint16_t m = BK_SHAPES[bkT][bkR & 3];
  for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++) if (m & (0x8000 >> (row * 4 + col))) { int by = bkY + row, bx = bkX + col; if (by >= 0 && by < 20) bkRows[by] |= (1 << bx); }
  int cleared = 0;
  for (int y = 19; y >= 0; y--) if (bkRows[y] == 0x3FF) { for (int k = y; k > 0; k--) bkRows[k] = bkRows[k - 1]; bkRows[0] = 0; cleared++; y++; }
  static const uint16_t PTS[5] = {0, 100, 300, 500, 800};
  bkScore += (uint32_t)PTS[cleared] * (bkLevel() + 1); bkLines += (uint16_t)cleared;
  if (cleared) ledFlash(0, 40, 60, 150);
  bkSpawn(bkNext); bkFallAt = millis() + bkDelay(); needRedraw = true;
}
static bool bkMove(int dx) { if (bkState != 1 || !bkFits(bkT, bkR, bkX + dx, bkY)) return false; bkX = (int8_t)(bkX + dx); needRedraw = true; return true; }
static bool bkRotate(int dir) {
  if (bkState != 1) return false;
  int nr = (bkR + dir) & 3;
  static const int8_t KICK[5] = {0, -1, 1, -2, 2};
  for (int k = 0; k < 5; k++) if (bkFits(bkT, nr, bkX + KICK[k], bkY)) { bkR = (int8_t)nr; bkX = (int8_t)(bkX + KICK[k]); needRedraw = true; return true; }
  return false;
}
static bool bkDown() { if (bkState != 1) return false; if (bkFits(bkT, bkR, bkX, bkY + 1)) { bkY++; bkFallAt = millis() + bkDelay(); needRedraw = true; return true; } bkLock(); return false; }
static void bkDrop() { if (bkState != 1) return; int n = 0; while (bkFits(bkT, bkR, bkX, bkY + 1)) { bkY++; n++; } bkScore += (uint32_t)n * 2; bkLock(); }
static void sceneBlocks() {
  apFrame("BLOCKS");
  const int ox = 70, oy = 36, cs = 8;
  spr.drawRect(ox - 2, oy - 2, 10 * cs + 4, 20 * cs + 4, C_DIM2);
  for (int y = 0; y < 20; y++) for (int x = 0; x < 10; x++) if (bkRows[y] & (1 << x)) spr.fillRect(ox + x * cs, oy + y * cs, cs - 1, cs - 1, C_ACC);
  if (bkState == 1 || bkState == 3) {
    uint16_t m = BK_SHAPES[bkT][bkR & 3];
    for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++) if (m & (0x8000 >> (row * 4 + col))) { int by = bkY + row; if (by >= 0) spr.fillRect(ox + (bkX + col) * cs, oy + by * cs, cs - 1, cs - 1, C_ACC2); }
  }
  char b[16]; spr.setTextDatum(MC_DATUM); spr.setTextColor(C_TXT); snprintf(b, sizeof b, "%lu", (unsigned long)bkScore); spr.drawString(b, 180, 80, 2);
  spr.setTextColor(C_GRAY); snprintf(b, sizeof b, "L%u", bkLines); spr.drawString(b, 180, 100, 2);
  { uint16_t m = BK_SHAPES[bkNext][0]; for (int row = 0; row < 4; row++) for (int col = 0; col < 4; col++) if (m & (0x8000 >> (row * 4 + col))) spr.fillRect(168 + col * 5, 120 + row * 5, 4, 4, C_DIM2); }
  if (bkState == 0) { spr.setTextColor(C_TXT); spr.drawString("K1 START", 120, 112, 2); }
  if (bkState == 2) { spr.fillRoundRect(60, 96, 120, 40, 10, C_DIM); spr.setTextColor(C_RED); spr.drawString("GAME OVER", 120, 116, 2); }
  if (bkState == 3) { spr.setTextColor(C_WARN); spr.drawString("PAUSED", 120, 112, 2); }
}
static bool blocksKey(int i) {
  if (i == 0) { if (bkState == 1) bkState = 3; else if (bkState == 3) { bkState = 1; bkFallAt = millis() + bkDelay(); } else bkNew(); needRedraw = true; }
  else if (i == 1) bkDown();
  else if (i == 2) bkDrop();
  else if (i == 3) {                                                    // hold
    if (bkState == 1 && !bkHeld) { int cur = bkT; if (bkHold < 0) { bkHold = (int8_t)cur; bkSpawn(bkNext); } else { int h = bkHold; bkHold = (int8_t)cur; bkSpawn(h); } bkHeld = true; needRedraw = true; }
  }
  else return false;
  return true;
}
static bool blocksDial(int steps) { for (int i = 0; i < abs(steps); i++) bkMove(steps > 0 ? 1 : -1); return true; }
static bool blocksClick() { bkRotate(1); return true; }
static void blocksTick() { if (bkState == 1 && (int32_t)(millis() - bkFallAt) >= 0) bkDown(); }
static uint16_t blocksInterval() { return bkState == 1 ? 50 : 1000; }

// ================================================================ screen 30: puzzles - 2048 (page 0), memory (1), tic-tac-toe (2), minesweeper (3)
static uint8_t pzPage = 0;
static uint16_t g48[16]; static uint32_t g48Score = 0; static bool g48Over = false, g48Won = false; static uint16_t g48Undo[16]; static uint32_t g48UndoScore = 0; static bool g48Have = false;
static bool g48Line(uint16_t* l, uint32_t& sc) {                            // slide one line toward index 0; returns true when something moved
  uint16_t out[4] = {0, 0, 0, 0}; int n = 0; bool merged = false;
  for (int i = 0; i < 4; i++) { if (!l[i]) continue; if (n && out[n - 1] == l[i] && !merged) { out[n - 1] *= 2; sc += out[n - 1]; merged = true; } else { out[n++] = l[i]; merged = false; } }
  bool ch = false; for (int i = 0; i < 4; i++) { if (l[i] != out[i]) ch = true; l[i] = out[i]; }
  return ch;
}
static void g48Spawn() { int free_[16], n = 0; for (int i = 0; i < 16; i++) if (!g48[i]) free_[n++] = i; if (!n) return; g48[free_[apRand() % n]] = (apRand() % 10) ? 2 : 4; }
static bool g48CanMove() { for (int i = 0; i < 16; i++) { if (!g48[i]) return true; if ((i % 4 < 3 && g48[i] == g48[i + 1]) || (i < 12 && g48[i] == g48[i + 4])) return true; } return false; }
static bool g48Move(int dir) {                                              // 0 left, 1 right, 2 up, 3 down
  uint16_t keep[16]; memcpy(keep, g48, sizeof keep); uint32_t ks = g48Score; bool ch = false;
  for (int k = 0; k < 4; k++) {
    uint16_t l[4];
    for (int j = 0; j < 4; j++) { int idx = dir == 0 ? k * 4 + j : dir == 1 ? k * 4 + 3 - j : dir == 2 ? j * 4 + k : (3 - j) * 4 + k; l[j] = g48[idx]; }
    if (g48Line(l, g48Score)) ch = true;
    for (int j = 0; j < 4; j++) { int idx = dir == 0 ? k * 4 + j : dir == 1 ? k * 4 + 3 - j : dir == 2 ? j * 4 + k : (3 - j) * 4 + k; g48[idx] = l[j]; }
  }
  if (!ch) { g48Score = ks; return false; }
  memcpy(g48Undo, keep, sizeof keep); g48UndoScore = ks; g48Have = true;
  g48Spawn(); for (int i = 0; i < 16; i++) if (g48[i] >= 2048) g48Won = true;
  g48Over = !g48CanMove(); needRedraw = true; return true;
}
static void g48New() { memset(g48, 0, sizeof g48); g48Score = 0; g48Over = g48Won = false; g48Have = false; g48Spawn(); g48Spawn(); needRedraw = true; }
// memory
static uint8_t mmCard[16]; static uint16_t mmFace = 0, mmDone = 0; static int8_t mmA = -1, mmB = -1; static uint8_t mmCur = 0; static uint16_t mmMoves = 0; static uint32_t mmHideAt = 0; static bool mmInit = false;
static void mmNew() { for (int i = 0; i < 16; i++) mmCard[i] = (uint8_t)(i / 2); for (int i = 15; i > 0; i--) { int j = apRand() % (i + 1); uint8_t t = mmCard[i]; mmCard[i] = mmCard[j]; mmCard[j] = t; } mmFace = mmDone = 0; mmA = mmB = -1; mmMoves = 0; mmCur = 0; mmInit = true; needRedraw = true; }
static void mmFlip() {
  if (mmB >= 0 || (mmFace >> mmCur) & 1) return;
  mmFace |= (1 << mmCur);
  if (mmA < 0) mmA = (int8_t)mmCur;
  else { mmB = (int8_t)mmCur; mmMoves++; if (mmCard[mmA] == mmCard[mmB]) { mmDone |= (1 << mmA) | (1 << mmB); mmA = mmB = -1; ledFlash(0, 50, 0, 150); } else mmHideAt = millis() + 800; }
  needRedraw = true;
}
// tic-tac-toe: the pad is O and plays perfectly (minimax); you are X
static int8_t ttt[9]; static uint8_t ttCur = 0; static int8_t ttResult = 0; static bool ttAiFirst = false;     // result: 0 playing, 1 you win (impossible), 2 pad wins, 3 draw
static int ttWinner(const int8_t* b) { static const uint8_t L[8][3] = {{0, 1, 2}, {3, 4, 5}, {6, 7, 8}, {0, 3, 6}, {1, 4, 7}, {2, 5, 8}, {0, 4, 8}, {2, 4, 6}}; for (auto& l : L) if (b[l[0]] && b[l[0]] == b[l[1]] && b[l[1]] == b[l[2]]) return b[l[0]]; return 0; }
static int ttMinimax(int8_t* b, int turn, int depth, int* best) {            // turn 2 = pad (O, maximises), 1 = player
  int w = ttWinner(b); if (w == 2) return 10 - depth; if (w == 1) return depth - 10;
  bool full = true; for (int i = 0; i < 9; i++) if (!b[i]) full = false; if (full) return 0;
  int bv = turn == 2 ? -100 : 100, bm = -1;
  for (int i = 0; i < 9; i++) if (!b[i]) { b[i] = (int8_t)turn; int v = ttMinimax(b, 3 - turn, depth + 1, nullptr); b[i] = 0; if (turn == 2 ? v > bv : v < bv) { bv = v; bm = i; } }
  if (best) *best = bm; return bv;
}
static void ttCheck() { int w = ttWinner(ttt); bool full = true; for (int i = 0; i < 9; i++) if (!ttt[i]) full = false; ttResult = (int8_t)(w == 1 ? 1 : w == 2 ? 2 : full ? 3 : 0); needRedraw = true; }
static void ttAi() { if (ttResult) return; int m = -1; ttMinimax(ttt, 2, 0, &m); if (m >= 0) ttt[m] = 2; ttCheck(); }
static void ttNew() { memset(ttt, 0, sizeof ttt); ttResult = 0; ttCur = 4; if (ttAiFirst) ttAi(); needRedraw = true; }
static void ttPlay() { if (ttResult || ttt[ttCur]) return; ttt[ttCur] = 1; ttCheck(); if (!ttResult) ttAi(); }
// minesweeper-lite 8x8, 10 mines, the first reveal is always safe
static uint8_t msMine[64], msShown[64], msFlag[64], msCur = 0; static uint8_t msState = 0; static bool msPlaced = false;     // state 0 playing, 1 won, 2 lost
static int msCount(int i) { int c = 0, x = i % 8, y = i / 8; for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) { int nx = x + dx, ny = y + dy; if ((dx || dy) && nx >= 0 && nx < 8 && ny >= 0 && ny < 8 && msMine[ny * 8 + nx]) c++; } return c; }
static void msNew() { memset(msMine, 0, 64); memset(msShown, 0, 64); memset(msFlag, 0, 64); msState = 0; msPlaced = false; msCur = 27; needRedraw = true; }
static void msPlace(int safe) { int n = 0; while (n < 10) { int i = apRand() % 64; int dx = abs(i % 8 - safe % 8), dy = abs(i / 8 - safe / 8); if (msMine[i] || (dx <= 1 && dy <= 1)) continue; msMine[i] = 1; n++; } msPlaced = true; }
static void msReveal(int i) {
  if (msShown[i] || msFlag[i]) return;
  msShown[i] = 1;
  if (msMine[i]) { msState = 2; return; }
  if (!msCount(i)) { int x = i % 8, y = i / 8; for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) { int nx = x + dx, ny = y + dy; if ((dx || dy) && nx >= 0 && nx < 8 && ny >= 0 && ny < 8) msReveal(ny * 8 + nx); } }
}
static void msClick() {
  if (msState) return;
  if (!msPlaced) msPlace(msCur);
  msReveal(msCur);
  if (!msState) { int hidden = 0; for (int i = 0; i < 64; i++) if (!msShown[i]) hidden++; if (hidden == 10) msState = 1; }
  if (msState == 1) ledFlash(0, 60, 0, 400); else if (msState == 2) ledFlash(60, 0, 0, 400);
  needRedraw = true;
}
static void puzzleInit() { static bool once = false; if (once) return; once = true; g48New(); mmNew(); ttNew(); msNew(); }
static void scenePuzzle() {
  puzzleInit();
  static const char* const T[4] = {"2048", "MEMORY", "TIC TAC TOE", "MINES"};
  apFrame(T[pzPage]); char b[24]; spr.setTextDatum(MC_DATUM);
  if (pzPage == 0) {
    for (int i = 0; i < 16; i++) {
      int x = 48 + (i % 4) * 36, y = 52 + (i / 4) * 36; spr.fillRoundRect(x, y, 34, 34, 6, g48[i] ? C_DIM2 : C_DIM);
      if (g48[i]) { snprintf(b, sizeof b, "%u", g48[i]); spr.setTextColor(g48[i] >= 128 ? C_WARN : C_TXT); spr.drawString(b, x + 17, y + 17, g48[i] >= 1024 ? 1 : 2); }
    }
    snprintf(b, sizeof b, "%lu", (unsigned long)g48Score); spr.setTextColor(C_GRAY); spr.drawString(b, 120, 204, 2);
    if (g48Over) { spr.setTextColor(C_RED); spr.drawString("GAME OVER", 120, 224, 2); }
  } else if (pzPage == 1) {
    for (int i = 0; i < 16; i++) {
      int x = 52 + (i % 4) * 34, y = 50 + (i / 4) * 34; bool up = (mmFace >> i) & 1, done = (mmDone >> i) & 1;
      spr.fillRoundRect(x, y, 32, 32, 6, done ? C_DIM : up ? C_ACC : C_DIM2);
      if (up || done) { uint8_t r, g, bb; hsv2rgb((uint16_t)(mmCard[i] * 45), &r, &g, &bb); spr.fillCircle(x + 16, y + 16, 8, rgb(r, g, bb)); }
      if (i == mmCur) spr.drawRoundRect(x - 2, y - 2, 36, 36, 8, C_WARN);
    }
    snprintf(b, sizeof b, "MOVES %u", mmMoves); spr.setTextColor(C_GRAY); spr.drawString(b, 120, 204, 2);
    if (mmDone == 0xFFFF) { spr.setTextColor(C_OK); spr.drawString("DONE!", 120, 224, 2); }
  } else if (pzPage == 2) {
    for (int i = 0; i < 9; i++) {
      int x = 60 + (i % 3) * 40, y = 60 + (i / 3) * 40; spr.drawRoundRect(x, y, 38, 38, 6, i == ttCur ? C_WARN : C_DIM2);
      if (ttt[i] == 1) { spr.drawLine(x + 8, y + 8, x + 30, y + 30, C_ACC); spr.drawLine(x + 30, y + 8, x + 8, y + 30, C_ACC); }
      if (ttt[i] == 2) spr.drawCircle(x + 19, y + 19, 11, C_ACC2);
    }
    spr.setTextColor(ttResult == 2 ? C_RED : C_GRAY); spr.drawString(ttResult == 2 ? "THE PAD WINS" : ttResult == 3 ? "DRAW" : ttResult == 1 ? "YOU WIN" : ttAiFirst ? "PAD STARTS (K4)" : "YOU ARE X (K4)", 120, 204, 2);
  } else {
    for (int i = 0; i < 64; i++) {
      int x = 56 + (i % 8) * 16, y = 50 + (i / 8) * 16; bool sh = msShown[i];
      spr.fillRect(x, y, 15, 15, sh ? C_DIM : C_DIM2);
      if (sh && msMine[i]) spr.fillCircle(x + 7, y + 7, 4, C_RED);
      else if (sh && msCount(i)) { snprintf(b, sizeof b, "%d", msCount(i)); spr.setTextColor(C_TXT); spr.drawString(b, x + 8, y + 8, 1); }
      if (msFlag[i] && !sh) spr.fillTriangle(x + 4, y + 3, x + 4, y + 11, x + 11, y + 7, C_WARN);
      if (i == msCur) spr.drawRect(x - 1, y - 1, 17, 17, C_ACC);
    }
    spr.setTextColor(msState == 1 ? C_OK : msState == 2 ? C_RED : C_GRAY); spr.drawString(msState == 1 ? "CLEARED!" : msState == 2 ? "BOOM" : "K1 FLAG  K3 NEW", 120, 204, 2);
  }
  spr.setTextColor(C_DIM2); spr.drawString("K5 NEXT GAME", 120, 226, 1);
}
static bool puzzleKey(int i) {
  puzzleInit();
  if (i == 4) { pzPage = (uint8_t)((pzPage + 1) % 4); needRedraw = true; return true; }
  if (pzPage == 0) { if (i == 0) g48Move(2); else if (i == 1) g48Move(3); else if (i == 2) g48New(); else if (i == 3) { if (g48Have) { memcpy(g48, g48Undo, sizeof g48); g48Score = g48UndoScore; g48Have = false; g48Over = false; } } else return false; }
  else if (pzPage == 1) { if (i == 2) mmNew(); else return false; }
  else if (pzPage == 2) { if (i == 2) ttNew(); else if (i == 3) { ttAiFirst = !ttAiFirst; ttNew(); } else return false; }
  else { if (i == 0) { if (!msShown[msCur] && !msState) msFlag[msCur] ^= 1; } else if (i == 2) msNew(); else return false; }
  needRedraw = true; return true;
}
static bool puzzleDial(int s) {
  puzzleInit();
  if (pzPage == 0) g48Move(s > 0 ? 1 : 0);
  else if (pzPage == 1) mmCur = (uint8_t)((((int)mmCur + s) % 16 + 16) % 16);
  else if (pzPage == 2) ttCur = (uint8_t)((((int)ttCur + s) % 9 + 9) % 9);
  else msCur = (uint8_t)((((int)msCur + s) % 64 + 64) % 64);
  needRedraw = true; return true;
}
static bool puzzleClick() {
  puzzleInit();
  if (pzPage == 0) g48Move(2 + (apRand() & 1));
  else if (pzPage == 1) mmFlip();
  else if (pzPage == 2) ttPlay();
  else msClick();
  needRedraw = true; return true;
}
static void puzzleTick() { if (pzPage == 1 && mmB >= 0 && (int32_t)(millis() - mmHideAt) >= 0) { mmFace &= (uint16_t)~((1 << mmA) | (1 << mmB)); mmA = mmB = -1; needRedraw = true; } }
static uint16_t puzzleInterval() { return (pzPage == 1 && mmB >= 0) ? 100 : 1000; }

// ================================================================ screen 31: space game - the dial turns the ship, K1 thrust, K2 fire, K3 hyperspace, K4 start / pause
struct Rock { float x, y, vx, vy; uint8_t size; bool on; };
struct Bullet { float x, y, vx, vy; uint8_t life; bool on; };
static Rock rocks[12]; static Bullet bullets[4]; static float spX = 120, spY = 120, spVx = 0, spVy = 0, spAng = 0; static uint8_t spLives = 3, spWave = 0, spState = 0; static uint32_t spScore = 0, spInvUntil = 0, spAt = 0;   // state 0 ready, 1 playing, 2 over, 3 paused
static bool spThrust = false; static uint32_t spThrustUntil = 0;
static void spWaveStart() {
  spWave++; int n = min(2 + spWave, 6);
  for (int i = 0; i < 12; i++) rocks[i].on = false;
  for (int i = 0; i < n; i++) { Rock& r = rocks[i]; float a = (apRand() % 360) * DEG_TO_RAD; r.x = 120 + 110 * sinf(a); r.y = 120 - 110 * cosf(a); float d = a + (float)M_PI + ((int)(apRand() % 60) - 30) * DEG_TO_RAD; r.vx = 14 * sinf(d); r.vy = -14 * cosf(d); r.size = 3; r.on = true; }
}
static void spNew() { spX = spY = 120; spVx = spVy = 0; spAng = 0; spLives = 3; spWave = 0; spScore = 0; spState = 1; spInvUntil = millis() + 2000; for (int i = 0; i < 4; i++) bullets[i].on = false; spWaveStart(); spAt = millis(); needRedraw = true; }
static void spSplit(int i) {
  Rock& r = rocks[i]; uint8_t sz = r.size; r.on = false; spScore += sz == 3 ? 20 : sz == 2 ? 50 : 100;
  if (sz > 1) for (int k = 0; k < 2; k++) for (int j = 0; j < 12; j++) if (!rocks[j].on) { rocks[j].on = true; rocks[j].x = r.x; rocks[j].y = r.y; rocks[j].size = (uint8_t)(sz - 1); float a = (apRand() % 360) * DEG_TO_RAD; rocks[j].vx = 22 * sinf(a); rocks[j].vy = -22 * cosf(a); break; }
  ledFlash(30, 20, 0, 80);
}
static int spRockR(uint8_t sz) { return sz == 3 ? 14 : sz == 2 ? 9 : 5; }
static void spaceTickAt(uint32_t now, float dt) {
  if (spState != 1) return;
  float a = spAng * DEG_TO_RAD;
  if (spThrust && (int32_t)(spThrustUntil - now) > 0) { spVx += 120 * sinf(a) * dt; spVy -= 120 * cosf(a) * dt; }
  float drag = powf(0.5f, dt); spVx *= drag; spVy *= drag; spX += spVx * dt; spY += spVy * dt;
  if (spX < 0) spX += 240; if (spX > 240) spX -= 240; if (spY < 0) spY += 240; if (spY > 240) spY -= 240;
  for (int i = 0; i < 12; i++) if (rocks[i].on) { rocks[i].x += rocks[i].vx * dt; rocks[i].y += rocks[i].vy * dt; if (rocks[i].x < 0) rocks[i].x += 240; if (rocks[i].x > 240) rocks[i].x -= 240; if (rocks[i].y < 0) rocks[i].y += 240; if (rocks[i].y > 240) rocks[i].y -= 240; }
  for (int b = 0; b < 4; b++) if (bullets[b].on) {
    Bullet& u = bullets[b]; u.x += u.vx * dt; u.y += u.vy * dt; if (u.life-- == 0 || u.x < -4 || u.x > 244 || u.y < -4 || u.y > 244) { u.on = false; continue; }
    for (int i = 0; i < 12; i++) if (rocks[i].on) { float dx = u.x - rocks[i].x, dy = u.y - rocks[i].y; int r = spRockR(rocks[i].size); if (dx * dx + dy * dy < r * r) { u.on = false; spSplit(i); break; } }
  }
  if ((int32_t)(spInvUntil - now) <= 0) for (int i = 0; i < 12; i++) if (rocks[i].on) { float dx = spX - rocks[i].x, dy = spY - rocks[i].y; int r = spRockR(rocks[i].size) + 5; if (dx * dx + dy * dy < r * r) {
    rocks[i].on = false; if (spLives) spLives--; ledFlash(80, 0, 0, 300); spX = spY = 120; spVx = spVy = 0; spInvUntil = now + 2000;
    if (!spLives) spState = 2; break; } }
  bool any = false; for (int i = 0; i < 12; i++) if (rocks[i].on) any = true; if (!any && spState == 1) { spWaveStart(); spInvUntil = now + 1500; }
  needRedraw = true;
}
static void spaceTick() { uint32_t now = millis(); if (spState == 1 && now - spAt >= 30) { float dt = (now - spAt) / 1000.0f; spAt = now; spaceTickAt(now, min(dt, 0.1f)); } else if (spState != 1) spAt = now; }
static uint16_t spaceInterval() { return spState == 1 ? 30 : 1000; }
static void sceneSpace() {
  spr.fillSprite(C_BG); spr.setTextDatum(MC_DATUM);
  for (int i = 0; i < 12; i++) if (rocks[i].on) spr.drawCircle(lroundf(rocks[i].x), lroundf(rocks[i].y), spRockR(rocks[i].size), C_GRAY);
  for (int b = 0; b < 4; b++) if (bullets[b].on) spr.fillCircle(lroundf(bullets[b].x), lroundf(bullets[b].y), 2, C_WARN);
  if (spState != 2) {
    float a = spAng * DEG_TO_RAD; bool blink = (int32_t)(spInvUntil - millis()) > 0 && ((millis() / 120) & 1);
    if (!blink) { int x0 = lroundf(spX + 9 * sinf(a)), y0 = lroundf(spY - 9 * cosf(a)), x1 = lroundf(spX + 7 * sinf(a + 2.5f)), y1 = lroundf(spY - 7 * cosf(a + 2.5f)), x2 = lroundf(spX + 7 * sinf(a - 2.5f)), y2 = lroundf(spY - 7 * cosf(a - 2.5f));
      spr.fillTriangle(x0, y0, x1, y1, x2, y2, C_ACC); }
  }
  char b[20]; snprintf(b, sizeof b, "%lu", (unsigned long)spScore); spr.setTextColor(C_TXT); spr.drawString(b, 120, 22, 2);
  for (int i = 0; i < spLives; i++) spr.fillTriangle(100 + i * 16, 42, 94 + i * 16, 52, 106 + i * 16, 52, C_ACC2);
  if (spState == 0) { spr.setTextColor(C_TXT); spr.drawString("K4 START", 120, 150, 2); }
  if (spState == 2) { spr.setTextColor(C_RED); spr.drawString("GAME OVER", 120, 120, 4); }
  if (spState == 3) { spr.setTextColor(C_WARN); spr.drawString("PAUSED", 120, 120, 4); }
}
static void spFire() {
  if (spState != 1) return; float a = spAng * DEG_TO_RAD;
  for (int i = 0; i < 4; i++) if (!bullets[i].on) { Bullet& u = bullets[i]; u.on = true; u.x = spX + 10 * sinf(a); u.y = spY - 10 * cosf(a); u.vx = spVx + 180 * sinf(a); u.vy = spVy - 180 * cosf(a); u.life = 40; return; }
}
static bool spaceKey(int i) {
  if (i == 0) { if (spState == 1) { spThrust = true; spThrustUntil = millis() + 250; } else return true; }
  else if (i == 1) spFire();
  else if (i == 2) { if (spState == 1) { spX = (float)(20 + apRand() % 200); spY = (float)(20 + apRand() % 200); spVx = spVy = 0; if (apRand() % 6 == 0 && spLives) { spLives--; if (!spLives) spState = 2; } } }
  else if (i == 3) { if (spState == 1) spState = 3; else if (spState == 3) { spState = 1; spAt = millis(); } else spNew(); }
  else return false;
  needRedraw = true; return true;
}
static bool spaceDial(int s) { spAng = fmodf(spAng + s * 12.0f + 360.0f, 360.0f); needRedraw = true; return true; }
static bool spaceClick() { spFire(); return true; }

// ================================================================ screen 32: PC-rendered stream (ideas 39 / 40): the app sends changed 16x16 tiles, the pad keeps a frame buffer
static uint16_t* strBuf = nullptr; static uint32_t strFrames = 0, strTiles = 0, strBytes = 0, strLastAt = 0, strFirstAt = 0; static bool strActive = false;
static int b64val(char c) { return c >= 'A' && c <= 'Z' ? c - 'A' : c >= 'a' && c <= 'z' ? c - 'a' + 26 : c >= '0' && c <= '9' ? c - '0' + 52 : c == '+' ? 62 : c == '/' ? 63 : -1; }
static int b64Decode(const char* s, uint8_t* out, int maxn) {
  int acc = 0, bits = 0, n = 0;
  for (; *s && *s != '='; s++) { int v = b64val(*s); if (v < 0) return -1; acc = (acc << 6) | v; bits += 6; if (bits >= 8) { if (n >= maxn) return -1; out[n++] = (uint8_t)((acc >> (bits - 8)) & 255); bits -= 8; } }
  return n;
}
static bool strStart() {
  if (!strBuf) { strBuf = (uint16_t*)calloc(240 * 240, 2); if (!strBuf) return false; }
  strActive = true; strFrames = strTiles = strBytes = 0; strLastAt = 0; strFirstAt = millis(); return true;
}
static void strStop() { strActive = false; if (strBuf) { free(strBuf); strBuf = nullptr; } }
static bool strDecode(const uint8_t* d, int n, uint16_t* px) {
  if (n < 1) return false;
  if (d[0] == 0) { if (n != 3) return false; uint16_t c = (uint16_t)(d[1] | (d[2] << 8)); for (int i = 0; i < 256; i++) px[i] = c; }
  else if (d[0] == 1) { if (n != 513) return false; for (int i = 0; i < 256; i++) px[i] = (uint16_t)(d[1 + i * 2] | (d[2 + i * 2] << 8)); }
  else if (d[0] == 2) { int p = 0, i = 1; while (i + 2 < n + 0 && p < 256) { int run = d[i]; if (!run || p + run > 256) return false; uint16_t c = (uint16_t)(d[i + 1] | (d[i + 2] << 8)); for (int k = 0; k < run; k++) px[p++] = c; i += 3; } if (p != 256 || i != n) return false; }
  else return false;
  return true;
}
static bool strTileOk(int tx, int ty, const uint8_t* d, int n) { uint16_t px[256]; return strBuf && tx >= 0 && tx <= 14 && ty >= 0 && ty <= 14 && strDecode(d, n, px); }
static void strTile(int tx, int ty, const uint8_t* d, int n) {
  uint16_t px[256]; if (!strBuf || !strDecode(d, n, px)) return;
  for (int y = 0; y < 16; y++) memcpy(strBuf + (ty * 16 + y) * 240 + tx * 16, px + y * 16, 32);
}
static void sceneStream() {
  if (!strActive || !strBuf) { apFrame("STREAM"); spr.setTextColor(C_GRAY); spr.drawString("WAITING FOR THE APP", 120, 120, 2); return; }
  for (int y = 0; y < 240; y++) {
    int x = 0; const uint16_t* row = strBuf + y * 240;
    while (x < 240) { int e = x + 1; while (e < 240 && row[e] == row[x]) e++; if (e - x == 1) spr.drawPixel(x, y, row[x]); else spr.drawFastHLine(x, y, e - x, row[x]); x = e; }
  }
  if (!strLastAt || (uint32_t)(millis() - strLastAt) > 5000) { spr.fillRoundRect(60, 104, 120, 32, 8, C_DIM); spr.setTextDatum(MC_DATUM); spr.setTextColor(C_WARN); spr.drawString("NO SIGNAL", 120, 120, 2); }
}
static bool streamKey(int) { return false; }
static bool streamDial(int) { return false; }
static bool streamClick() { return false; }
static uint16_t streamInterval() { return strActive ? 60 : 1000; }

#if defined(DC_SIM) || defined(DC_NATIVE)
// test hooks: set a game's state directly (the native build and the simulator only)
static bool extTestHook(const char* op, JsonDocument& doc) {
  if (!strcmp(op, "blocks_set")) {
    JsonArrayConst rows = doc["rows"].as<JsonArrayConst>();
    if (!rows.isNull()) { int i = 0; for (JsonVariantConst r : rows) if (i < 20) bkRows[i++] = (uint16_t)(r.as<int>() & 0x3FF); }
    if (!doc["cur"].isNull()) { bkT = (int8_t)(doc["cur"]["t"] | 0); bkR = (int8_t)(doc["cur"]["r"] | 0); bkX = (int8_t)(doc["cur"]["x"] | 3); bkY = (int8_t)(doc["cur"]["y"] | 0); }
    if (!doc["next"].isNull()) bkNext = (int8_t)(doc["next"] | 0);
    if (!doc["state"].isNull()) bkState = (uint8_t)(doc["state"] | 1);
    bkFallAt = millis() + 100000; ack("app"); return true;
  }
  if (!strcmp(op, "g48_set")) { JsonArrayConst g = doc["grid"].as<JsonArrayConst>(); if (g.isNull() || g.size() != 16) { nack("grid"); return true; } int i = 0; for (JsonVariantConst v : g) g48[i++] = (uint16_t)v.as<int>(); g48Score = doc["score"] | 0; g48Over = !g48CanMove(); ack("app"); return true; }
  if (!strcmp(op, "g48_move")) { puzzleInit(); bool ch = g48Move(doc["dir"] | 0); JsonDocument d; d["ok"] = true; d["evt"] = "app"; d["moved"] = ch; sendDoc(d); return true; }
  if (!strcmp(op, "ms_set")) { JsonArrayConst m = doc["mines"].as<JsonArrayConst>(); if (m.isNull()) { nack("mines"); return true; } memset(msMine, 0, 64); for (JsonVariantConst v : m) msMine[v.as<int>() & 63] = 1; msPlaced = true; ack("app"); return true; }
  if (!strcmp(op, "ms_click")) { puzzleInit(); msCur = (uint8_t)((doc["cell"] | 0) & 63); msClick(); ack("app"); return true; }
  if (!strcmp(op, "ttt_set")) { JsonArrayConst b = doc["board"].as<JsonArrayConst>(); if (b.isNull() || b.size() != 9) { nack("board"); return true; } int i = 0; for (JsonVariantConst v : b) ttt[i++] = (int8_t)v.as<int>(); ttResult = 0; ttCheck(); ack("app"); return true; }
  if (!strcmp(op, "ttt_play")) { puzzleInit(); ttCur = (uint8_t)((doc["cell"] | 0) % 9); ttPlay(); ack("app"); return true; }
  if (!strcmp(op, "mm_set")) { JsonArrayConst c = doc["cards"].as<JsonArrayConst>(); if (c.isNull() || c.size() != 16) { nack("cards"); return true; } int i = 0; for (JsonVariantConst v : c) mmCard[i++] = (uint8_t)v.as<int>(); mmFace = mmDone = 0; mmA = mmB = -1; ack("app"); return true; }
  if (!strcmp(op, "space_set")) {
    if (!doc["ship"].isNull()) { spX = doc["ship"]["x"] | 120.0f; spY = doc["ship"]["y"] | 120.0f; spAng = doc["ship"]["a"] | 0.0f; spVx = spVy = 0; }
    if (!doc["rocks"].isNull()) { for (int i = 0; i < 12; i++) rocks[i].on = false; int i = 0; for (JsonVariantConst r : doc["rocks"].as<JsonArrayConst>()) if (i < 12) { rocks[i].x = r["x"] | 0.0f; rocks[i].y = r["y"] | 0.0f; rocks[i].vx = r["vx"] | 0.0f; rocks[i].vy = r["vy"] | 0.0f; rocks[i].size = (uint8_t)(r["s"] | 1); rocks[i].on = true; i++; } }
    if (!doc["inv"].isNull()) spInvUntil = millis() + (uint32_t)(doc["inv"] | 0);
    if (!doc["state"].isNull()) { spState = (uint8_t)(doc["state"] | 1); spAt = millis(); }
    ack("app"); return true;
  }
  return false;
}
#endif
static void appGameState(JsonObject o) {                                    // state of the games for cmd "app" / "state"
  if (mode == M_BLOCKS) { JsonArray r = o["rows"].to<JsonArray>(); for (int i = 0; i < 20; i++) r.add(bkRows[i]); o["t"] = bkT; o["r"] = bkR; o["x"] = bkX; o["y"] = bkY; o["next"] = bkNext; o["hold"] = bkHold; o["score"] = bkScore; o["lines"] = bkLines; o["level"] = bkLevel(); o["state"] = bkState; }
  else if (mode == M_PUZZLE) {
    puzzleInit(); o["page"] = pzPage;
    if (pzPage == 0) { JsonArray g = o["grid"].to<JsonArray>(); for (int i = 0; i < 16; i++) g.add(g48[i]); o["score"] = g48Score; o["over"] = g48Over; o["won"] = g48Won; }
    else if (pzPage == 1) { JsonArray c = o["cards"].to<JsonArray>(); for (int i = 0; i < 16; i++) c.add(mmCard[i]); o["face"] = mmFace; o["done"] = mmDone; o["moves"] = mmMoves; o["cur"] = mmCur; }
    else if (pzPage == 2) { JsonArray c = o["board"].to<JsonArray>(); for (int i = 0; i < 9; i++) c.add(ttt[i]); o["result"] = ttResult; o["cur"] = ttCur; o["ai_first"] = ttAiFirst; }
    else { JsonArray m = o["mines"].to<JsonArray>(), sh = o["shown"].to<JsonArray>(), fl = o["flags"].to<JsonArray>(); for (int i = 0; i < 64; i++) { m.add(msMine[i]); sh.add(msShown[i]); fl.add(msFlag[i]); } o["state"] = msState; o["cur"] = msCur; }
  }
  else if (mode == M_SPACE) {
    o["state"] = spState; o["lives"] = spLives; o["score"] = spScore; o["wave"] = spWave; o["x"] = spX; o["y"] = spY; o["a"] = spAng; o["vx"] = spVx; o["vy"] = spVy;
    JsonArray r = o["rocks"].to<JsonArray>(); for (int i = 0; i < 12; i++) if (rocks[i].on) { JsonObject q = r.add<JsonObject>(); q["x"] = rocks[i].x; q["y"] = rocks[i].y; q["s"] = rocks[i].size; }
    int nb = 0; for (int i = 0; i < 4; i++) if (bullets[i].on) nb++; o["bullets"] = nb;
  }
  else if (mode == M_STREAM) { o["active"] = strActive; o["frames"] = strFrames; o["tiles"] = strTiles; o["bytes"] = strBytes; }
}
