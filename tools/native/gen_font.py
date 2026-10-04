#!/usr/bin/env python3
"""Generates build/font_data.h: 1-bit glyph bitmaps (ASCII 32..126) for the six TFT_eSPI font numbers the sketch uses, rendered with DejaVu Sans via Pillow.
The host build's text is only an approximation of the real panel fonts (widths and heights are close, the shapes are not)."""
import os
import sys
from PIL import ImageFont

FONTS = {1: 8, 2: 14, 4: 22, 6: 40, 7: 40, 8: 64}          # TFT_eSPI font number -> pixel height used here
PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def main(out):
    lines = ["#pragma once", "#include <cstdint>", "struct NGlyph { uint8_t w, h; int8_t xo, yo; uint8_t adv; uint32_t off; };",
             "struct NFont { int px; int ascent; const NGlyph* g; const uint8_t* bits; };"]
    for num, px in FONTS.items():
        f = ImageFont.truetype(PATH, px)
        asc, desc = f.getmetrics()
        glyphs, bits = [], bytearray()
        for code in range(32, 127):
            ch = chr(code)
            adv = int(round(f.getlength(ch)))
            bb = f.getbbox(ch, anchor="ls") if ch != " " else (0, 0, 0, 0)
            x0, y0, x1, y1 = bb
            w, h = max(0, x1 - x0), max(0, y1 - y0)
            off = len(bits)
            if w and h:
                im = f.getmask(ch, mode="L")
                mw, mh = im.size
                data = list(im)
                for yy in range(h):
                    for xx in range(w):
                        v = data[yy * mw + xx] if xx < mw and yy < mh else 0
                        bits.append(1 if v > 90 else 0)
            glyphs.append((w, h, x0, y0, adv, off))
        lines.append(f"static const NGlyph g_glyphs{num}[95] = {{" + ",".join("{%d,%d,%d,%d,%d,%du}" % g for g in glyphs) + "};")
        lines.append(f"static const uint8_t g_bits{num}[] = {{" + ",".join(str(b) for b in bits) + ("0" if not bits else "") + "};")
        lines.append(f"static const int g_ascent{num} = {asc};")
    lines.append("static const NFont NFONTS[9] = {{0,0,0,0}, {%d,g_ascent1,g_glyphs1,g_bits1}, {%d,g_ascent2,g_glyphs2,g_bits2}, {0,0,0,0}, {%d,g_ascent4,g_glyphs4,g_bits4}, {0,0,0,0}, {%d,g_ascent6,g_glyphs6,g_bits6}, {%d,g_ascent7,g_glyphs7,g_bits7}, {%d,g_ascent8,g_glyphs8,g_bits8}};"
                 % (FONTS[1], FONTS[2], FONTS[4], FONTS[6], FONTS[7], FONTS[8]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write("\n".join(lines) + "\n")
    print("font data:", os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/font_data.h")
