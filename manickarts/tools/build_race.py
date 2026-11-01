#!/usr/bin/env python3
"""build_race.py - the race tables out of K.EXE, for src/powerup.c: data/race.bin

    python3 tools/build_race.py        (build_dc.sh runs it when race.bin is missing)

    "MKRC", u32 tracks (16)
    s32 pitAward[4]          time in the pit lane (ms / 2) that earns a power-up, by engine
                             class (K.EXE 0xba2a0)
    then for each track (the track table at 0xbaf44, 0x342 bytes each):
    u8  pitNoBrake, pad[3]   track +0x119: the computer drivers don't brake in the pit lane
    s16 ai[4][16]            track +0x11a: by engine class, the computer drivers' grip (0..7)
                             and power (8..15) by race position (FUN_43978)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_assets as b                    # noqa: E402


def main():
    exe, base = b.exe_tables()
    out = b"MKRC" + struct.pack("<I", 16)
    out += struct.pack("<4i", *struct.unpack_from("<4i", exe, base + 0xba2a0))
    for t in range(16):
        raw = exe[base + 0xbaf44 + t * 0x342:base + 0xbaf44 + (t + 1) * 0x342]
        out += struct.pack("<B3x", raw[0x119])
        for c in range(4):
            p = struct.unpack_from("<I", raw, 0x11a + 4 * c)[0]
            a = base + (p if p >= 0xb0000 else 0xb0000 + p)
            out += struct.pack("<16h", *struct.unpack_from("<16h", exe, a))
    for c, a in enumerate((0xb9f14, 0xb9f64, 0xb9fb4, 0xba004)):     # shop prices by class (items 0..5)
        out += struct.pack("<6i", *struct.unpack_from("<6i", exe, base + a))
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "race.bin")
    open(path, "wb").write(out)
    print("race tables -> data/race.bin (%d bytes)" % len(out))


if __name__ == "__main__":
    main()
