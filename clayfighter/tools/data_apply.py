"""data_apply.py - puts the game's data (data.S, made from the visitor's ROM by the port's own
tools/port_data.py) into the prebuilt program, in place of the zeros it was published with.
It does the assembler's job for data.S: each .byte, .short and .long with a number is written
(little-endian, as on the Dreamcast) at its place; a .long naming a symbol (a pointer) is left
as it is in the program, where the linker already put it.

    data_apply.py data.S program.bin offset          writes the data in
    data_apply.py data.S program.bin offset --zero   (publishing) writes zeros there instead
"""
import re, sys

SIZE = {"byte": 1, "short": 2, "long": 4}

def apply(data_s, prog, base, zero=False):
    at = 0
    for line in data_s.splitlines():
        m = re.match(r"\s*\.(byte|short|long)\s+(.*)", line)
        if not m:
            if re.match(r"\s*\.(balign|align|space|zero|skip|word|int|float)\b", line) and at != 0:
                raise SystemExit("data_apply: unexpected line in data.S: " + line.strip())
            continue
        n = SIZE[m.group(1)]
        for v in (x.strip() for x in m.group(2).split(",")):
            if re.match(r"^-?(0x[0-9A-Fa-f]+|\d+)$", v):
                val = 0 if zero else int(v, 0) & ((1 << (8 * n)) - 1)
                prog[base + at:base + at + n] = val.to_bytes(n, "little")
            at += n
    return at

if __name__ == "__main__":
    s, p, off = sys.argv[1], sys.argv[2], int(sys.argv[3], 0)
    prog = bytearray(open(p, "rb").read())
    used = apply(open(s).read(), prog, off, "--zero" in sys.argv)
    open(p, "wb").write(prog)
    print("data_apply: %d bytes of data at 0x%X" % (used, off))
