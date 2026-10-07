"""Firmware 2.0 native tests, batch 3: falling blocks, puzzles (2048, memory, tic-tac-toe, minesweeper), the space game and the PC-rendered stream.  Run through fw20_test.py."""
import base64
import random
import struct
import time

from fw20_common import (appstate, click, clear, expect, fresh, frame, px, region, setmode, tap, turn)  # noqa: F401

M_BLOCKS, M_PUZZLE, M_SPACE, M_STREAM = 29, 30, 31, 32


def hook(e, op, **kw):
    return e.request(dict({"cmd": "app", "op": op}, **kw))


def key(e, k):
    return e.request({"cmd": "input", "k": k})


# ------------------------------------------------------------------------------------------------ falling blocks
def t_blocks(binary):
    e = fresh(binary, "blocks")
    setmode(e, M_BLOCKS)
    s = appstate(e); expect(s["state"] == 0 and s["score"] == 0, f"ready: {s}")
    hook(e, "seed", n=7)
    tap(e, 1); s = appstate(e); expect(s["state"] == 1 and 0 <= s["t"] <= 6 and 0 <= s["next"] <= 6, f"K1 starts: {s}")
    time.sleep(2.1); s2 = appstate(e); expect(s2["y"] >= s["y"] + 2 or s2["score"] > 0 or any(s2["rows"]), f"gravity pulls the piece down (800 ms per row): {s['y']} -> {s2['y']}")
    # walls: an I piece lying down fits columns 0..9
    hook(e, "blocks_set", rows=[0] * 20, cur={"t": 0, "r": 0, "x": 3, "y": 0}, state=1)
    turn(e, -10); expect(appstate(e)["x"] == 0, f"left wall: {appstate(e)['x']}")
    turn(e, 10); expect(appstate(e)["x"] == 6, f"right wall (4 cells wide): {appstate(e)['x']}")
    # rotation with a wall kick: a vertical I at the left edge cannot lie down in place
    hook(e, "blocks_set", rows=[0] * 20, cur={"t": 0, "r": 1, "x": -2, "y": 5}, state=1)
    click(e); s = appstate(e); expect(s["r"] == 2 and s["x"] == 0, f"rotates and kicks off the wall: {s}")
    hook(e, "blocks_set", rows=[0] * 20, cur={"t": 1, "r": 0, "x": 3, "y": 5}, state=1)
    click(e); expect(appstate(e)["r"] == 1, "the square rotates (no visible change)")
    # a rotation that does not fit anywhere is refused
    hook(e, "blocks_set", rows=[0x3FF] * 18 + [0, 0], cur={"t": 0, "r": 0, "x": 3, "y": 18}, state=1)
    click(e); expect(appstate(e)["r"] == 0, "no room to rotate")
    # line clear and scoring: row 19 only misses columns 0..3, an I piece fills them
    hook(e, "blocks_set", rows=[0] * 19 + [0x3F0], cur={"t": 0, "r": 0, "x": 0, "y": 18}, next=1, state=1)
    tap(e, 3); s = appstate(e)
    expect(s["lines"] == 1 and s["score"] == 100 and s["rows"] == [0] * 20, f"hard drop clears the row: {s}")
    expect(s["t"] == 1, f"the next piece (O) comes in: {s['t']}")
    # four at once
    rows = [0] * 16 + [0x3FE] * 4                                          # four rows that only miss column 0
    hook(e, "blocks_set", rows=rows, cur={"t": 0, "r": 1, "x": -2, "y": 0}, next=1, state=1)       # a vertical I at column 0 (its cells are the bitmap's third column)
    tap(e, 3); s = appstate(e)
    expect(s["lines"] == 5 and s["score"] == 100 + 800 + 16 * 2, f"tetris = 800 (+ 2 a row for the 16 rows of the hard drop): lines {s['lines']} score {s['score']}")
    # soft drop moves one row, hold swaps once
    hook(e, "blocks_set", rows=[0] * 20, cur={"t": 2, "r": 0, "x": 3, "y": 0}, next=1, state=1)
    tap(e, 2); expect(appstate(e)["y"] == 1, "K2 soft drop")
    tap(e, 4); s = appstate(e); expect(s["hold"] == 2 and s["t"] == 1, f"K4 holds the T and brings the next piece: {s}")
    tap(e, 4); expect(appstate(e)["t"] == 1, "a second hold in the same turn is refused")
    # pause
    tap(e, 1); expect(appstate(e)["state"] == 3, "K1 pauses"); y = appstate(e)["y"]; time.sleep(1.0); expect(appstate(e)["y"] == y, "paused: nothing falls")
    tap(e, 1); expect(appstate(e)["state"] == 1, "K1 resumes")
    # game over: the new piece does not fit
    hook(e, "blocks_set", rows=[0x3FE, 0x3FE] + [0] * 17 + [0x3F0], cur={"t": 0, "r": 0, "x": 0, "y": 18}, next=1, state=1)
    tap(e, 3); expect(appstate(e)["state"] == 2, "game over when the next piece cannot enter")
    tap(e, 3); tap(e, 2); expect(appstate(e)["state"] == 2, "keys do nothing in game over except K1")
    tap(e, 1); expect(appstate(e)["state"] == 1 and appstate(e)["rows"] == [0] * 20 and appstate(e)["score"] == 0, "K1 starts a new game")
    f = frame(e); expect(region(f, 68, 34, 152, 200) > 100, "the well and the piece are drawn")
    # level: 10 lines = level 1 speeds the game up
    e.close()


# ------------------------------------------------------------------------------------------------ puzzles
def ref_line(line):
    out = []; score = 0; merged = False
    for v in line:
        if not v:
            continue
        if out and out[-1] == v and not merged:
            out[-1] *= 2; score += out[-1]; merged = True
        else:
            out.append(v); merged = False
    return out + [0] * (4 - len(out)), score


def ref_move(grid, d):
    g = [grid[i * 4:(i + 1) * 4] for i in range(4)]
    score = 0; new = [[0] * 4 for _ in range(4)]
    for k in range(4):
        if d == 0:
            line = g[k]
        elif d == 1:
            line = g[k][::-1]
        elif d == 2:
            line = [g[j][k] for j in range(4)]
        else:
            line = [g[j][k] for j in range(3, -1, -1)]
        res, sc = ref_line(line); score += sc
        for j in range(4):
            if d == 0:
                new[k][j] = res[j]
            elif d == 1:
                new[k][3 - j] = res[j]
            elif d == 2:
                new[j][k] = res[j]
            else:
                new[3 - j][k] = res[j]
    flat = [v for r in new for v in r]
    return flat, score, flat != list(grid)


def t_2048(binary):
    e = fresh(binary, "g2048")
    setmode(e, M_PUZZLE)
    s = appstate(e); expect(s["page"] == 0 and sum(1 for v in s["grid"] if v) == 2 and set(s["grid"]) <= {0, 2, 4}, f"new game: two tiles {s}")
    rng = random.Random(5); hook(e, "seed", n=11)
    bad = 0
    for n in range(500):
        grid = [rng.choice([0, 0, 0, 2, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]) for _ in range(16)]
        d = rng.randrange(4)
        hook(e, "g48_set", grid=grid, score=1000)
        r = hook(e, "g48_move", dir=d)
        s = appstate(e); want, sc, moved = ref_move(grid, d)
        if r["moved"] != moved:
            bad += 1; expect(False, f"moved flag {grid} dir {d}: pad {r['moved']} ref {moved}"); continue
        if not moved:
            ok = s["grid"] == grid and s["score"] == 1000
        else:
            diff = [i for i in range(16) if s["grid"][i] != want[i]]
            ok = len(diff) == 1 and want[diff[0]] == 0 and s["grid"][diff[0]] in (2, 4) and s["score"] == 1000 + sc
        if not ok:
            bad += 1; expect(False, f"2048 move {grid} dir {d}: pad {s['grid']} score {s['score']}, ref {want} +{sc}")
        if bad > 3:
            break
    expect(bad == 0, "500 random moves match the reference implementation")
    hook(e, "g48_set", grid=[1024, 1024] + [0] * 14); hook(e, "g48_move", dir=0); s = appstate(e)
    expect(s["won"] and s["grid"][0] == 2048, f"2048 reached: {s}")
    hook(e, "g48_set", grid=[2, 4, 2, 4, 4, 2, 4, 2, 2, 4, 2, 4, 4, 2, 4, 8]); s = appstate(e); expect(s["over"], "no move left = game over")
    hook(e, "g48_set", grid=[2, 4, 2, 4, 4, 2, 4, 2, 2, 4, 2, 4, 4, 2, 4, 4]); expect(not appstate(e)["over"], "one merge left: not over")
    # the real controls: dial = left / right, K1 / K2 = up / down, K4 = undo, K3 = new game
    hook(e, "g48_set", grid=[2, 0, 0, 0] + [0] * 12, score=0)
    turn(e, 1); s = appstate(e); expect(s["grid"][3] == 2, f"dial right slides the tile to the end: {s['grid']}")
    tap(e, 2); expect(appstate(e)["grid"][15] == 2, "K2 down")
    tap(e, 1); expect(appstate(e)["grid"][3] == 2, "K1 up")
    before = appstate(e)["grid"]; tap(e, 2); tap(e, 4); expect(appstate(e)["grid"] == before, "K4 undoes the last move")
    tap(e, 3); s = appstate(e); expect(sum(1 for v in s["grid"] if v) == 2 and s["score"] == 0, "K3 new game")
    f = frame(e); expect(region(f, 48, 52, 192, 196) > 5000, "the board is drawn")
    e.close()


def t_memory(binary):
    e = fresh(binary, "memory")
    setmode(e, M_PUZZLE); tap(e, 5); s = appstate(e); expect(s["page"] == 1 and len(s["cards"]) == 16 and sorted(s["cards"]) == sorted(list(range(8)) * 2), f"8 pairs: {s}")
    cards = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7]
    hook(e, "mm_set", cards=cards)
    click(e); s = appstate(e); expect(s["face"] == 1 and s["moves"] == 0, f"one card up: {s}")
    turn(e, 1); click(e); s = appstate(e); expect(s["done"] == 3 and s["moves"] == 1, f"a pair stays: {s}")
    turn(e, 1); click(e); turn(e, 2); click(e)                       # cards 2 and 4 = 1 and 2: no match
    s = appstate(e); expect(s["face"] & 0b10100 == 0b10100 and s["moves"] == 2, f"two different cards face up: {s}")
    turn(e, 1); click(e); expect(appstate(e)["face"] == s["face"], "a third card is refused while two are up")
    time.sleep(1.2); s = appstate(e); expect(s["face"] == 3 and s["done"] == 3, f"they turn back after a moment: {s}")
    turn(e, -s["cur"]); click(e); expect(appstate(e)["face"] == 3, "a card that is already done is not flipped again")
    tap(e, 3); s = appstate(e); expect(s["moves"] == 0 and s["done"] == 0 and sorted(s["cards"]) == sorted(cards), "K3 reshuffles")
    hook(e, "mm_set", cards=cards)
    for pair in range(8):
        for c in (2 * pair, 2 * pair + 1):
            cur = appstate(e)["cur"]; turn(e, (c - cur) % 16 if (c - cur) % 16 <= 8 else (c - cur) % 16 - 16); click(e)
    s = appstate(e); expect(s["done"] == 0xFFFF and s["moves"] == 8, f"solved in 8 moves: {s}")
    e.close()


def t_tictactoe(binary):
    e = fresh(binary, "ttt")
    setmode(e, M_PUZZLE); tap(e, 5); tap(e, 5); s = appstate(e); expect(s["page"] == 2 and s["board"] == [0] * 9, f"{s}")
    # the pad never loses: 300 games against random play (hook: cell numbers), plus it takes the win and blocks
    rng = random.Random(3); wins = draws = pad = 0
    for g in range(300):
        tap(e, 3) if False else key(e, 3)
        if rng.random() < 0.3:
            key(e, 4)                                                   # the pad starts
            key(e, 4)
            key(e, 4)
        for _ in range(9):
            s = appstate(e)
            if s["result"]:
                break
            free = [i for i, v in enumerate(s["board"]) if v == 0]
            hook(e, "ttt_play", cell=rng.choice(free))
        s = appstate(e)
        if s["result"] == 1:
            wins += 1
        elif s["result"] == 2:
            pad += 1
        else:
            draws += 1
        if s["ai_first"]:
            key(e, 4)
    expect(wins == 0, f"a perfect pad never loses: {wins} wins for the player in 300 games ({pad} pad wins, {draws} draws)")
    expect(pad > 100, f"and wins most games against random play: {pad}")
    key(e, 3)
    hook(e, "ttt_set", board=[2, 2, 0, 1, 0, 0, 1, 0, 0]); hook(e, "ttt_play", cell=4)    # X threatens 3 4 5 but the pad (O) can win at 2 and does
    s = appstate(e); expect(s["board"][2] == 2 and s["result"] == 2, f"takes the win instead of blocking: {s}")
    hook(e, "ttt_set", board=[1, 0, 0, 0, 2, 0, 0, 0, 0]); hook(e, "ttt_play", cell=1)
    s = appstate(e); expect(s["board"][2] == 2, f"blocks X at 0 1 by playing 2: {s['board']}")
    hook(e, "ttt_set", board=[1, 0, 0, 0, 0, 0, 0, 0, 0]); hook(e, "ttt_play", cell=8)
    s = appstate(e); expect(s["board"][4] == 2, f"answers a corner with the centre: {s['board']}")
    # the real controls
    key(e, 3); turn(e, -4); click(e); s = appstate(e)
    expect(s["board"][0] == 1 and s["board"].count(2) == 1, f"dial moves the cursor, click plays, the pad answers: {s}")
    key(e, 3)
    key(e, 4); s = appstate(e); expect(s["ai_first"] and s["board"].count(2) == 1, f"K4: the pad starts: {s}")
    e.close()


def flood(mines, start):
    shown = set(); stack = [start]
    def count(i):
        x, y = i % 8, i // 8
        return sum(1 for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dx or dy) and 0 <= x + dx < 8 and 0 <= y + dy < 8 and (y + dy) * 8 + x + dx in mines)
    while stack:
        i = stack.pop()
        if i in shown:
            continue
        shown.add(i)
        if count(i) == 0:
            x, y = i % 8, i // 8
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if (dx or dy) and 0 <= x + dx < 8 and 0 <= y + dy < 8:
                        stack.append((y + dy) * 8 + x + dx)
    return shown


def t_minesweeper(binary):
    e = fresh(binary, "mines")
    setmode(e, M_PUZZLE); tap(e, 5); tap(e, 5); tap(e, 5)
    s = appstate(e); expect(s["page"] == 3 and s["state"] == 0, f"{s}")
    mines = {63, 62, 55}
    hook(e, "ms_set", mines=sorted(mines)); hook(e, "ms_click", cell=0); s = appstate(e)
    want = flood(mines, 0); got = {i for i, v in enumerate(s["shown"]) if v}
    expect(got == want and s["state"] == 0, f"flood fill from a corner: {len(got)} cells vs {len(want)}")
    hook(e, "ms_click", cell=63); expect(appstate(e)["state"] == 2, "stepping on a mine loses")
    hook(e, "ms_click", cell=10); tap(e, 3)
    s = appstate(e); expect(s["state"] == 0 and sum(s["shown"]) == 0, "K3 new game")
    hook(e, "ms_set", mines=[0, 9, 18, 27, 36, 45, 54, 63, 7, 56])
    for i in range(64):
        if i not in (0, 9, 18, 27, 36, 45, 54, 63, 7, 56):
            hook(e, "ms_click", cell=i)
    expect(appstate(e)["state"] == 1, "all safe cells revealed = cleared")
    # flags: K1 flags the cell under the cursor and a flagged cell is not revealed
    tap(e, 3); hook(e, "ms_set", mines=[63]); tap(e, 1); s = appstate(e); expect(s["flags"][s["cur"]] == 1, "K1 flags"); c = s["cur"]
    click(e); s = appstate(e); expect(s["shown"][c] == 0, "a flagged cell is not revealed")
    tap(e, 1); expect(appstate(e)["flags"][c] == 0, "K1 again removes the flag")
    # first click is always safe: 200 games, mines never touch the clicked cell or its neighbours
    rng = random.Random(9)
    for g in range(200):
        key(e, 3); c = rng.randrange(64)
        hook(e, "seed", n=rng.randrange(1, 1 << 30)); hook(e, "ms_click", cell=c); s = appstate(e)
        near = {(c // 8 + dy) * 8 + (c % 8 + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if 0 <= c // 8 + dy < 8 and 0 <= c % 8 + dx < 8}
        mm = {i for i, v in enumerate(s["mines"]) if v}
        if not (len(mm) == 10 and not (mm & near) and s["state"] == 0):
            expect(False, f"first click at {c}: mines {sorted(mm)}, state {s['state']}"); break
    else:
        expect(True, "")
    e.close()


# ------------------------------------------------------------------------------------------------ space game
def t_space(binary):
    e = fresh(binary, "space")
    setmode(e, M_SPACE)
    s = appstate(e); expect(s["state"] == 0, f"ready {s}")
    hook(e, "seed", n=5); tap(e, 4); s = appstate(e)
    expect(s["state"] == 1 and s["lives"] == 3 and s["wave"] == 1 and len(s["rocks"]) == 3, f"K4 starts: {s}")
    turn(e, 3); expect(abs(appstate(e)["a"] - 36) < 0.01, "the dial turns the ship 12 degrees a click")
    hook(e, "space_set", ship={"x": 120, "y": 120, "a": 0}, rocks=[{"x": 120, "y": 70, "s": 1}, {"x": 20, "y": 20, "s": 3}], inv=0)
    key(e, 2); time.sleep(0.9); s = appstate(e)
    expect(s["score"] == 100 and len(s["rocks"]) == 1, f"a shot kills a small rock (100 points): {s}")
    hook(e, "space_set", rocks=[{"x": 120, "y": 70, "s": 3}, {"x": 20, "y": 200, "s": 3}])
    key(e, 2); time.sleep(0.9); s = appstate(e)
    expect(s["score"] == 120 and sorted(r["s"] for r in s["rocks"]) == [2, 2, 3], f"a big rock splits into two medium ones (20 points): {s['score']} {s['rocks']}")
    hook(e, "space_set", ship={"x": 120, "y": 120, "a": 180}, rocks=[{"x": 10, "y": 10, "s": 3}])
    key(e, 1); time.sleep(0.5); s = appstate(e); expect(s["y"] > 125 and abs(s["x"] - 120) < 1, f"K1 thrust accelerates the way the nose points (down): {s['x']:.1f},{s['y']:.1f}")
    for lives in (2, 1, 0):                                             # hit the ship three times
        hook(e, "space_set", ship={"x": 120, "y": 120, "a": 0}, rocks=[{"x": 120, "y": 120, "s": 1}], inv=0)
        time.sleep(0.25); s = appstate(e); expect(s["lives"] == lives, f"collision: {lives} lives left: {s['lives']}")
    expect(s["state"] == 2, "no lives left = game over")
    tap(e, 4); expect(appstate(e)["state"] == 1 and appstate(e)["lives"] == 3 and appstate(e)["score"] == 0, "K4 restarts")
    hook(e, "space_set", ship={"x": 120, "y": 120, "a": 0}, rocks=[{"x": 120, "y": 120, "s": 1}], inv=2000)
    time.sleep(0.3); expect(appstate(e)["lives"] == 3, "invulnerable right after a start / hit")
    hook(e, "space_set", rocks=[{"x": 239, "y": 20, "vx": 40, "vy": 0, "s": 1}], inv=2000); time.sleep(0.6)
    expect(appstate(e)["rocks"][0]["x"] < 30, "the field wraps around")
    hook(e, "space_set", rocks=[{"x": 120, "y": 70, "s": 1}], ship={"x": 120, "y": 120, "a": 0}); key(e, 2); time.sleep(1.0); s = appstate(e)
    expect(s["wave"] == 2 and len(s["rocks"]) == 4, f"clearing the field starts the next wave: {s['wave']} / {len(s['rocks'])}")
    tap(e, 4); expect(appstate(e)["state"] == 3, "K4 pauses"); x = appstate(e)["rocks"][0]["x"]; time.sleep(0.5); expect(appstate(e)["rocks"][0]["x"] == x, "paused: nothing moves")
    tap(e, 4); expect(appstate(e)["state"] == 1, "resumes")
    for _ in range(8):
        key(e, 2)
    expect(appstate(e)["bullets"] <= 4, "at most four bullets")
    f = frame(e); expect(region(f, 0, 0, 240, 240) > 100, "the field is drawn")
    e.close()


# ------------------------------------------------------------------------------------------------ PC-rendered stream
def rgb565_le(c):
    return struct.pack("<H", c)


def solid(c):
    return base64.b64encode(b"\x00" + rgb565_le(c)).decode()


def raw_tile(pixels):
    return base64.b64encode(b"\x01" + b"".join(rgb565_le(c) for c in pixels)).decode()


def rle_tile(runs):
    return base64.b64encode(b"\x02" + b"".join(bytes([n]) + rgb565_le(c) for n, c in runs)).decode()


def t_stream(binary):
    e = fresh(binary, "stream")
    expect(e.request({"cmd": "tiles", "t": [[0, 0, solid(1)]]}).get("err") == "not_streaming", "tiles are refused before the stream starts")
    r = e.request({"cmd": "stream", "op": "start"}); expect(r["ok"] and r["active"], f"start {r}")
    time.sleep(0.2)
    f0 = frame(e); expect(region(f0, 0, 0, 240, 240) > 0, "'no signal' is shown at first")
    r = e.request({"cmd": "tiles", "t": [[0, 0, solid(0xF800)], [14, 14, solid(0x07E0)]], "end": True}); expect(r["ok"] and r["n"] == 2, f"tiles {r}")
    grad = [(x * 2) << 11 | (y * 4) << 5 | (x + y) for y in range(16) for x in range(16)]
    expect(e.request({"cmd": "tiles", "t": [[3, 2, raw_tile(grad)]]})["ok"], "raw tile")
    runs = [(100, 0x001F), (100, 0xFFE0), (56, 0xF81F)]
    expect(e.request({"cmd": "tiles", "t": [[5, 5, rle_tile(runs)]], "end": True})["ok"], "rle tile")
    time.sleep(0.3); f = frame(e)
    expect(px(f, 5, 5) == 0xF800 and px(f, 15, 15) == 0xF800 and px(f, 16, 0) == 0, "solid tile at 0,0 (and nothing next to it)")
    expect(px(f, 235, 235) == 0x07E0, "solid tile at 14,14")
    bad = [(x, y) for y in range(16) for x in range(16) if px(f, 48 + x, 32 + y) != grad[y * 16 + x]]
    expect(not bad, f"the raw tile is pixel exact ({len(bad)} wrong)")
    want = [0x001F] * 100 + [0xFFE0] * 100 + [0xF81F] * 56
    bad = [(x, y) for y in range(16) for x in range(16) if px(f, 80 + x, 80 + y) != want[y * 16 + x]]
    expect(not bad, f"the RLE tile is pixel exact ({len(bad)} wrong)")
    st = e.request({"cmd": "stream"}); expect(st["frames"] == 2 and st["tiles"] == 4 and st["bytes"] > 500, f"stats {st}")
    # validation: nothing is drawn when one tile of a request is bad (all or nothing)
    before = frame(e)
    for name, t in (("tx out of range", [[15, 0, solid(1)]]), ("ty negative", [[0, -1, solid(1)]]), ("not base64", [[0, 0, "!!!!"]]), ("short raw", [[0, 0, base64.b64encode(b"\x01\x00\x00").decode()]]),
                    ("bad format", [[0, 0, base64.b64encode(b"\x07\x00\x00").decode()]]), ("rle too long", [[0, 0, rle_tile([(200, 1), (100, 2)])]]), ("rle too short", [[0, 0, rle_tile([(100, 1)])]]),
                    ("rle zero run", [[0, 0, rle_tile([(0, 1), (255, 2), (1, 3)])]]), ("not an array", [{"x": 1}]), ("two items", [[0, 0]]), ("13 tiles", [[0, 0, solid(1)]] * 13), ("empty", [])):
        r = e.request({"cmd": "tiles", "t": t}); expect(not r.get("ok"), f"{name} rejected")
    r = e.request({"cmd": "tiles", "t": [[7, 7, solid(0x1234)], [99, 0, solid(1)]]}); expect(not r.get("ok"), "one bad tile")
    time.sleep(0.3); after = frame(e); expect(after == before, "a request with a bad tile draws nothing at all")
    # speed: 12 full frames of 225 flat tiles; the loopback must carry well over 8 frames a second
    cols = [0x0000, 0xFFFF]
    import json
    with e.lock:
        e.msgs.clear()
    t0 = time.time(); sent = 0
    for fr in range(12):
        for k in range(0, 225, 12):
            tl = [[(k + j) % 15, (k + j) // 15, solid(cols[fr & 1])] for j in range(12) if k + j < 225]
            e.send({"cmd": "tiles", "t": tl, "end": k + 12 >= 225, "id": 1000 + sent}); sent += 1
    got = 0
    while got < sent and time.time() - t0 < 30:
        time.sleep(0.005)
        got += len(e.take("tiles"))
    dt = time.time() - t0; fps = 12 / dt
    expect(got == sent, f"{got} of {sent} requests answered")
    expect(fps >= 8, f"{fps:.1f} full frames per second of flat tiles")
    st = e.request({"cmd": "stream"}); expect(st["frames"] >= 14, f"frame counter {st}")
    # the stream stops when you leave the screen
    setmode(e, 1); time.sleep(0.2)
    expect(e.request({"cmd": "stream"})["active"] is False, "leaving the screen frees the buffer")
    expect(e.request({"cmd": "tiles", "t": [[0, 0, solid(1)]]}).get("err") == "not_streaming", "and stops accepting tiles")
    for _ in range(10):
        e.request({"cmd": "stream", "op": "start"}); e.request({"cmd": "stream", "op": "stop"})
    expect(e.request({"cmd": "stream", "op": "bogus"}).get("err") == "op", "op validated")
    e.close()
