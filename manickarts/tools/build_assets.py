#!/usr/bin/env python3
"""
build_assets.py - turns the original Manic Karts files into the port's data/ folder.

Reads (relative to the project folder):
    extracted/KART/      the files unpacked from KART.DAT (tools/unkart.c)
    extracted/K.EXE      the game program (for the track table: names and file names)
    mods/                your own pictures to paste over the game's (see read_sheet)
Writes:
    data/tracks.bin      16 track entries (see track.c: TrackInfo)
    data/trkNN.map       the track's 9 maps (the .KTF file as-is) + a 128x128 brightness map
    data/XX.pal          the theme's 256 colours (ARGB1555): every picture below uses them,
                         one byte a pixel, as the original does; then the fog colour (r, g, b, 255):
                         the colour along the bottom of the sky
    data/XX_floor.tex    floor tiles: 240 tiles of 32x32, 16 a row, 512x512
    data/XX_walls.tex    wall tiles: 60 tiles of 64x64, 8 a row, 512x512 (map value v: tile v-1)
    data/trkNN.anm       the track's animated wall and floor tiles
    data/trkNN_far.tex   the whole floor of the track, lit, 8 pixels a cell: 1024x1024
                         (drawn for the far-away floor, in 8x8-cell blocks)
    (the karts: tools/build_vq.py)
    data/XX_hud.tex      the on-screen display's pictures: the original's fonts and icons (nFONT0..4,
                         nSMLNUM .AZ) and the two tracks' small maps (TRKnn.BMP), packed in 512x256
    data/XX_hud.bin      ... where each glyph is (5 fonts of 60 + 2 maps, x y w h), then the drivers' names
    data/NAME.pcm        each race sound (.MSF), for the Dreamcast's sound chip: rate, loop, 8-bit samples
    data/trkNN.sfx       the track's sound list (as the original's, by number) and its ambient sounds
    data/XX_skyN.tex     the sky panorama (hi-res .SKY, 2560x250), in 5 pieces of 512x256
    data/XX_obj.tex      the theme's scenery sprites (.CRP), packed into 1024x512,
    data/XX_obj.bin      ... and where each one is (x, y, w, h)
    data/trkNN.obj       the track's object types and where they stand
    data/physics.bin     TRIG.DAT (cos, sin, tan x 3600) + engine power curves and
                         the engine-upgrade table, copied out of K.EXE

Textures are stored already twiddled (the PowerVR's memory order), so the
Dreamcast copies them straight into video memory.

Everything is converted here, at build time, so the Dreamcast only copies it
into video memory. Plain Python 3, nothing to install.
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(HERE)
KART = os.path.join(PORT, "extracted", "KART")
KEXE = os.path.join(PORT, "extracted", "K.EXE")
OUT = os.path.join(PORT, "data")

TEX_ARGB1555 = 0
TEX_RGB565 = 2
TEX_TWIDDLED = 0x8000                     # (flag in the format field)


MODS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mods")


def read_png(path):
    """a PNG as (width, height, [(r, g, b, a)...]), with nothing but the standard library
    (8-bit greyscale, RGB, palette, grey+alpha or RGBA; not interlaced)"""
    import zlib
    d = open(path, "rb").read()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit("%s is not a PNG" % path)
    i, idat, plte, trns = 8, b"", None, None
    while i < len(d):
        n, kind = struct.unpack(">I4s", d[i:i + 8])
        body = d[i + 8:i + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        i += 12 + n
    if depth != 8 or inter:
        sys.exit("%s: please save it as an ordinary 8-bit PNG (not 16-bit or interlaced)" % path)
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev, at = [], bytearray(stride), 0
    for y in range(h):
        f = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    px = []
    for line in rows:
        for x in range(w):
            p = line[x * bpp:(x + 1) * bpp]
            if ctype == 0:
                px.append((p[0], p[0], p[0], 255))
            elif ctype == 2:
                px.append((p[0], p[1], p[2], 255))
            elif ctype == 3:
                k = p[0]
                al = trns[k] if trns and k < len(trns) else 255
                px.append((plte[k * 3], plte[k * 3 + 1], plte[k * 3 + 2], al))
            elif ctype == 4:
                px.append((p[0], p[0], p[0], p[1]))
            else:
                px.append((p[0], p[1], p[2], p[3]))
    return w, h, px


def read_sheet(pcx):
    """a floor or wall sheet: the game's own NAME.PCX, with any of your own pictures pasted
    over it from mods/: mods/NAME@X,Y.png goes onto the sheet with its top left corner at
    (X, Y). Any colours: each is matched to the nearest of the sheet's palette; pure green
    (and see-through pixels) become colour 0, which is see-through on walls"""
    w, h, px, pal = read_pcx(os.path.join(KART, pcx))
    stem = pcx.rsplit(".", 1)[0]
    if os.path.isdir(MODS):
        near = None
        for fn in sorted(os.listdir(MODS)):
            if not (fn.upper().startswith(stem.upper() + "@") and fn.lower().endswith(".png")):
                continue
            try:
                x0, y0 = (int(v) for v in fn[len(stem) + 1:-4].split(","))
            except ValueError:
                sys.exit("mods/%s: name it %s@X,Y.png (X, Y: where its top left corner goes)" % (fn, stem))
            mw, mh, rgba = read_png(os.path.join(MODS, fn))
            if x0 < 0 or y0 < 0 or x0 + mw > w or y0 + mh > h:
                sys.exit("mods/%s (%dx%d at %d,%d) doesn't fit on %s (%dx%d)" % (fn, mw, mh, x0, y0, pcx, w, h))
            near = near or Nearest(pal)
            px = bytearray(px)
            for i, (r_, g_, b_, a_) in enumerate(rgba):
                px[(y0 + i // mw) * w + x0 + i % mw] = 0 if a_ < 128 or (r_ < 40 and g_ > 215 and b_ < 40) \
                    else near(r_, g_, b_)
            print("  using mods/" + fn)
    return w, h, px, pal


def read_pcx(path):
    """an 8-bit PCX: (width, height, pixels as bytes, palette as 256 (r,g,b))"""
    d = open(path, "rb").read()
    xmin, ymin, xmax, ymax = struct.unpack("<4H", d[4:12])
    bpl = struct.unpack("<H", d[66:68])[0]
    w, h = xmax - xmin + 1, ymax - ymin + 1
    pal_raw = d[-768:]
    pal = [(pal_raw[i * 3], pal_raw[i * 3 + 1], pal_raw[i * 3 + 2]) for i in range(256)]
    out = bytearray()
    i = 128
    need = bpl * h
    while len(out) < need and i < len(d) - 769:
        b = d[i]; i += 1
        if b >= 0xC0:
            n = b & 0x3F; v = d[i]; i += 1
            out += bytes([v]) * n
        else:
            out.append(b)
    px = bytearray()
    for y in range(h):
        px += out[y * bpl:y * bpl + w]
    return w, h, bytes(px), pal


def rgb565(c):
    r, g, b = c
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


SPREAD = [sum(((v >> b) & 1) << (2 * b) for b in range(11)) for v in range(2048)]


def twiddle(w, h, texels):
    """the PowerVR's twiddled order (same formula as plat_dc.c used at load time):
    a square texture interleaves the y and x bits; a non-square one is a row of squares"""
    m = min(w, h)
    lm = m.bit_length() - 1
    out = [0] * (w * h)
    for y in range(h):
        sy = SPREAD[y & (m - 1)]
        row = texels[y * w:(y + 1) * w]
        for x in range(w):
            idx = sy | (SPREAD[x & (m - 1)] << 1)
            idx += ((x >> lm) << (2 * lm)) if w > h else ((y >> lm) << (2 * lm))
            out[idx] = row[x]
    return out


def write_tex(path, w, h, texels, fmt=TEX_RGB565):
    with open(path, "wb") as f:
        f.write(b"MKTX" + struct.pack("<4H", w, h, fmt | TEX_TWIDDLED, 0))
        f.write(struct.pack("<%dH" % (w * h), *twiddle(w, h, texels)))


TEX_PAL8 = 3
TEX_NOFOG = 0x4000
TEX_POINT = 0x2000


def rle(data):
    """packed: a control byte c < 128 = the next c+1 bytes as they are; c >= 128 = the
    next byte c-126 times (2..129). The disc reads far less (most sprite sheets are
    mostly see-through)"""
    out = bytearray()
    i, n = 0, len(data)
    lit = bytearray()
    while i < n:
        j = i
        while j < n and j - i < 129 and data[j] == data[i]:
            j += 1
        if j - i >= 3:
            while lit:
                out.append(min(len(lit), 128) - 1)
                out += lit[:128]
                lit = lit[128:]
            out.append(j - i + 126)
            out.append(data[i])
            i = j
        else:
            lit.append(data[i])
            i += 1
    while lit:
        out.append(min(len(lit), 128) - 1)
        out += lit[:128]
        lit = lit[128:]
    return bytes(out)


def write_tex8(path, w, h, idx, bank, flags=0):
    """a paletted texture: one byte a texel, colours from palette bank (0 = sprites,
    colour 0 see-through; 1 = solid); "MKTR": packed (rle())"""
    with open(path, "wb") as f:
        f.write(b"MKTR" + struct.pack("<4H", w, h, TEX_PAL8 | (bank << 8) | flags | TEX_TWIDDLED, 0))
        f.write(rle(bytes(twiddle(w, h, idx))))


def atlas8(sheets, tile, per_sheet_cols, per_sheet_rows, atlas_cols, size=512):
    """as atlas(), keeping the colour numbers"""
    tex = [0] * (size * size)
    n = 0
    for (w, h, px, pal) in sheets:
        for ty in range(per_sheet_rows):
            for tx in range(per_sheet_cols):
                ax, ay = (n % atlas_cols) * tile, (n // atlas_cols) * tile
                for y in range(tile):
                    row = (ty * tile + y) * w + tx * tile
                    dst = (ay + y) * size + ax
                    tex[dst:dst + tile] = px[row:row + tile]
                n += 1
    return tex


def atlas(sheets, tile, per_sheet_cols, per_sheet_rows, atlas_cols, size=512):
    """cut every sheet into tiles (in order) and lay them out atlas_cols to a row"""
    tex = [0] * (size * size)
    n = 0
    for (w, h, px, pal) in sheets:
        lut = [rgb565(c) for c in pal]
        for ty in range(per_sheet_rows):
            for tx in range(per_sheet_cols):
                ax, ay = (n % atlas_cols) * tile, (n // atlas_cols) * tile
                for y in range(tile):
                    row = (ty * tile + y) * w + tx * tile
                    dst = (ay + y) * size + ax
                    for x in range(tile):
                        tex[dst + x] = lut[px[row + x]]
                n += 1
    return tex


# Surface type of each floor tile, for each pair of tracks. The original builds
# these at start-up (init_track_surface_names 0x496a0 copies 10-letter strings out
# of K.EXE into each track's entry at +0x142); copied out here once. Letters pick
# the drag (kart_compute_forces): D G M S d l m p w; jumps: B J j; others none.
SURFACES = [
    "tttItttttttttIttIIttttIItIIIItttttttIItttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttppptttttttttptttttttttttttttttttttttttBIBttttBIBIgIttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt.",
    "NNNNNNNWWWNNMtNNNWWWNNNWWNNWNNWWWWNNNWNNNNNNNNNgggNNNNWWWWWNNNggNNNiiiNNggNNNiiiNNN.NNNiiiiiiMMMMMM.iiiMMMMMttiiiMMMMMttIIIIIIIIJJIIIIIIIIJJIIIIIIIIppIIIIIIII..IIIIttt...IIIIttt...NNNNNNNNNNNNNNNNNNNNNNNNNNN...NNNNWWWW......NNWW......NNWW..",
    "gttttggg..ggggtttt..gggtttggggttgggggtggggggtttttttgggggttttIIIIIIIIIIIIIIIIIIJIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIttttttttttttttttttttttttttttttttttttggggttttttggggttttttggggttttttttttttttttttttttttttttttttBgggppptttggggppptttggggpppt",
    "...........................................................................................................................JJ...J....J..............................................mmmmmmmmmpmmmmmmmmmpmmmmmmmmm.mmmpppppppmmmpppppppmmmpppp.pp",
    "ggbgggggggggggggbgggggbbgbbbggbggbgbggggggggggggggbgggggggggggggBpggggggggBpggggggggggggggggggggggggggggWWggggggggWWWWBWWWWWggggggWWWWggDDDDWWWWggDDDDWWWWggDDDDWWWWggDDDDggggggggggggggggggggDDDDDDDDggDDDDDDDDggDDDDDDDDggDDDDDDDDssggggggggsB",
    "........WW......WWWW..IIIIWWWW..IIIIWWWW..IIIIWWWW...pIIWWWW........................................................................................................................B..B..B.WW..B..B..WW.B..B..BWW..B..B..WWJ...................",
    "..........................................................................................II........IIIIIIpp....IIIIpp........J..J.........J.........J............................................................................pp........pp..",
    "SSSSSSSSSSSSwwwwwwwwSSwwwwwwwwwwMMMMMMMMwwMMMMMMMMMMMMMMMMMMSSSSSSSSSSSSSSSSSSSSwwSSSSSgSSwwwwgSSSSSMMwwDDDDDDMMMMDDDDDDSSggttggttSSggttggttttttttggggttttttgtggttttttgtggSSSSSSggggttSSSppttJttSSSppttgSSSSttttttStSSttttttStSSSSttSSSSSSttttSS",
]


def track_table():
    """the 16 track entries inside K.EXE (834 bytes each, see decomp/game/track.h)"""
    exe = open(KEXE, "rb").read()
    first = exe.find(b"UK.KTF\x00")
    if first < 0:
        sys.exit("can't find the track table in K.EXE")
    def cstr(b):
        return b.split(b"\x00")[0].decode("latin-1")
    tracks = []
    for i in range(16):
        e = exe[first + i * 0x342:first + (i + 1) * 0x342]
        tracks.append({
            "ktf": cstr(e[0:13]),
            "title": cstr(e[0x0d:0x0d + 40]),
            "floors": [cstr(e[0x78 + 13 * k:0x78 + 13 * (k + 1)]) for k in range(4)],
            "walls": [cstr(e[0xac + 13 * k:0xac + 13 * (k + 1)]) for k in range(4)],
            "ksp": cstr(e[0xfa:0xfa + 13]),
            "sky": cstr(e[0xe0:0xe0 + 13]),
            "raw": e,
            "pal": cstr(e[0xed:0xed + 13]),
            "karts": "%dENK" % (i // 2),               # kart sprites for this theme's palette: nENK0..3.DAT
            "heading": struct.unpack("<I", e[0x10f:0x113])[0] % 3600,   # start heading, tenths of a degree
            "surface": SURFACES[i // 2].encode("latin-1").ljust(256, b"\x00"),   # floor tile -> surface type
        })
    return tracks


def pcx_name(bmp):
    return bmp.rsplit(".", 1)[0] + ".PCX"


def small_tiles(sheets, tile, cols, rows, px_out):
    """each tile shrunk to px_out x px_out (averaged), as rows of (r, g, b) floats"""
    tiles = []
    k = tile // px_out
    for (w, h, px, pal) in sheets:
        for ty in range(rows):
            for tx in range(cols):
                t = []
                for oy in range(px_out):
                    for ox in range(px_out):
                        r = g = b = 0
                        for y in range(k):
                            base = (ty * tile + oy * k + y) * w + tx * tile + ox * k
                            for x in range(k):
                                c = pal[px[base + x]]
                                r += c[0]; g += c[1]; b += c[2]
                        n = k * k
                        t.append((r / n, g / n, b / n))
                tiles.append(t)
    return tiles


def far_floor(ktf, light, tiles, ppc=8):
    """the whole floor, ppc pixels a cell, each cell's tile darkened by its brightness
    (blended between the cell's corners, as the near floor is)"""
    size = 128 * ppc
    floor = ktf[0:16384]
    # corner brightness, as track.c works it out
    corner = [[0.0] * 129 for _ in range(129)]
    for z in range(129):
        for x in range(129):
            s = n = 0
            for dz in (-1, 0):
                for dx in (-1, 0):
                    cx, cz = x + dx, z + dz
                    if 0 <= cx < 128 and 0 <= cz < 128:
                        s += light[cz * 128 + cx]; n += 1
            corner[z][x] = (s / n if n else 255) / 255.0
    tex = [0] * (size * size)
    for r in range(128):
        for c in range(128):
            t = floor[r * 128 + c]
            t = 239 if t >= 240 else t
            pix = tiles[t]
            l00, l10, l01, l11 = corner[r][c], corner[r][c + 1], corner[r + 1][c], corner[r + 1][c + 1]
            for oy in range(ppc):
                fy = (oy + 0.5) / ppc
                la, lb = l00 + (l01 - l00) * fy, l10 + (l11 - l10) * fy
                base = (r * ppc + oy) * size + c * ppc
                for ox in range(ppc):
                    l = la + (lb - la) * ((ox + 0.5) / ppc)
                    pr, pg, pb = pix[oy * ppc + ox]
                    tex[base + ox] = rgb565((min(255, int(pr * l)), min(255, int(pg * l)), min(255, int(pb * l))))
    return size, tex


class Nearest:
    """the palette colour nearest to an (r, g, b), remembered"""
    def __init__(self, pal):
        self.pal = pal
        self.memo = {}

    def __call__(self, r, g, b):
        k = (int(r) >> 2, int(g) >> 2, int(b) >> 2)
        v = self.memo.get(k)
        if v is None:
            best, v = 1 << 30, 0
            for i in range(1, 256):              # (not 0: see-through in the sprite palette)
                pr, pg, pb = self.pal[i]
                d = (pr - r) * (pr - r) * 3 + (pg - g) * (pg - g) * 4 + (pb - b) * (pb - b) * 2
                if d < best:
                    best, v = d, i
            self.memo[k] = v
        return v


def far_floor8(ktf, light, tiles, nearest, ppc=8):
    """far_floor(), as colour numbers of the theme's palette"""
    size = 128 * ppc
    floor = ktf[0:16384]
    corner = [[0.0] * 129 for _ in range(129)]
    for z in range(129):
        for x in range(129):
            s = n = 0
            for dz in (-1, 0):
                for dx in (-1, 0):
                    cx, cz = x + dx, z + dz
                    if 0 <= cx < 128 and 0 <= cz < 128:
                        s += light[cz * 128 + cx]; n += 1
            corner[z][x] = (s / n if n else 255) / 255.0
    tex = [0] * (size * size)
    for r in range(128):
        for c in range(128):
            t = floor[r * 128 + c]
            t = 239 if t >= 240 else t
            pix = tiles[t]
            l00, l10, l01, l11 = corner[r][c], corner[r][c + 1], corner[r + 1][c], corner[r + 1][c + 1]
            for oy in range(ppc):
                fy = (oy + 0.5) / ppc
                la, lb = l00 + (l01 - l00) * fy, l10 + (l11 - l10) * fy
                base = (r * ppc + oy) * size + c * ppc
                for ox in range(ppc):
                    l = la + (lb - la) * ((ox + 0.5) / ppc)
                    pr, pg, pb = pix[oy * ppc + ox]
                    tex[base + ox] = nearest(pr * l, pg * l, pb * l)
    return size, tex


def lighting(ktf, ksp_path, pal):
    """map 8 (lighting level per cell) -> brightness 0..255 per cell, from the
    shade table: how much darker each level makes the palette on average"""
    ksp = open(ksp_path, "rb").read()
    table = bytes(range(256)) + ksp[:0x4000]          # row 0 = unchanged (as the game does)
    lum = [0.3 * r + 0.59 * g + 0.11 * b for (r, g, b) in pal]
    base = sum(lum[1:]) or 1.0
    factor = []
    for level in range(65):
        row = table[level * 256:(level + 1) * 256]
        factor.append(sum(lum[row[c]] for c in range(1, 256)) / base)
    light = bytearray(128 * 128)
    m8 = ktf[8 * 16384:9 * 16384]
    for i in range(16384):
        v = m8[i]
        if v >= 0x6C:
            v -= 0x6C
        elif v >= 0x36:
            v = 0
        f = factor[min(v, 64)]
        light[i] = max(0, min(255, int(f * 255 + 0.5)))
    return light


def start_place(ktf):
    """somewhere sensible to start: the middle of the first lap checkpoint zone,
    facing the middle of the next one (map 6)"""
    m6 = ktf[6 * 16384:7 * 16384]
    zones = {}
    for i, v in enumerate(m6):
        if 0 < v < 0x36:
            zones.setdefault(v, []).append(i)
    if len(zones) < 2:
        return 64, 64, 0
    ks = sorted(zones)
    def centre(k):
        c = zones[k]
        return sum(i % 128 for i in c) / len(c), sum(i // 128 for i in c) / len(c)
    (x0, y0), (x1, y1) = centre(ks[0]), centre(ks[1])
    import math
    heading = int((math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360) * 10)
    return int(x0), int(y0), heading


def load_pal(path):
    """a RIFF .PAL: 256 (r, g, b)"""
    d = open(path, "rb").read()
    i = d.find(b"data")
    n = struct.unpack("<H", d[i + 10:i + 12])[0]
    pal = [tuple(d[i + 12 + 4 * j:i + 15 + 4 * j]) for j in range(n)]
    return pal + [(0, 0, 0)] * (256 - len(pal))


def argb1555(c):
    r, g, b = c
    return 0x8000 | ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)


def kart_atlas(path, pal):
    """the first driver's 64 views (each stored on its side) -> 128x64 cells, 8 a row,
    the kart standing on the bottom edge, centred; colour 0 = see-through"""
    d = open(path, "rb").read()
    n = struct.unpack("<H", d[:2])[0]
    base = 2 + 12 * n
    lut = list(range(256))                   # (colour numbers, as stored)
    W, H = 1024, 512
    tex = [0] * (W * H)
    for k in range(64):
        s, w, ln = struct.unpack("<III", d[2 + 12 * k:14 + 12 * k])
        px = d[base + s:base + s + w * ln]
        # stored: ln rows of w (=64) bytes, each row one screen column (turned 90 degrees)
        cx, cy = (k % 8) * 128, (k // 8) * 64
        x0 = cx + (128 - min(ln, 128)) // 2
        skip = max(0, ln - 128) // 2            # (a few karts are wider than 128: trim both ends)
        n = min(ln, 128)
        for col in range(n):
            row = px[(col + skip) * w:(col + skip + 1) * w]
            for j in range(min(w, 64)):
                v = row[j]
                if v:
                    tex[(cy + 63 - j) * W + x0 + n - 1 - col] = lut[v]
    return W, H, tex


def kart_views(path, driver):
    """one driver's 64 views from a kart file, each as rows of 64 colour numbers by 128,
    the right way round (as kart_atlas)"""
    d = open(path, "rb").read()
    n = struct.unpack("<H", d[:2])[0]
    base = 2 + 12 * n
    views = []
    for k in range(64):
        s, w, ln = struct.unpack("<III", d[2 + 12 * (driver * 64 + k):14 + 12 * (driver * 64 + k)])
        px = d[base + s:base + s + w * ln]
        img = [[0] * 128 for _ in range(64)]
        x0 = (128 - min(ln, 128)) // 2
        skip = max(0, ln - 128) // 2
        m = min(ln, 128)
        for col in range(m):
            row = px[(col + skip) * w:(col + skip + 1) * w]
            for j in range(min(w, 64)):
                img[63 - j][x0 + m - 1 - col] = row[j]
        views.append(img)
    return views


def views_atlas(views):
    """64 views (kart_views) -> 1024x512, 8 a row of 128x64 (as kart_atlas)"""
    tex = [0] * (1024 * 512)
    for v, img in enumerate(views):
        vx, vy = (v % 8) * 128, (v // 8) * 64
        for y in range(64):
            tex[(vy + y) * 1024 + vx:(vy + y) * 1024 + vx + 128] = img[y]
    return tex


HUD_FONTS = [("FONT0", 32, 20, 60), ("FONT1", 32, 20, 60), ("FONT2", 32, 20, 60),
             ("FONT4", 32, 32, 60), ("SMLNUM", 32, 32, 8)]


def hud_assets(n, tracks2):
    """the theme's fonts (as race_load_fonts_and_icons loads them: glyphs in cells of w x h,
    from the top left, each trimmed to its last solid pixel) and the small maps of its
    two tracks -> (512x256 atlas, table of (x, y, w, h))"""
    glyphs = []
    for name, cw, ch, count in HUD_FONTS:
        rows = read_bmp(os.path.join(KART, "%d%s.AZ" % (n, name)))[2][::-1]   # (stored top row first)
        for k in range(60):
            if k >= count:
                glyphs.append(None)
                continue
            x0, y0 = (k % 10) * cw, (k // 10) * ch
            w = h = 0
            for y in range(ch):
                for x in range(cw):
                    if rows[y0 + y][x0 + x]:
                        w = max(w, x + 1)
                        h = max(h, y + 1)
            glyphs.append((w, h, [bytes(rows[y0 + y][x0:x0 + w]) for y in range(h)]) if w else None)
    for t in tracks2:
        w, h, rows = read_bmp(os.path.join(KART, "TRK%02d.BMP" % t))
        flat = b"".join(rows)
        bg = max(set(flat), key=flat.count)          # (the map's background: see-through)
        glyphs.append((w, h, [bytes(0 if v == bg else v for v in r) for r in rows]))
    solid = [g for g in glyphs if g]
    tex, rects = pack_sprites(solid, 512, 256)
    table, i = [], 0
    for g in glyphs:
        if g:
            table.append(rects[i])
            i += 1
        else:
            table.append((0, 0, 0, 0))
    return tex, table


def driver_names(exe):
    """the drivers' surnames, as the original lists them (full name, first, last)"""
    i = exe.find(b"KEVIN MOORE\x00")
    names = []
    parts = [p for p in exe[i:i + 600].split(b"\x00") if p]
    for k in range(0, 48, 3):
        names.append(parts[k + 2].decode("latin-1"))
    return names


def msf_to_pcm(name):
    """a MANIC SOUND FILE -> data/NAME.pcm: "MKSN", u32 rate, samples, loop start, loop end,
    u32 loops; then signed 8-bit samples. The sound chip plays at most 65534 samples, so
    longer ones are halved (every other pair averaged, played at half the rate)."""
    d = open(os.path.join(KART, name), "rb").read()
    ln, ns, fmt, rate, _, play, ls, lflag, le = struct.unpack("<9I", d[0x16:0x3a])
    body = d[58:58 + ln]
    if fmt == 6:                               # (16-bit: keep the top byte)
        body = bytes(body[i + 1] for i in range(0, len(body) - 1, 2))
        ls //= 2; le //= 2
    while len(body) > 65534:
        sb = struct.unpack("%db" % len(body), body)
        body = struct.pack("%db" % (len(sb) // 2), *[(sb[i] + sb[i + 1]) // 2 for i in range(0, len(sb) - 1, 2)])
        play //= 2; ls //= 2; le //= 2
    n = len(body)
    body += b"\x00" * (-n % 32)
    loops = 1 if lflag == 0xFFFFFFFF else 0
    out = os.path.join(OUT, name.rsplit(".", 1)[0] + ".pcm")
    open(out, "wb").write(b"MKSN" + struct.pack("<5I", play, n, min(ls, n), min(le, n) or n, loops) + body)


def track_sounds(t, ktf, exe, base):
    """trkNN.sfx: u8 sounds, 13-byte names (the list at track +0x13e, to "END"); u8 sources,
    then (column, row, sound, 0) for each ambient sound placed on map 5 (object types whose
    picture number is 0x80 or more are sounds: number - 0x80)"""
    o = base + 0xb0000 + struct.unpack("<I", t["raw"][0x13e:0x142])[0]
    names = []
    while exe[o:o + 3] != b"END" and len(names) < 32:
        names.append(exe[o:o + 13].split(b"\x00")[0].decode("latin-1"))
        o += 13
    obj_list = 0xb0000 + struct.unpack("<I", t["raw"][0x12e:0x132])[0]
    types = []
    o = base + obj_list
    while exe[o:o + 3] != b"END" and len(types) < 64:
        types.append(exe[o + 9])
        o += 61
    src = []
    m5 = ktf[5 * 16384:6 * 16384]
    for i, v in enumerate(m5):
        if 0x36 <= v < 0x69:
            k = (v - 0x36) >> 1
            if k < len(types) and types[k] >= 0x80 and len(src) < 32:
                src.append((i % 128, i // 128, (types[k] + 0x80) & 0xff, 0))
    return (bytes([len(names)]) + b"".join(nm.encode("latin-1")[:12].ljust(13, b"\x00") for nm in names) +
            bytes([len(src)]) + b"".join(bytes(s4) for s4 in src)), names


def grid(ktf):
    """starting grid: map 5 holds 0x24 followed by the kart number; the original
    starts kart n at x = (column+1)*1024, y = row*1024 (see the track loader)"""
    m5 = ktf[5 * 16384:6 * 16384]
    g = [0] * 16
    for i in range(16383):
        if m5[i] == 0x24 and m5[i + 1] < 8 and i % 128 < 127:
            k = m5[i + 1]
            g[k * 2], g[k * 2 + 1] = i % 128 + 1, i // 128
    return bytes(g)


def physics_tables():
    """TRIG.DAT, then from K.EXE: the 5 engine power curves (40 ints each, at
    0xb5cf4) and the engine-upgrade shift table (4 x 8 ints, at 0xba180)"""
    trig = open(os.path.join(KART, "TRIG.DAT"), "rb").read()
    exe = open(KEXE, "rb").read()
    first = struct.pack("<4i", 600000, 600000, 610000, 620000)
    at = exe.find(first)
    if at < 0 or len(trig) != 43200:
        sys.exit("can't find the physics tables")
    curves = exe[at:at + 5 * 160]
    up = exe[at + (0xba180 - 0xb5cf4):at + (0xba180 - 0xb5cf4) + 128]
    return trig + curves + up


def read_bmp(path):
    """an 8-bit uncompressed BMP: (width, height, rows top to bottom)"""
    d = open(path, "rb").read()
    off = struct.unpack("<I", d[10:14])[0]
    w, h = struct.unpack("<ii", d[18:26])
    stride = (w + 3) & ~3
    rows = [d[off + y * stride:off + y * stride + w] for y in range(abs(h))]
    if h > 0:
        rows.reverse()                       # (stored bottom row first)
    return w, abs(h), rows


def sky_pieces(path):
    """the hi-res panorama, 2560 x 250: 5 pieces of 512 x 256 (the bottom row repeated below)"""
    w, h, rows = read_bmp(path)
    pieces = []
    for k in range(5):
        tex = []
        for y in range(256):
            row = rows[min(y, h - 1)]
            tex += row[k * 512:(k + 1) * 512]
        pieces.append(tex)
    return pieces


def crp_sprite(path):
    """a .CRP scenery sprite, stored on its side as the karts are: -> (w, h, rows of
    colour numbers, the right way up; colour 0 = see-through)"""
    d = open(path, "rb").read()
    off = struct.unpack("<I", d[10:14])[0]
    W, H = struct.unpack("<ii", d[18:26])
    stride = (W + 3) & ~3
    filerow = [d[off + y * stride:off + y * stride + W] for y in range(H)]   # (as in the file)
    # picture (x, y) = file row H-1-x, column W-1-y: H wide, W tall
    return H, W, [bytes(filerow[H - 1 - x][W - 1 - y] for x in range(H)) for y in range(W)]


def pack_sprites(sprites, W=1024, H=512):
    """shelf-pack the sprites, tallest first, a pixel apart: -> (atlas, [(x, y, w, h)])"""
    order = sorted(range(len(sprites)), key=lambda i: -sprites[i][1])
    rects = [None] * len(sprites)
    x = y = shelf = 0
    for i in order:
        w, h, _ = sprites[i]
        if x + w > W:
            x, y, shelf = 0, y + shelf + 1, 0
        if y + h > H:
            sys.exit("scenery sprites don't fit")
        rects[i] = (x, y, w, h)
        x += w + 1
        shelf = max(shelf, h)
    tex = [0] * (W * H)
    for i, (w, h, rows) in enumerate(sprites):
        sx, sy = rects[i][0], rects[i][1]
        for yy in range(h):
            tex[(sy + yy) * W + sx:(sy + yy) * W + sx + w] = rows[yy]
    return tex, rects


def crp_names(t, exe, base):
    """the theme's scenery sprite files (K.EXE, the list at track +0x12a, 15 bytes each)"""
    o = base + 0xb0000 + struct.unpack("<I", t["raw"][0x12a:0x12e])[0]
    names = []
    while exe[o] not in (0, 0xff) and len(names) < 128:
        names.append(exe[o:o + 13].split(b"\x00")[0].decode("latin-1"))
        o += 15
    return names


def exe_tables():
    exe = open(KEXE, "rb").read()
    at = exe.find(struct.pack("<4i", 600000, 600000, 610000, 620000))
    return exe, at - 0xb5cf4                 # (file offset of address 0)


def track_objects(t, ktf, exe, base):
    """the track's object types (K.EXE, the list at track +0x12e, 61 bytes each, to "END")
    and where they stand (map 5: 0x36..0x68, (v-0x36)/2 = the type, odd = mirrored),
    as track_place_objects 0x4331c does.
    trkNN.obj: u16 types, u16 objects; types of 16 bytes: u8 sprite, u8 flags (1 = bump:
    a jump when driven over, 2 = no collision), u8 fallen[3] (pictures when knocked over),
    u8 pad, s16 z, s16 tall (64 a cell), u8 pad[2], s32 knock (0 = solid, else how hard
    it is to knock flying); objects of
    4 bytes: u8 column, row, type, and the original's draw flag ((v & 1) ^ 1: 1 = as drawn,
    0 = mirrored; karts and other sprites start at 1)."""
    crp_list = 0xb0000 + struct.unpack("<I", t["raw"][0x12a:0x12e])[0]
    obj_list = 0xb0000 + struct.unpack("<I", t["raw"][0x12e:0x132])[0]
    types = []
    o = base + obj_list
    while exe[o:o + 3] != b"END" and len(types) < 64:
        types.append(exe[o:o + 61])
        o += 61
    out = b""
    for r in types:
        spr = r[9]
        flags = 0
        if spr < 0x80:
            ce = exe[base + crp_list + spr * 15:base + crp_list + spr * 15 + 15]
            if ce[14]:
                flags |= 1
            elif ce[13]:
                flags |= 2
        z, tall = struct.unpack("<ii", r[0x32:0x3a])
        knock = struct.unpack("<i", r[0x0d:0x11])[0]
        out += struct.pack("<BB3sxhh2xi", spr, flags, r[0x1d:0x20], z, tall, knock)
    placed = b""
    m5 = ktf[5 * 16384:6 * 16384]
    n = 0
    for i, v in enumerate(m5):
        if 0x36 <= v < 0x69:
            k = (v - 0x36) >> 1
            if k >= len(types):
                k = 0
            if types and types[k][9] < 0x80:
                placed += struct.pack("<BBBB", i % 128, i // 128, k, (v & 1) ^ 1)
                n += 1
    return struct.pack("<HH", len(types), n) + out + placed


def track_anims(t, exe, base):
    """the animated wall and floor tiles (track_animate_map_tiles 0x17320): the track's two
    lists (track +0x132 walls, +0x136 floors), 17 bytes an entry to 0xff: u8 the map value
    it changes, ..., u32 its frames at +9: 5 bytes each, u8 the value shown, u32 for how
    long (hundredths of a second), to a length of 0.
    trkNN.anm: "MKAN", u8 wall entries, u8 floor entries; then each entry: u8 value,
    u8 frames, frames x (u8 value, u8 hundredths)."""
    out = [b"", b""]
    counts = [0, 0]
    for k, off in enumerate((0x132, 0x136)):
        o = base + 0xb0000 + struct.unpack("<I", t["raw"][off:off + 4])[0]
        while exe[o] != 0xff and counts[k] < 255:
            q = base + 0xb0000 + struct.unpack("<I", exe[o + 9:o + 13])[0]
            frames = []
            while len(frames) < 32:
                v, dur = struct.unpack("<BI", exe[q:q + 5])
                if dur == 0:
                    break
                frames.append(struct.pack("<BB", v, min(dur, 255)))
                q += 5
            if frames:
                out[k] += struct.pack("<BB", exe[o], len(frames)) + b"".join(frames)
                counts[k] += 1
            o += 17
    return b"MKAN" + struct.pack("<BB", *counts) + out[0] + out[1]


def main():
    os.makedirs(OUT, exist_ok=True)
    tracks = track_table()
    exe, base = exe_tables()
    themes_done = {}
    sounds_done = set()
    entries = b""
    for n, t in enumerate(tracks):
        theme = t["floors"][0][:2]                    # "UK", "IC", ...
        if theme not in themes_done:
            pal = load_pal(os.path.join(KART, t["pal"]))
            w_, h_, srows = read_bmp(os.path.join(KART, "H" + t["sky"]))
            low = [pal[v] for row in srows[h_ - 12:] for v in row[::7]]
            fog = [sum(c[i] for c in low) // len(low) for i in range(3)]
            open(os.path.join(OUT, theme + ".pal"), "wb").write(
                struct.pack("<256H", *[argb1555(c) for c in pal]) + bytes(fog + [255]))
            floors = [read_sheet(pcx_name(f)) for f in t["floors"]]
            walls = [read_sheet(pcx_name(f)) for f in t["walls"]]
            write_tex8(os.path.join(OUT, theme + "_floor.tex"), 512, 512, atlas8(floors, 32, 10, 6, 16), 1)
            # (the sprites' bank: colour 0, pure green in the pictures, is see-through, as on
            # the low barriers of Germany's circuit, whose tops show what's behind)
            write_tex8(os.path.join(OUT, theme + "_walls.tex"), 512, 512, atlas8(walls, 64, 5, 3, 8), 0)
            for k, piece in enumerate(sky_pieces(os.path.join(KART, "H" + t["sky"]))):
                write_tex8(os.path.join(OUT, "%s_sky%d.tex" % (theme, k)), 512, 256, piece, 1, TEX_NOFOG)
            htex, htab = hud_assets(n // 2, (n, n + 1))
            write_tex8(os.path.join(OUT, theme + "_hud.tex"), 512, 256, htex, 0, TEX_POINT)
            dn = driver_names(exe)
            open(os.path.join(OUT, theme + "_hud.bin"), "wb").write(
                b"".join(struct.pack("<4H", *r) for r in htab) +
                b"".join(nm.encode("latin-1")[:15].ljust(16, b"\x00") for nm in dn))
            names = crp_names(t, exe, base)
            sprites = [crp_sprite(os.path.join(KART, nm)) for nm in names]
            stex, rects = pack_sprites(sprites)
            write_tex8(os.path.join(OUT, theme + "_obj.tex"), 1024, 512, stex, 0)
            open(os.path.join(OUT, theme + "_obj.bin"), "wb").write(
                struct.pack("<H", len(rects)) + b"".join(struct.pack("<4H", *r) for r in rects))
            themes_done[theme] = (small_tiles(floors, 32, 10, 6, 8), Nearest(pal))
            print("textures for", theme)
        ktf = open(os.path.join(KART, t["ktf"]), "rb").read()
        pal = read_pcx(os.path.join(KART, pcx_name(t["floors"][0])))[3]
        light = lighting(ktf, os.path.join(KART, t["ksp"]), pal)
        with open(os.path.join(OUT, "trk%02d.map" % n), "wb") as f:
            f.write(ktf + bytes(light) + t["surface"])
        size, far = far_floor8(ktf, light, themes_done[theme][0], themes_done[theme][1])
        write_tex8(os.path.join(OUT, "trk%02d_far.tex" % n), size, size, far, 1)
        open(os.path.join(OUT, "trk%02d.obj" % n), "wb").write(track_objects(t, ktf, exe, base))
        open(os.path.join(OUT, "trk%02d.anm" % n), "wb").write(track_anims(t, exe, base))
        sfx, names = track_sounds(t, ktf, exe, base)
        open(os.path.join(OUT, "trk%02d.sfx" % n), "wb").write(sfx)
        for nm in names:
            if nm not in sounds_done:
                msf_to_pcm(nm)
                sounds_done.add(nm)
        sx, sy, sh = start_place(ktf)
        # TrackInfo: char title[32]; char theme[4]; u8 startX, startY; u16 startHeading;
        #            u16 heading; u8 pad[6]; u8 grid[16]   (64 bytes)
        entries += struct.pack("<32s4sBBHH6x16s", t["title"].encode("latin-1")[:31], theme.encode(),
                               sx, sy, sh, t["heading"], grid(ktf))
        print("track %2d  %-14s %-16s start %d,%d heading %d" % (n, t["ktf"], t["title"], sx, sy, sh // 10))
    open(os.path.join(OUT, "tracks.bin"), "wb").write(entries)
    open(os.path.join(OUT, "physics.bin"), "wb").write(physics_tables())
    print("done: data/ ready")


if __name__ == "__main__":
    main()
