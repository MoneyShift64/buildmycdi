#!/usr/bin/env python3
"""
port_sound.py - the game's sounds and music instruments for the Dreamcast, from your ROM.

Usage: port_sound.py baserom.us.z64 <mpf_unpack tool> out/SOUND.DAT

The N64 game keeps its sounds in libultra sound banks: a bank file (instruments, sounds,
envelopes, key maps, wave tables) and its sample data, compressed with the N64's VADPCM. Here
every sample is decoded and stored the way the Dreamcast's sound chip (the AICA) plays it:
  - sounds that loop (held notes): 16-bit (short ones) or 8-bit linear PCM, cut at the loop's
    end, since the AICA's ADPCM can't restart a loop cleanly
  - one-shot sounds: the AICA's 4-bit ADPCM
  - one-shot sounds longer than the AICA can play from one buffer (65534 samples): 4-bit
    ADPCM too, streamed while they play
The bank files themselves become a small little-endian form the port reads directly.

SOUND.DAT: "CFSD", the number of bank files, then for each: the ROM address of its sample data
(how the game names it), offset and size in this file; then the bank files.
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import port_assets                      # (data_words, the unpacker)

# the bank files the game loads by address (GAME.C, CHARLOAD.C): bank file, its samples
GLOBAL_BANKS = [(0xD53510, 0xD54AF0), (0xF1EF70, 0xF20530), (0xF040C0, 0xF055C0), (0xE221A0, 0xE26FF0)]

FMT_PCM16, FMT_PCM8, FMT_ADPCM, FMT_STREAM = 0, 1, 2, 3
AICA_MAX = 65534                         # samples one AICA channel can play from a buffer

def u32(b, o): return struct.unpack_from(">I", b, o)[0]

# ---- VADPCM (the N64's sample compression): 9-byte frames of 16 samples
def codebook(ctl, book):
    order, npred = struct.unpack_from(">ii", ctl, book)
    raw = struct.unpack_from(f">{order * npred * 8}h", ctl, book + 8)
    tables = []
    for p in range(npred):
        t = [[0] * (order + 8) for _ in range(8)]
        for j in range(order):
            for k in range(8): t[k][j] = raw[(p * order + j) * 8 + k]
        for k in range(1, 8): t[k][order] = t[k - 1][order - 1]
        t[0][order] = 1 << 11
        for k in range(1, 8):
            for j in range(k): t[j][k + order] = 0
            for j in range(k, 8): t[j][k + order] = t[j - k][order]
        tables.append(t)
    return order, tables

def vadpcm_decode(data, order, tables, nsamples):
    out = []
    state = [0] * 16
    for f in range(0, len(data) - 8, 9):
        h = data[f]
        scale = 1 << (h >> 4)
        tab = tables[h & 15] if (h & 15) < len(tables) else tables[0]
        ix = []
        for b in data[f + 1:f + 9]:
            for v in (b >> 4, b & 15):
                ix.append((v - 16 if v >= 8 else v) * scale)
        for j in range(2):
            vec = state[16 - order:16] if j == 0 else state[8 - order:8]
            vec = list(vec)
            for i in range(8):
                ind = j * 8 + i
                coef = tab[i]
                acc = 0
                for k in range(order + i): acc += coef[k] * vec[k]
                v = (acc >> 11) + ix[ind]            # (floor, as the SDK's decoder)
                vec.append(ix[ind])
                state[ind] = v
        for v in state: out.append(-32768 if v < -32768 else 32767 if v > 32767 else v)
        if len(out) >= nsamples: break
    return out[:nsamples]

# ---- the AICA's ADPCM (Yamaha, as KallistiOS's wav2adpcm; public domain code by superctr)
STEP = (230, 230, 230, 230, 307, 409, 512, 614)
def aica_adpcm(pcm):
    if len(pcm) & 1: pcm = pcm + [0]
    out = bytearray(len(pcm) // 2)
    step, hist = 127, 0
    for i, s in enumerate(pcm):
        d = (s & -8) - hist
        a = (abs(d) << 16) // (step << 14)
        a = 7 if a > 7 else a
        if d < 0: a |= 8
        if i & 1: out[i >> 1] |= a << 4
        else: out[i >> 1] = a
        # the decoder's step (so the encoder follows what will be heard)
        delta = a & 7
        diff = ((1 + (delta << 1)) * step) >> 3
        diff = 32767 if diff > 32767 else diff
        hist = hist - diff if a & 8 else hist + diff
        hist = -32768 if hist < -32768 else 32767 if hist > 32767 else hist
        step = (STEP[delta] * step) >> 8
        step = 127 if step < 127 else 24576 if step > 24576 else step
    return bytes(out)

def convert_wave(rom, ctl, tbl, wt):
    """(format, data, samples, loop start, loop end, loop count) for a wave table"""
    base, length = struct.unpack_from(">II", ctl, wt)
    kind = ctl[wt + 8]
    loopoff = u32(ctl, wt + 12)
    src = rom[tbl + base:tbl + base + length]
    if kind == 0:
        order, tables = codebook(ctl, u32(ctl, wt + 16))
        n = length // 9 * 16
        pcm = vadpcm_decode(src, order, tables, n)
    else:                                               # (raw 16-bit, big-endian)
        pcm = list(struct.unpack(f">{len(src) // 2}h", src[:len(src) & ~1]))
    ls = le = 0
    count = 0
    if loopoff:
        ls, le, count = struct.unpack_from(">IIi", ctl, loopoff)
        if count == 0 or le <= ls or le > len(pcm): count = 0
    if count:                                           # looped: played to the loop's end, ever after
        pcm = pcm[:le]
        if len(pcm) > AICA_MAX:                         # (none in this game)
            pcm = pcm[:AICA_MAX]; le = AICA_MAX
        if len(pcm) <= 16384:
            return FMT_PCM16, struct.pack(f"<{len(pcm)}h", *pcm), len(pcm), ls, le, -1
        return FMT_PCM8, bytes(((v >> 8) & 0xFF) for v in pcm), len(pcm), ls, le, -1
    fmt = FMT_ADPCM if len(pcm) <= AICA_MAX else FMT_STREAM
    return fmt, aica_adpcm(pcm), len(pcm), 0, 0, 0

def convert_bank_file(rom, ctl, tbl):
    """the bank file in the port's form (see the header of port/audio.c)"""
    rev, nbanks = struct.unpack_from(">hh", ctl, 0)
    waves, wave_index = [], {}
    banks = []
    for b in range(nbanks):
        bo = u32(ctl, 4 + 4 * b)
        ninst, = struct.unpack_from(">h", ctl, bo)
        rate, perc = struct.unpack_from(">iI", ctl, bo + 4)
        insts = []
        for io in [perc] + [u32(ctl, bo + 12 + 4 * i) for i in range(ninst)]:
            if io == 0: insts.append(None); continue
            vol, pan, pri, fl = ctl[io:io + 4]
            bend, ns = struct.unpack_from(">hh", ctl, io + 12)
            sounds = []
            for s in range(ns):
                so = u32(ctl, io + 16 + 4 * s)
                env, km, wt = struct.unpack_from(">III", ctl, so)
                span, svol, sfl = ctl[so + 12:so + 15]
                at, dt, rt = struct.unpack_from(">iii", ctl, env)
                av, dv = ctl[env + 12:env + 14]
                if wt not in wave_index:
                    wave_index[wt] = len(waves)
                    waves.append(convert_wave(rom, ctl, tbl, wt))
                sounds.append((at, dt, rt, av, dv, span, svol, bytes(ctl[km:km + 6]), sfl, wave_index[wt]))
            insts.append((vol, pan, pri, fl, bend, sounds))
        banks.append((rate, insts))

    # lay it out: header, banks, instruments, sounds, waves, sample data (all 4-aligned)
    out = bytearray()
    def here(): return len(out)
    out += b"PBNK" + struct.pack("<II", nbanks, len(waves)) + bytes(4) + bytes(4 * nbanks)
    for b, (rate, insts) in enumerate(banks):
        struct.pack_into("<I", out, 16 + 4 * b, here())
        bo = here()
        out += struct.pack("<iiI", len(insts) - 1, rate, 0) + bytes(4 * (len(insts) - 1))
        for i, inst in enumerate(insts):
            if inst is None: continue
            io = here()
            vol, pan, pri, fl, bend, sounds = inst
            out += struct.pack("<BBBBhh", vol, pan, pri, fl, bend, len(sounds)) + bytes(4 * len(sounds))
            for s, (at, dt, rt, av, dv, span, svol, km, sfl, wi) in enumerate(sounds):
                struct.pack_into("<I", out, io + 8 + 4 * s, here())
                out += struct.pack("<iiiBBBB", at, dt, rt, av, dv, span, svol) + km + struct.pack("<BBI", sfl, 0, wi)
            if i == 0: struct.pack_into("<I", out, bo + 8, io)          # the percussion
            else: struct.pack_into("<I", out, bo + 12 + 4 * (i - 1), io)
    struct.pack_into("<I", out, 12, here())
    wt = here()
    out += bytes(28 * len(waves))
    for i, (fmt, data, n, ls, le, count) in enumerate(waves):
        while len(out) & 31: out.append(0)
        struct.pack_into("<IIIIIiI", out, wt + 28 * i, here(), len(data), n, ls, le, count, fmt)
        out += data
    while len(out) & 31: out.append(0)
    return bytes(out)

def main():
    rom_path, tool, out_path = sys.argv[1:4]
    rom = open(rom_path, "rb").read()
    up = port_assets.Unpacker(tool)
    up.rom, up.rom_path = rom, rom_path
    files = []                                          # (sample data address, bank file)
    for ctl, tbl in GLOBAL_BANKS:
        files.append((tbl, rom[ctl:tbl]))
    # each fighter's own bank file is in its packed block; its samples are in the ROM after it
    for base in sorted(set(port_assets.data_words("D_CharacterRomTable", 17, rom))):
        if rom[base:base + 8] == b"nullchar": continue
        bank, brom, po, psize, passes = struct.unpack_from(">IIIIi", rom, base + 0x18)
        if bank == 0: continue
        data = up.unpack(rom, base + po)
        for _ in range(passes - 1): data = up.unpack(data, 0)
        files.append(((base + brom) & 0xFFFFFFFF, data[bank - 0x40:]))
    blobs = []
    for tbl, ctl in files:
        blobs.append((tbl, convert_bank_file(rom, ctl, tbl)))
    head = b"CFSD" + struct.pack("<I", len(blobs))
    off = (len(head) + 12 * len(blobs) + 31) & ~31
    table, body = bytearray(), bytearray()
    for tbl, blob in blobs:
        table += struct.pack("<III", tbl, off + len(body), len(blob))
        body += blob
    f = head + table
    f += bytes(off - len(f))
    open(out_path, "wb").write(f + body)
    print(f"port_sound: {out_path}: {len(blobs)} bank files, {len(f + body) // 1024} KB")

if __name__ == "__main__":
    main()
