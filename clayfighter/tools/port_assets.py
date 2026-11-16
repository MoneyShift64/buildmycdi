#!/usr/bin/env python3
"""Makes the Dreamcast port's game data, CFSC.DAT, from your ROM (at build time, so the port
never has to convert anything while it runs).

Usage: port_assets.py baserom.us.z64 asset_trace.txt mpf_unpack CFSC.DAT

The N64 is big-endian and the Dreamcast little-endian, so every 16- and 32-bit number the game
reads from the ROM has to have its bytes turned round. tools/assettrace.py recorded how the
game reads each byte of what it loads (asset_trace.txt: offsets and widths, no ROM data); this
applies that to the ROM:

  - data the game copies from the ROM as it is: converted where it lies;
  - data the game unpacks (mpDataDecompress): unpacked here, converted, and stored unpacked
    (an MPF "stored" block), so the port doesn't unpack anything either. These get bigger, so
    they move: CFSC.DAT starts with a directory that says where each ROM range is now, and
    the headers that say how big they are or where they start are changed to match.

CFSC.DAT: u32 count, then count x {rom start, rom end, offset in the file, size} (all
little-endian), then the data.
"""
import os, re, struct, subprocess, sys, tempfile

VIRTUAL = 0x02000000          # moved data gets "ROM" addresses from here on

LAYOUT = os.environ.get("CFSC_LAYOUT", "port/data_layout.txt")
CHARDEF_PATH = os.path.join(os.path.dirname(LAYOUT), "chardef_layout.txt")

def data_words(label, n, rom=None):
    """n words of the game's data at label (its address from the data layout, the words from the ROM)"""
    if rom is not None and os.path.exists(LAYOUT):
        for line in open(LAYOUT):
            f = line.split()
            if f[0] == "L" and f[2] == label:
                o = 0x1000 + int(f[1], 16) - 0x80000400
                return list(struct.unpack_from(f">{n}I", rom, o))
    out, on = [], False
    for line in open("asm/data.s"):
        if line.startswith(label + ":"): on = True; continue
        if on:
            m = re.match(r"/\* [0-9A-F]{8} \*/ \.word (.*)", line)
            if m:
                out += [int(w, 16) for w in m.group(1).split(",")]
                if len(out) >= n: return out[:n]
    raise SystemExit(f"port_assets: no {label} in the data layout")

def load_trace(path):
    """masks: 1, 2, 4 how the C reads a byte; x 8 how the original's code does (used only
    where no C read it)"""
    masks = {}
    packed = set()
    for line in open(path):
        f = line.split()
        d = masks.setdefault(f[0], {})
        if len(f) == 5:                 # packed: source start step count mask (tools/assettrace_pack.py)
            o, step, n, m = int(f[1], 16), int(f[2], 16), int(f[3], 16), int(f[4])
            for k in range(n): d[o + k * step] = d.get(o + k * step, 0) | m
            packed.add(f[0])
        else:
            o = int(f[1], 16)
            d[o] = d.get(o, 0) | int(f[2])
    for name, d in masks.items():
        if name in packed: continue
        cgroups = {o & ~3 for o, m in d.items() if m & 7}
        for o in list(d):
            d[o] = (d[o] & 7) if (o & ~3) in cgroups else (d[o] >> 3)
    return masks

def convert(buf, masks, lo=0, hi=None):
    """turns the 16- and 32-bit numbers in buf[lo:hi] round, by how the game reads them"""
    hi = len(buf) if hi is None else hi
    groups = sorted({(o & ~3) for o in masks if lo <= o < hi})
    for a in groups:
        m = [masks.get(a + k, 0) for k in range(4)]
        if a + 4 > hi or a < lo: continue
        if (m[0] | m[1]) & 2 or (m[2] | m[3]) & 2:
            # 16-bit reads win over 32-bit ones (structures of halves get copied as words);
            if (m[0] | m[1]) & 2 or m[0] & 4: buf[a], buf[a + 1] = buf[a + 1], buf[a]
            if (m[2] | m[3]) & 2 or m[0] & 4: buf[a + 2], buf[a + 3] = buf[a + 3], buf[a + 2]
        elif m[0] & 4:
            buf[a:a + 4] = buf[a:a + 4][::-1]

def bitmap_at(raw, o):
    """the size of the N64 bitmap (N64BMP.C) at raw[o:], or 0 if it doesn't look like one"""
    if o + 16 > len(raw): return 0
    fmt, pal = raw[o], raw[o + 1]
    doff, aoff, w, h, xh, yh, stride = struct.unpack_from(">HHhhhhH", raw, o + 2)
    kind = fmt & 0xF
    if kind > 3 or fmt & 0x80 or not (0 < w <= 1024 and 0 < h <= 1024): return 0
    bpp = (0.5, 1, 2, 4)[kind]
    if stride < w * bpp or stride > w * bpp + 16: return 0
    if doff < 16 or o + doff + stride * h > len(raw): return 0
    if kind < 2 and fmt & 0x10 and doff < 16 + (pal + 1) * 2: return 0
    return doff + stride * h

def convert_bitmap(out, raw, o):
    """an N64 bitmap, by its own layout: the header, the palette (16-bit), the texels"""
    fmt, pal = raw[o], raw[o + 1]
    doff, aoff, w, h, xh, yh, stride = struct.unpack_from(">HHhhhhH", raw, o + 2)
    out[o:o + 2] = raw[o:o + 2]
    struct.pack_into("<HHhhhhH", out, o + 2, doff, aoff, w, h, xh, yh, stride)
    kind = fmt & 0xF
    if fmt & 0x10:
        n = min((pal + 1), (doff - 16) // 2)
        for i in range(n):
            p = o + 16 + 2 * i
            out[p:p + 2] = raw[p:p + 2][::-1]
    t = o + doff
    if kind == 2:
        for i in range(0, stride * h, 2): out[t + i:t + i + 2] = raw[t + i:t + i + 2][::-1]
    elif kind == 3:
        for i in range(0, stride * h, 4): out[t + i:t + i + 4] = raw[t + i:t + i + 4][::-1]
    else:
        out[t:t + stride * h] = raw[t:t + stride * h]

SEQ_SIZE = {0x80: 4, 0x81: 4, 0x82: 4, 0x93: 14, 0x94: 10, 0x95: 10}
for c in range(0xA0, 0xA8): SEQ_SIZE[c] = 6
for c in (0xB0, 0xB1, 0xB6, 0xB7, 0xBA, 0xBB, 0xC0, 0xFE): SEQ_SIZE[c] = 2
for c in (0xB2, 0xB3, 0xB4, 0xB5, 0xB8, 0xB9, 0xBC, 0xBD, 0xBE, 0xBF, 0xC4, 0xC5, 0xC8, 0xC9): SEQ_SIZE[c] = 4

def convert_struct(out, raw, d, layout):
    """a structure the C declares (offset, size per item): bytes stay, 16/32-bit items turn round"""
    for o, n in layout:
        a = d + o
        if a + n > len(raw): continue
        out[a:a + n] = raw[a:a + n] if n == 1 else raw[a:a + n][::-1]

def load_layout(path):
    if not os.path.exists(path): return []
    return [tuple(int(x, 16) if i == 0 else int(x) for i, x in enumerate(l.split()))
            for l in open(path) if l.strip() and not l.startswith("#")]

# a move record (CF3CHAR.C, size-prefixed, from CharDef offset1C): (offset, size) of each
# 16/32-bit item; everything else is bytes (and the move's name, at the u16 at 4)
MOVE_ITEMS = ([(0x00, 4), (0x04, 2)] + [(o, 2) for o in range(0x1A, 0x30, 2)]
              + [(o, 2) for o in range(0x32, 0x56, 2) if o != 0x3A]
              + [(o, 2) for o in range(0x58, 0x6E, 2)] + [(o, 2) for o in range(0x86, 0xCA, 2)]
              + [(0xCC, 4), (0xD0, 4)])
VARIANT_ITEMS = [(0, 2), (2, 2), (4, 2), (6, 2), (8, 2), (0xA, 2), (0x12, 2),
                 (0x14, 4), (0x18, 4), (0x1C, 4), (0x20, 4)]

def convert_moves(out, raw, d):
    """the move list: each record by MOVE_ITEMS, its input steps (u16), its variants"""
    n, off = struct.unpack_from(">iI", raw, d + 0x18)
    p = d + off
    for _ in range(max(0, min(n, 1000))):
        if p + 0x30 > len(raw): return
        size = struct.unpack_from(">i", raw, p)[0]
        ns, so, nv, vo = struct.unpack_from(">hHhH", raw, p + 0x28)
        lo = min(so, vo)
        items = [(o, k) for o, k in MOVE_ITEMS if o + k <= lo]
        items += [(so + 2 * i, 2) for i in range(max(ns, 0))]
        items += [(vo + 0x24 * j + o, k) for j in range(max(nv, 0)) for o, k in VARIANT_ITEMS]
        if 0 < size and p + size <= len(raw): out[p:p + size] = raw[p:p + size]
        for o, k in items:
            a = p + o
            if a + k <= len(raw) and o + k <= size: out[a:a + k] = raw[a:a + k][::-1]
        if size <= 0: return
        p += size

# a projectile's launch record (CF3CHAR.C, 0x44 bytes, from CharDef num28/offset2C): s16 x 14,
# 4 bytes, f32 x 9; a debris record (num20/offset24, 16 bytes): an s16 frame number first
PROJ_ITEMS = [(o, 2) for o in range(0, 0x1C, 2)] + [(o, 4) for o in range(0x20, 0x44, 4)]
DEBRIS_ITEMS = [(0, 2)]

def convert_records(out, raw, d, at, size, items):
    n, off = struct.unpack_from(">iI", raw, d + at)
    if not (0 < n < 1000) or d + off + n * size > len(raw): return
    for i in range(n):
        p = d + off + i * size
        out[p:p + size] = raw[p:p + size]
        for o, k in items: out[p + o:p + o + k] = raw[p + o:p + o + k][::-1]

def convert_frame_rects(out, raw, d):
    """each animation frame's body rectangles (CF3CHAR.C): a u16 offset per frame, then at each
    offset s16 n and n x 4 s16"""
    n, off = struct.unpack_from(">iI", raw, d + 0x10)
    if not (0 < n < 5000) or d + off + 2 * n > len(raw): return
    offs = struct.unpack_from(f">{n}H", raw, d + off)
    struct.pack_into(f"<{n}H", out, d + off, *offs)
    for o in set(offs):
        p = d + o
        if not o or p + 2 > len(raw): continue
        k = struct.unpack_from(">h", raw, p)[0]
        if not (0 <= k <= 64): continue
        for j in range(0, 2 + 8 * k, 2):
            if p + j + 2 <= len(raw): out[p + j:p + j + 2] = raw[p + j:p + j + 2][::-1]

def convert_sequences(out, raw, d):
    """a fighter's animation sequences (CF3SEQ.C) at CharDef d: command bytes stay, the 16-bit
    operands turn round (the C reads some command bytes two at a time, so the trace can't tell)"""
    n, so = struct.unpack_from(">iI", raw, d + 8)
    if not (0 < n < 2000) or d + so + 2 * n > len(raw): return
    offs = struct.unpack_from(f">{n}H", raw, d + so)
    struct.pack_into(f"<{n}H", out, d + so, *offs)
    for off in set(offs):
        if not off: continue
        p = d + off
        if p + 16 > len(raw): continue
        for k in range(0, 10, 2): out[p + k:p + k + 2] = raw[p + k:p + k + 2][::-1]   # the header:
        out[p + 10:p + 16] = raw[p + 10:p + 16]                                     # u16 x 5, bytes x 6
        p += 16
        while p < len(raw):
            b = raw[p]
            size = 6 if b < 0x80 else SEQ_SIZE.get(b, 0) if b != 0xFF else -1
            if size <= 0:
                out[p] = raw[p]
                break
            out[p:p + 2] = raw[p:p + 2]
            for k in range(2, size, 2):
                out[p + k:p + k + 2] = raw[p + k:p + k + 2][::-1]
            p += size

# N64BMP.C's files (func_80080360) the C names (its other ones come from tables, read below)
CONTAINERS = [0xE20D00, 0xF4D8B0, 0xF53700, 0xF54510, 0xF56270, 0xF58F00, 0xF9B7B0, 0xF9BB20,
              0xFAA000, 0xFC15D0, 0xFD3010]

def container_header(rom, x):
    """the 12-byte header: 4 bytes (the 3rd: how many times packed), a count the C reads as a
    number, and the size, which the C reads as big-endian bytes"""
    h = bytearray(rom[x:x + 12])
    struct.pack_into("<I", h, 4, struct.unpack_from(">I", rom, x + 4)[0])
    return h

def convert_container(data, raw, count):
    """the (offset, size) table and the pictures in an N64BMP.C file (data = after the header;
    the offsets count from the header)"""
    if not (0 < count < 512) or 8 * count > len(raw): return
    for i in range(count):
        off, size = struct.unpack_from(">II", raw, 8 * i)
        struct.pack_into("<II", data, 8 * i, off, size)
        o = off - 12
        if 0 <= o < len(raw) and bitmap_at(raw, o): convert_bitmap(data, raw, o)

class Unpacker:
    def __init__(self, tool):
        self.tool = tool
        self.tmp = tempfile.mkdtemp()
        self.rom_path = None
    def unpack(self, data, off):
        o = os.path.join(self.tmp, "out.bin")
        if self.rom_path is not None and data is self.rom:
            i = self.rom_path
        else:
            i = os.path.join(self.tmp, "in.bin")
            open(i, "wb").write(data)
        subprocess.check_call([self.tool, i, str(off), o])
        return open(o, "rb").read()

def stored(data):
    """an MPF block holding data as it is (type 0)"""
    return struct.pack(">BBHII", 0, 0, 12, 12 + len(data), len(data)) + bytes(data)

def main():
    rom_path, trace_path, tool, out_path = sys.argv[1:5]
    global CHARDEF
    CHARDEF = load_layout(CHARDEF_PATH)
    rom = open(rom_path, "rb").read()
    masks = load_trace(trace_path) if os.path.exists(trace_path) else {}
    up = Unpacker(tool)
    up.rom, up.rom_path = rom, rom_path
    img = bytearray(rom)
    convert(img, masks.get("rom", {}))
    moved = []                # (rom start, rom end, data)
    vnext = [VIRTUAL]
    def virtual(data):
        a = vnext[0]
        vnext[0] = (a + len(data) + 15) & ~15
        moved.append((a, a + len(data), data))
        return a
    handled = set()           # streams dealt with by a loader below

    def unpack_chain(addr, passes):
        """unpack the block at ROM addr 'passes' times; the result and its trace name"""
        data = up.unpack(rom, addr)
        name = f"unpack:rom@{addr:X}"
        handled.add(name)
        for _ in range(passes - 1):
            data = up.unpack(data, 0)
            name = f"unpack:{name}@0"
            handled.add(name)
        out = bytearray(data)
        convert(out, masks.get(name, {}))
        unpack_chain.raw = data
        return out

    # ---- the characters: the header points at a packed block; it becomes a stored one
    chars = sorted(set(data_words("D_CharacterRomTable", 17, rom)))
    for base in chars:
        if rom[base:base + 8] == b"nullchar": continue
        po, psize, passes = struct.unpack_from(">IIi", rom, base + 0x20)
        ft = struct.unpack_from(">I", rom, base + 0x08)[0]
        nframes = struct.unpack_from(">h", rom, base + 0x02)[0]
        data = unpack_chain(base + po, passes)
        raw = unpack_chain.raw
        mv = struct.unpack_from(">I", rom, base + 4)[0] - 0x40
        if 0 <= mv and mv + 4 <= len(data):
            convert_struct(data, raw, mv, CHARDEF)    # the CharDef: its own layout
            convert_sequences(data, raw, mv)
            convert_frame_rects(data, raw, mv)
            convert_moves(data, raw, mv)
            convert_records(data, raw, mv, 0x28, 0x44, PROJ_ITEMS)
            convert_records(data, raw, mv, 0x20, 16, DEBRIS_ITEMS)
        blk = stored(data)
        va = virtual(blk)
        newpo = (va - base) & 0xFFFFFFFF
        # the alpha streams' offsets are from the packed block: move them with it
        # the frame table (rom offset, size, alpha offset: u32s) is a known structure: all of it
        # (a test only reads the entries of the frames it shows); the alpha streams' offsets are
        # from the packed block, so they move with it
        for i in range(nframes):
            o = ft - 0x40 + i * 12
            if 0 <= o and o + 12 <= len(data):
                a, b, c = struct.unpack_from(">III", raw, o)
                struct.pack_into("<III", data, o, a, b, (c + newpo - po) & 0xFFFFFFFF)
                # the frame itself (read from the ROM as it is): a byte stream (CHARFRAM.C reads
                # bits from it a byte at a time, which the compiler can merge into wider reads)
                if base + a + b <= len(rom): img[base + a:base + a + b] = rom[base + a:base + a + b]
        # the alpha streams (run-length bytes) are the end of the block; the colours are 16-bit
        al = [struct.unpack_from(">I", raw, ft - 0x40 + i * 12 + 8)[0] - po for i in range(nframes)
              if 0 <= ft - 0x40 + i * 12 + 12 <= len(raw)]
        al = [x for x in al if 0 < x < len(raw)]
        if al: data[min(al):] = raw[min(al):]
        co, pages = struct.unpack_from(">II", rom, base + 0x10)[0] - 0x40, struct.unpack_from(">I", rom, base + 0x0C)[0]
        if 0 <= co and co + pages * 128 <= len(raw) and pages < 64:
            for i in range(pages * 64):
                data[co + 2 * i:co + 2 * i + 2] = raw[co + 2 * i:co + 2 * i + 2][::-1]
        moved[-1] = (va, va + len(blk), stored(data))
        # the header (CharFileHeader, char.h): u16, s16, then 32-bit fields
        struct.pack_into("<Hh15I", img, base, *struct.unpack_from(">Hh15I", rom, base))
        struct.pack_into("<IIi", img, base + 0x20, newpo, len(blk), 1)

    # ---- the arena banks: packed objects and textures inside the file become stored ones
    banks = sorted(set(data_words("D_800AB150", 26, rom)))
    overrides = []            # (rom start, rom end, new data)
    for base in banks:
        if rom[base:base + 7].lower() == b"nullbkg": continue
        hdr = struct.unpack_from(">IiIIiIiIiIiI", rom, base)
        nobj, fsize, ntex, texoff = hdr[1], hdr[2], hdr[4], hdr[5]
        # from the ROM's own bytes: everything that isn't one of the structures below is a bit
        # stream (read a byte at a time), which the trace's word reads must not turn round
        f = bytearray(rom[base:base + fsize])
        # the header and the tables are known structures: written from the C's own layout
        # (the trace can't always tell, e.g. where a test read only part of a field)
        struct.pack_into("<12I", f, 0, *struct.unpack_from(">12I", rom, base))
        for i in range(nobj):
            o = 0x3C + i * 12
            f[o:o + 4] = rom[base + o:base + o + 4]
            struct.pack_into("<II", f, o + 4, *struct.unpack_from(">II", rom, base + o + 4))
        for i in range(ntex):
            o = texoff + i * 20
            f[o:o + 4] = rom[base + o:base + o + 4]
            struct.pack_into("<hh", f, o + 4, *struct.unpack_from(">hh", rom, base + o + 4))
            f[o + 8:o + 12] = rom[base + o + 8:base + o + 12]
            struct.pack_into("<II", f, o + 12, *struct.unpack_from(">II", rom, base + o + 12))
        struct.pack_into("<3i", f, 0x30, *struct.unpack_from(">3i", rom, base + 0x30))   # bias
        # the models (BkgFileModel, arena.h), their cells, material texture numbers and points
        def swap(o, k):
            if o + k <= fsize: f[o:o + k] = rom[base + o:base + o + k][::-1]
        for i in range(nobj):
            o = 0x3C + i * 12
            if rom[base + o] != 0: continue
            m = struct.unpack_from(">I", rom, base + o + 4)[0]
            if m + 0x8C > fsize: continue
            f[m:m + 4] = rom[base + m:base + m + 4]
            for k in range(4, 0x8C, 4): swap(m + k, 4)
            gx, gy, gz, subs, cells = struct.unpack_from(">iiiiI", rom, base + m + 0x50)
            nmat, matoff = struct.unpack_from(">iI", rom, base + m + 0x6C)
            npts, ptsoff = struct.unpack_from(">iI", rom, base + m + 0x7C)
            if 0 <= gx * gy * gz < 100000 and 0 <= subs < 64:
                csize = 0x14 + subs * 8
                for c in range(gx * gy * gz):
                    cc = cells + c * csize
                    if cc + csize > fsize: break
                    f[cc:cc + csize] = rom[base + cc:base + cc + csize]
                    for k in range(0, 0x12, 2): swap(cc + k, 2)
                    for k in range(0x14, csize, 2): swap(cc + k, 2)
            for k in range(max(0, min(nmat, 10000))): swap(matoff + 2 * k, 2)
            for k in range(max(0, min(npts, 100000))): swap(ptsoff + 2 * k, 2)
        extra = bytearray()
        def add_stream(src_field_off, src, palette=0):
            data = unpack_chain(base + src, 1)
            raw = unpack_chain.raw
            if palette:
                data[:] = raw
                for k in range(0, min(2 * palette, len(raw) & ~1), 2): data[k:k + 2] = raw[k:k + 2][::-1]
            elif bitmap_at(raw, 0): convert_bitmap(data, raw, 0)    # a picture standing in the arena
            pos = fsize + len(extra)
            extra.extend(stored(data))
            while len(extra) % 4: extra.append(0)
            struct.pack_into("<I", f, src_field_off, pos)
        for i in range(nobj):
            o = 0x3C + i * 12
            if rom[base + o] != 0:                      # packed
                add_stream(o + 4, struct.unpack_from(">I", rom, base + o + 4)[0])
        for i in range(ntex):
            o = texoff + i * 20
            # a texture (BkgFileTexture): its 16 or 256-colour palette (16-bit, RGBA5551) and then
            # the colour indices (bytes), by that layout and not by the trace
            src = struct.unpack_from(">I", rom, base + o + 12)[0]
            npal = 256 if rom[base + o] else 16
            add_stream(o + 12, src, palette=npal)
        new = f + extra
        struct.pack_into("<I", new, 8, len(new))         # fileSize
        overrides.append((base, base + fsize, bytes(new)))

    # ---- N64BMP.C's files (func_80080360): a 12-byte header (the 3rd byte: how many times
    # packed), then a table of (offset, size) pairs, one per picture (or font) in the file
    known = set(CONTAINERS + data_words("D_800AAFF0", 17, rom))
    for name in masks:
        m = re.match(r"unpack:rom@([0-9A-F]+)$", name)
        if m and name not in handled and int(m.group(1), 16) < len(rom): known.add(int(m.group(1), 16) - 12)
    done = set()
    for x in sorted(known):
        if x in done: continue
        done.add(x)
        passes = rom[x + 2]
        total = struct.unpack_from(">I", rom, x + 8)[0]
        if not (passes <= 4 and 12 < total < 0x200000):
            print(f"port_assets: no N64BMP.C file at {x:X}", file=sys.stderr)
            continue
        hdr = container_header(rom, x)
        if passes == 0:
            raw = rom[x + 12:x + total]
            data = bytearray(img[x + 12:x + total])
        else:
            data = unpack_chain(x + 12, passes)
            raw = unpack_chain.raw
        convert_container(data, raw, struct.unpack_from(">I", rom, x + 4)[0])
        if passes == 0:
            img[x:x + 12] = hdr
            img[x + 12:x + total] = data
        else:
            hdr[2] = 0
            struct.pack_into(">I", hdr, 8, 12 + len(data))
            overrides.append((x, x + total, bytes(hdr + data)))

    # ---- pictures read straight from the ROM: ARENADRAW.C's the arena grounds' dome, floor,
    # overlay and backdrop textures (tables of ROM addresses) and the stage sky's cloud
    pics = {0xD4D640}
    # ... and the pictures the screens copy straight from the ROM that no test reads: the intro's
    # final picture (the face over the island), the notice under every screen, a fight's picture
    pics.update({0xFC8D20, 0xFC8660, 0xD10A0})
    for lab, n in (("D_800AC3F0", 4), ("D_800AC410", 1), ("D_800AC418", 4), ("D_800AC438", 1)):
        pics.update(data_words(lab, n, rom))
    for x in sorted(pics):
        if bitmap_at(rom, x): convert_bitmap(img, rom, x)

    # ---- the arenas' definitions: the version bytes at the start stay bytes
    arenadef = load_layout(os.path.join(os.path.dirname(LAYOUT), "arenadef_layout.txt"))
    for x in sorted(set(data_words("D_ArenaGeometryFiles", 26, rom))):
        if x + 0x40 > len(rom) or rom[x:x + 2] != b"\x04\x0a": continue
        img[x:x + 2] = rom[x:x + 2]
        # the header's numbers (BKGLOAD.C: scale, parts, lights) and the lights (0x3C bytes: a
        # byte, then floats)
        for k in range(0x34, 0x48, 4): img[x + k:x + k + 4] = rom[x + k:x + k + 4][::-1]
        nl, lo = struct.unpack_from(">iI", rom, x + 0x40)
        for i in range(max(0, min(nl, 64))):
            r = x + lo + i * 0x3C
            if r + 0x3C > len(rom): break
            img[r:r + 4] = rom[r:r + 4]
            for k in range(4, 0x3C, 4): img[r + k:r + k + 4] = rom[r + k:r + k + 4][::-1]
        # the arenas in it (ArenaDef, its own layout) and their camera fly-throughs (32-bit)
        nparts, partsoff = struct.unpack_from(">iI", rom, x + 0x38)
        for i in range(max(0, min(nparts, 16))):
            a = x + partsoff + i * 0x60
            convert_struct(img, rom, a, arenadef)
            # its tables (CF3COMBT.C), offsets from the BkgDef: (count at, offset at, record size,
            # items); path points are 5 f32 (the ROM's count is an odd number: its top byte)
            for cat, oat, size, items in ((0x34, 0x38, 0x14, [(o, 4) for o in range(0, 0x14, 4)]),
                                          (0x1C, 0x20, 0x24, [(o, 4) for o in range(0, 0x20, 4)]),
                                          (0x40, 0x44, 0x24, [(o, 4) for o in range(0, 0x20, 4)]),
                                          (0x48, 0x4C, 0x18, [(o, 4) for o in range(0, 0x18, 4)]),
                                          (0x50, 0x54, 0x38, [(0, 2)] + [(o, 4) for o in range(0x1C, 0x38, 4)])):
                n, to = struct.unpack_from(">iI", rom, a + cat)[0], struct.unpack_from(">I", rom, a + oat)[0]
                if n >= 1 << 24: n >>= 24
                if not (0 < n < 1000) or x + to + n * size > len(rom): continue
                for j in range(n):
                    r = x + to + j * size
                    img[r:r + size] = rom[r:r + size]
                    for o, k in items: img[r + o:r + o + k] = rom[r + o:r + o + k][::-1]
            has, io = struct.unpack_from(">iI", rom, a + 0x58)
            if has and x + io + 4 <= len(rom):
                n = struct.unpack_from(">i", rom, x + io)[0]
                if 0 <= n < 1000:
                    for k in range(0, 0x2C + n * 12, 4):
                        p = x + io + k
                        img[p:p + 4] = rom[p:p + 4][::-1]
                    # (+8 is a byte: how the camera looks - ahead, at the fighters, at a point)
                    img[x + io + 8:x + io + 12] = rom[x + io + 8:x + io + 12]

    # ---- the file: directory, the converted ROM, then everything that moved
    entries, blobs = [], []
    overrides.sort()
    pos = 0
    for a, b, d in overrides:
        if a > pos: entries.append((pos, a, None))
        entries.append((a, b, d))
        pos = b
    entries.append((pos, len(rom), None))
    for a, b, d in moved: entries.append((a, b, d))
    head = 4 + 16 * len(entries)
    off = (head + 31) & ~31
    table, body = [], bytearray()
    img_off = off
    off += len(img)
    for a, b, d in entries:
        if d is None:
            table.append((a, b, img_off + a, b - a))
        else:
            off = (off + 31) & ~31
            table.append((a, b, off, len(d)))
            body += bytes((off - img_off - len(img) - len(body)))
            body += d
            off += len(d)
    with open(out_path, "wb") as fo:
        fo.write(struct.pack("<I", len(table)))
        for t in table: fo.write(struct.pack("<IIII", *t))
        fo.write(bytes(img_off - head))
        fo.write(img)
        fo.write(body)
    print(f"port_assets: {out_path}: {len(table)} ranges, {len(moved)} moved blocks, "
          f"{len(overrides)} rebuilt files, {os.path.getsize(out_path) // 1024} KB")

if __name__ == "__main__":
    main()
