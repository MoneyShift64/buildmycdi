#!/usr/bin/env python3
"""build_front.py - the original's menu screens (the hi-res H*.PCX pictures in KART.DAT)
for src/front.c:

    python3 tools/build_front.py        (build_dc.sh runs it when data/fe_HM1.bin is missing)

Reads extracted/KART (the menu pictures, fonts and menu sounds; see tools/unkart.c).
For each picture set S (one screen's PIC0..n, sharing PIC0's palette) writes:

    data/fe_S.pal       the 256 colours (ARGB1555); colour 0 is see-through on sprites
    data/fe_S_bg0.tex   the background (PIC1, or the screen's own), left 512 x 480 in 512 x 512
    data/fe_S_bg1.tex   ... and the right 128 x 480 in 128 x 512
    data/fe_S.tex       the sprites: the six fonts cut from PIC0 (font_create 0x12a54) and the
                        screen's icons, packed
    data/fe_S.bin       "MKFE", then 6 fonts x 96 characters (32..127) x (x, y, w, h, advance)
                        as u16, then u16 sprites + (x, y, w, h) each, then u16 ring entries +
                        (s16 x, s16 y, u16 scale x 1000) each: a turning icon ring's path
                        (menu_circle_setup 0x1169c, traced from the ROT picture)
Also data/MENUSEL.pcm etc.: the menu sounds (SELSND1, DESELECT, QUIT).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_assets as b                    # noqa: E402

KART = b.KART
OUT = b.OUT

# the six fonts (all PIC0 sheets share this layout): origin, cell, charmap rows, kern, width rule
FONTS = [
    ("A", 0, 0, 36, 19, ["ABCDEFGHIJKLMNOPQ", "RSTUVWXYZ -", "1234567890$.:;,"], 3, None),
    ("B", 0, 57, 36, 19, ["abcdefghijklmnopq", "rstuvwxyz ", "ABCDEFGHIJKLMNOPQ", "RSTUVWXYZ!@#$%^&*",
                          ")_+(:\"|<>?-=[];'\\", ",./1234567890"], 0, 0),
    ("C", 0, 180, 25, 15, ["ABCDEFGHIJKLMNOPQRSTUVWXY", "Z 1234567890$:.,;'#/\\[]-=", "_~"], 1, None),
    ("D", 0, 270, 50, 25, ["ABCDEFGHIJKL", "MNOPQRSTUVWX", "YZ 123456789", "0$"], 7, None),
    ("E", 0, 375, 20, 20, ["ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef", "ghijklmnopqrstuvwxyz ;:.,$!&'?%+",
                           "-()1234567890"], 0, 0),
    ("F", 0, 436, 20, 10, ["ABCDEFGHIJKLMNOPQRSTUVWXYZ 12345", "67890$:.,;'#/\\[]-=_+~"], 4, None),
]

# the track maps on the track-select pictures: (slot, glyph) per track (K.EXE 0xb5888) and
# each slot's glyph rects (x0, y0, x1, y1) (load_anim_file, screen 0x1b)
MAP_OF = [(3, 5), (3, 2), (3, 3), (3, 4), (2, 2), (2, 3), (3, 0), (3, 1),
          (2, 0), (2, 1), (2, 4), (2, 5), (4, 1), (4, 0), (4, 2), (4, 3)]
MAP_RECTS = {
    2: [(0, 0, 211, 220), (207, 10, 427, 218), (423, 2, 639, 230), (0, 225, 206, 459), (205, 243, 412, 423), (408, 229, 607, 434)],
    3: [(0, 0, 205, 209), (200, 3, 421, 226), (418, 5, 640, 226), (3, 225, 227, 454), (222, 228, 429, 457), (426, 224, 639, 425)],
    4: [(0, 0, 237, 226), (296, 3, 535, 236), (9, 235, 216, 460), (286, 230, 519, 462)],
}
LETTER = "EUFMG BA"
LETTER_OF = [0, 0, 2, 2, 4, 4, 1, 1, 6, 6, 3, 3, 1, 1, 7, 7]     # K.EXE 0xba168


def load(name):
    w, h, px, pal = b.read_pcx(os.path.join(KART, name + ".PCX"))
    px = bytearray(b"".join(bytes(r) for r in px) if isinstance(px, list) else px)
    return w, h, px, pal


def crop(px, W, x, y, w, h):
    return [bytes(px[(y + j) * W + x:(y + j) * W + x + w]) for j in range(h)]


def fonts(px):
    """-> list of 6 fonts: (kern, {char: (sprite rows, width)})"""
    out = []
    for _, x0, y0, cw, ch, rows, kern, extra in FONTS:
        g = {}
        for r, s in enumerate(rows):
            for c, chh in enumerate(s):
                gx, gy = x0 + c * cw, y0 + r * ch
                top = px[gy * 640 + gx:gy * 640 + gx + cw]
                if 1 in top:
                    wd = top.index(1)
                elif extra is None:
                    wd = cw
                else:
                    m = 0
                    for yy in range(ch):
                        row = px[(gy + yy) * 640 + gx:(gy + yy) * 640 + gx + cw]
                        nz = [i for i, v in enumerate(row) if v]
                        if nz:
                            m = max(m, nz[-1] + 1)
                    wd = m + extra
                rows_px = [bytes(0 if v == 1 else v for v in r_) for r_ in crop(px, 640, gx, gy, cw, ch)]
                g[chh] = (rows_px, wd, 0)
        out.append((kern, g))
    return out


def trace(name, step, dx=0, dy=0):
    """the ROT picture's path -> [(x, y, scale)] (menu_circle_setup 0x1169c)"""
    w, h, px, _ = load(name)
    W = w
    mx, pos = 0, 0
    for i, v in enumerate(px):
        if v > mx and v != 0xff:
            mx, pos = v, i
    path, marks = [], [(0, mx)]
    cur, n = pos, 0
    while True:
        px[cur] = 0
        path.append((cur % W, cur // W))
        n += 1
        nb = None
        for off in (-(W - 1), -W, -(W + 1), -1, 1, W - 1, W, W + 1):
            if 0 <= cur + off < len(px) and px[cur + off] != 0:
                nb = cur + off
                break
        if nb is None:
            marks.append((n, mx))
            break
        v = px[nb]
        if v == 1:
            break
        cur = nb
        if v != 0xff:
            marks.append((n, v))
    tab = []
    for k in range(1, len(marks)):
        n0, v0 = marks[k - 1]
        n1, v1 = marks[k]
        for j in range(step):
            i = n0 + (n1 - n0) * j // step
            x, y = path[min(i, len(path) - 1)]
            tab.append((x + dx, y + dy, (v0 + (v1 - v0) * j / step) / 254))
    return tab


def tex_out(name, w, h, idx, bank):
    b.write_tex8(os.path.join(OUT, name), w, h, idx, bank, 0x2000)


def build_set(S, bgname, sprites, ring=None, pal_from=None, use="ABCDEF", page2=None):
    """sprites: [(picture name, (x, y, w, h))] in id order (w = 0: none); use: the fonts the
    screen draws with (the others are left out); page2: sprites from this one on go into a
    second texture, fe_S_2.tex (their x has 0x8000 added)"""
    W0, H0, p0, pal = load(pal_from or (S + "PIC0"))
    open(os.path.join(OUT, "fe_%s.pal" % S), "wb").write(struct.pack("<256H", *[b.argb1555(c) for c in pal]))
    # background: 640 x 480 in two textures
    w, h, bg, _ = load(bgname)
    if (w, h) != (640, 480):              # (the 320 x 200 Virgin logo: scaled up)
        bg = bytearray(bg[(y * h // 480) * w + (x * w // 640)] for y in range(480) for x in range(640))
    left = bytearray(512 * 512)
    right = bytearray(128 * 512)
    for y in range(480):
        left[y * 512:y * 512 + 512] = bg[y * 640:y * 640 + 512]
        right[y * 128:y * 128 + 128] = bg[y * 640 + 512:y * 640 + 640]
    tex_out("fe_%s_bg0.tex" % S, 512, 512, list(left), 2)
    tex_out("fe_%s_bg1.tex" % S, 128, 512, list(right), 2)
    # sprites: fonts first, then the icons
    items = []
    fl = fonts(p0) if pal_from is None else None
    font_ix = []
    if fl:
        for fi, (kern, g) in enumerate(fl):
            ix = {}
            for chh, (rows_px, wd, mark) in g.items():
                vis = max((max((i for i, v in enumerate(r_) if v), default=-1) for r_ in rows_px), default=-1) + 1
                if FONTS[fi][0] not in use or chh == " " or vis == 0:
                    ix[chh] = (None, wd - kern, 0)
                    continue
                ix[chh] = (len(items), wd - kern, mark)
                items.append((vis, len(rows_px), [r_[:vis] for r_ in rows_px]))
            font_ix.append((kern, ix))
    pics = {}
    spr_ix = []
    items2 = []
    for si, (pic, (x, y, sw, sh)) in enumerate(sprites):
        if sw <= 0:
            spr_ix.append(None)
            continue
        if pic not in pics:
            pics[pic] = load(pic)
        pw, ph, ppx, _ = pics[pic]
        sw, sh = min(sw, pw - x), min(sh, ph - y)
        if page2 is not None and si >= page2:
            spr_ix.append(("2", len(items2)))
            items2.append((sw, sh, crop(ppx, pw, x, y, sw, sh)))
        else:
            spr_ix.append(len(items))
            items.append((sw, sh, crop(ppx, pw, x, y, sw, sh)))

    def fit(its, fn):
        for size in ((256, 256), (512, 256), (512, 512), (1024, 512), (1024, 1024)):
            try:
                atlas, rects = pack(its, *size)
                break
            except ValueError:
                continue
        else:
            sys.exit("menu sprites for %s don't fit" % S)
        tex_out(fn, size[0], size[1], atlas, 3)
        return rects, size
    rects, size = fit(items, "fe_%s.tex" % S)
    if items2:
        rects2, size2 = fit(items2, "fe_%s_2.tex" % S)
        spr_ix = [(None if i is None else ("p2", i[1]) if isinstance(i, tuple) else i) for i in spr_ix]
    out = bytearray(b"MKFE")
    for f in range(6):
        if not font_ix:
            out += bytes(96 * 10)
            continue
        kern, ix = font_ix[f]
        for c in range(32, 128):
            e = ix.get(chr(c))
            if e is None:
                out += struct.pack("<5H", 0, 0, 0, 0, 0)
            elif e[0] is None:            # (blank: just an advance)
                out += struct.pack("<5H", 0, 0, 0, FONTS[f][4], max(e[1], 0))
            else:
                x, y, w_, h_ = rects[e[0]]
                out += struct.pack("<5H", x, y, w_, h_, max(e[1], 0))
    out += struct.pack("<H", len(spr_ix))
    for i in spr_ix:
        if i is None:
            out += struct.pack("<4H", 0, 0, 0, 0)
        elif isinstance(i, tuple):
            x, y, w_, h_ = rects2[i[1]]
            out += struct.pack("<4H", x | 0x8000, y, w_, h_)
        else:
            out += struct.pack("<4H", *rects[i])
    ring = ring or []
    out += struct.pack("<H", len(ring))
    for x, y, s in ring:
        out += struct.pack("<hhH", int(x), int(y), int(s * 1000))
    open(os.path.join(OUT, "fe_%s.bin" % S), "wb").write(out)
    print("menu set %-6s %2d sprites  %dx%d" % (S, len(spr_ix), size[0], size[1]))


def pack(items, W, H):
    order = sorted(range(len(items)), key=lambda i: -items[i][1])
    rects = [None] * len(items)
    x = y = shelf = 0
    for i in order:
        w, h, _ = items[i]
        if x + w > W:
            x, y, shelf = 0, y + shelf + 1, 0
        if w > W or y + h > H:
            raise ValueError
        rects[i] = (x, y, w, h)
        x += w + 1
        shelf = max(shelf, h)
    tex = [0] * (W * H)
    for i, (w, h, rows) in enumerate(items):
        sx, sy = rects[i][0], rects[i][1]
        for yy in range(h):
            tex[(sy + yy) * W + sx:(sy + yy) * W + sx + w] = rows[yy]
    return tex, rects


def r2(x0, y0, x1, y1):
    return (x0, y0, x1 - x0, y1 - y0)


def main():
    # main menu: the five icons (menu_main 0x26cb8): NEW GAME, CONFIGURE, LOAD, HIGH SCORES, QUIT
    build_set("HM1", "HM1PIC1", use="ADF", sprites=[("HM1PIC3", (0, 0, 331, 224)), ("HM1PIC2", (0, 200, 300, 240)),
                                 ("HM1PIC2", (350, 0, 260, 125)), ("HM1PIC2", (350, 215, 260, 215)),
                                 ("HM1PIC2", (0, 0, 210, 200))], ring=trace("HM1ROT", 100))
    build_set("HM3", "HM3PIC1", [], use="CDF")
    # configure race: the 16 kart pictures (4 x 4 of 160 x 100)
    build_set("HM3B", "HM3BPIC1", [("HM3BPIC2", ((i % 4) * 160, (i // 4) * 100, 160, 100)) for i in range(16)], use="CDF")
    # track select: 9 flags (3 x 3 of 180 x 105), then the 16 tracks' maps
    spr = [("HTRPIC5", ((k % 3) * 180, (k // 3) * 105, 180, 105)) for k in range(9)]
    for t in range(16):
        slot, gi = MAP_OF[t]
        spr.append(("HTRPIC%d" % slot, r2(*MAP_RECTS[slot][gi])))
    build_set("HTR", "HTRPIC1", spr, trace("HTRROT", 20, 20, 117), use="DCB", page2=9)
    # next circuit: one set per country letter, the maps of its tracks (ids = track numbers)
    for li, L in enumerate(LETTER):
        if L == " ":
            continue
        spr = []
        for t in range(16):
            if LETTER_OF[t] != li:
                spr.append(("", (0, 0, 0, 0)))
                continue
            slot, gi = MAP_OF[t]
            rect = MAP_RECTS[slot][gi]
            if L == "U" and t in (6, 7):  # (Detroit's maps sit elsewhere on the USA picture)
                rect = [(15, 228, 228, 443), (238, 234, 470, 458)][t - 6]
            spr.append(("HTR%sPIC2" % L, r2(*rect)))
        build_set("HTR" + L, "HTR%sPIC1" % L, spr, use="DCB")
    # shop: SHPIC2/3/4 frames (3 x 4 of 174 x 103), then SHPIC5's 16 (load_anim_file, screen 0x1e)
    spr = []
    for p in (2, 3, 4):
        spr += [("HSHPIC%d" % p, (1 + (k % 3) * 174, (k // 3) * 103, 174, 103)) for k in range(12)]
    s5 = [(0, 0x10b, 0x88, 0x14), (0, 0x11f, 0x88, 0x14), (0, 0x133, 0x88, 0x14), (0, 0x147, 0x88, 0x14),
          (0, 0x15b, 0x88, 0x14), (0, 0x16f, 0x88, 0x14), (0x13b, 0x10e, 0xae, 0x67), (0x8d, 0x10e, 0xae, 0x67),
          r2(10, 8, 0x9b, 0x86), r2(0xc3, 6, 0x106, 0x88), r2(0x131, 7, 0x1bf, 0x84), r2(0x1c9, 0x10, 0x27b, 0x7d),
          r2(0, 0x89, 0x77, 0x109), r2(0x81, 0xa5, 0x129, 0xf7), r2(0x132, 0x94, 0x1b5, 0x100), r2(0x1d9, 0x86, 0x255, 0x104)]
    spr += [("HSHPIC5", r) for r in s5]
    build_set("HSH", "HSHPIC1", spr, trace("HSHOPROT", 38, 0, -40), use="ACFBE", page2=0)
    # results: the stopwatch (fastest lap), and the two panels (race, championship)
    build_set("HRES", "HRESPIC1", [("HRESPIC0", (0, 232, 26, 29)), ("HRESPIC2", (46, 81, 550, 360)),
                                   ("HRESPIC3", (46, 81, 550, 360))], use="CAF", page2=1)
    build_set("HHI", "HHIPIC2", [], use="CAF")
    build_set("HSAV", "HSAVPIC1", [], use="CDF")
    for k in range(8):
        build_set("HW%d" % k, "HW%dPIC2" % k, [], use="BFA")
        build_set("HL%d" % k, "HL%dPIC2" % k, [], use="BFA")
    build_set("VIRGIN", "VIRGIN", [], pal_from="VIRGIN")
    # the 16 drivers: full name and handle (the surname) (the driver table's +0x14 / +0x82)
    exe, _ = b.exe_tables()
    i = exe.find(b"KEVIN MOORE\x00")
    parts = [p for p in exe[i:i + 600].split(b"\x00") if p]
    out = bytearray()
    for k in range(0, 45, 3):
        out += parts[k][:19].ljust(20, b"\x00") + parts[k + 2][:9].ljust(10, b"\x00")
    out += parts[45][:19].ljust(20, b"\x00") + parts[45][:9].ljust(10, b"\x00")   # (the 16th: unused)
    open(os.path.join(OUT, "drivers.bin"), "wb").write(out)
    # the tracks' details for the track screens: country, name, the 11 description lines
    out = bytearray()
    for t in range(16):
        raw = exe[_ + 0xbaf44 + t * 0x342:_ + 0xbaf44 + (t + 1) * 0x342]
        out += raw[0x51:0x51 + 11].split(b"\x00")[0].ljust(12, b"\x00")
        out += raw[0x0d:0x0d + 19].split(b"\x00")[0].ljust(20, b"\x00")
        for i in range(11):
            a = struct.unpack_from("<I", raw, 0x21 + 4 * i)[0]
            a = a if a >= 0xb0000 else 0xb0000 + a
            line = exe[_ + a:exe.index(b"\x00", _ + a)] if a > 0xb0000 else b""
            out += line[:31].ljust(32, b"\x00")
    open(os.path.join(OUT, "trackinfo.bin"), "wb").write(out)
    for snd in ("SELSND1.MSF", "DESELECT.MSF", "QUIT.MSF"):
        b.msf_to_pcm(snd)
    print("done: menus ready")


if __name__ == "__main__":
    main()
