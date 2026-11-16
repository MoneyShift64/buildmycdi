#!/usr/bin/env python3
"""Makes the game's data (asm/data.s, 0x800A41B0-0x800D04A0) for a little-endian port.

Usage: port_data.py asm/data.s data_trace.txt defined_syms.txt out.S

The N64 is big-endian, the Dreamcast (and the PC test build) little-endian. asm/data.s
writes everything as 32-bit words, which is only right for data the code reads as
32-bit words. Each word is written out by how the C code reads it:
  - read as 32-bit words (or never read, or a pointer)  -> .long
  - read as 16-bit halves                                -> .short x 2
  - read as bytes (text, byte tables)                    -> .byte x 4
How each byte is read comes from data_trace.txt (recorded by running the C in the MIPS
emulator with CFSC_TRACE set), and where that has nothing, from the type the C declares
the data with (extern s16 D_... etc).

Pointers to code or data that the port doesn't have (libultra, asm jump tables) become 0.
The output goes through the C preprocessor: SYM(x) adds the leading underscore that SH-4
symbols have.
"""
import re, sys, os, glob

def c_types(src_dir):
    """symbol -> element size, from the C's extern declarations of basic types"""
    size = {"u8": 1, "s8": 1, "char": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4}
    out = {}
    for f in glob.glob(os.path.join(src_dir, "**", "*.[ch]"), recursive=True):
        t = re.sub(r"/\*.*?\*/", "", open(f, errors="replace").read(), flags=re.S)
        for decl in re.findall(r"\bextern\s+([^;(]*);", t):
            words = re.sub(r"\b(const|volatile|unsigned|signed)\b", " ", decl).split(None, 1)
            if len(words) < 2 or words[0] not in size: continue
            for d in words[1].split(","):
                m = re.match(r"\s*(\**)\s*([A-Za-z_]\w*)", d)
                if m: out.setdefault(m.group(2), 4 if m.group(1) else size[words[0]])
    return out

def c_arrays(src_dir):
    """symbol -> (element size, total bytes) for the C's extern arrays of bytes and halves"""
    size = {"u8": 1, "s8": 1, "char": 1, "u16": 2, "s16": 2}
    out = {}
    for f in glob.glob(os.path.join(src_dir, "**", "*.[ch]"), recursive=True):
        t = re.sub(r"/\*.*?\*/", "", open(f, errors="replace").read(), flags=re.S)
        for m in re.finditer(r"\bextern\s+(?:const\s+)?(u8|s8|char|u16|s16)\s+([A-Za-z_]\w*)((?:\s*\[\s*\d+\s*\])+)\s*;", t):
            n = 1
            for d in re.findall(r"\d+", m.group(3)): n *= int(d)
            out[m.group(2)] = (size[m.group(1)], n * size[m.group(1)])
    return out

BASIC = {"u8": 1, "s8": 1, "char": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4,
         "int": 4, "unsigned int": 4, "short": 2, "unsigned short": 2, "unsigned char": 1,
         "signed char": 1, "float": 4, "OSMesg": 4, "OSId": 4, "u64": 8, "s64": 8, "f64": 8}

def record_layouts(src_dir):
    """struct name -> (size, [(offset, type, field name)]) from clang's -fdump-record-layouts"""
    import subprocess
    res = subprocess.run(["clang", "-print-resource-dir"], capture_output=True, text=True).stdout.strip()
    recs = {}
    for f in glob.glob(os.path.join(src_dir, "**", "*.c"), recursive=True):
        cmd = ["clang", "-m32", "-fsyntax-only", "-Xclang", "-fdump-record-layouts", "-nostdinc",
               "-isystem", "/opt/musl32/include", "-isystem", res + "/include", "-DTARGET_PORT", "-w",
               "-Isrc/include", "-Isrc/mpf", "-Isrc/char", "-Isrc/game", "-Isrc/n64", "-Isrc/arena", f]
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        for block in out.split("*** Dumping AST Record Layout")[1:]:
            lines = [l for l in block.splitlines() if "|" in l]
            m = re.match(r"\s*0 \| (?:struct|union) (\w+)", lines[0]) if lines else None
            if not m: continue
            name, fields = m.group(1), []
            union = " union " in lines[0]
            size = None
            for l in lines[1:]:
                off, rest = l.split("|", 1)
                ms = re.search(r"sizeof=(\d+)", rest)
                if ms: size = int(ms.group(1)); continue
                if not off.strip() or not rest.startswith("   ") or rest.startswith("     "): continue
                mf = re.match(r"\s+(.*?)\s*(\w+)$", rest)
                if mf: fields.append((int(off), mf.group(1).strip(), mf.group(2)))
            if size and name not in recs: recs[name] = (size, fields, union)
    return recs

def type_units(t, recs, depth=0):
    """[(offset, size)] of the basic items in type t (None where unknown)"""
    t = t.replace("const ", "").replace("volatile ", "").replace("struct ", "").strip()
    if t.endswith("*") or "(*)" in t: return [(0, 4)]
    m = re.match(r"(.*)\[(\d+)\]$", t)
    if m:
        inner = type_units(m.group(1), recs, depth + 1)
        if inner is None: return None
        isz = type_size(m.group(1), recs)
        if isz is None: return None
        return [(i * isz + o, s) for i in range(int(m.group(2))) for o, s in inner]
    if t in BASIC: return [(0, BASIC[t])]
    if t in recs and depth < 8:
        size, fields, union = recs[t]
        if union: return None
        out = []
        for off, ft, fname in fields:
            if re.match(r"u8\[\d+\]$", ft) and re.match(r"(pad|unk|raw|_|v[0-9A-F]+$|b[0-9A-F]+$)", fname) \
                    and int(ft[3:-1]) > 4:
                continue                    # padding of unknown use: leave to the trace
            u = type_units(ft, recs, depth + 1)
            if u: out.extend((off + o, s) for o, s in u)
        return out
    return None

def type_size(t, recs):
    t = t.replace("const ", "").replace("volatile ", "").replace("struct ", "").strip()
    if t.endswith("*"): return 4
    m = re.match(r"(.*)\[(\d+)\]$", t)
    if m:
        s = type_size(m.group(1), recs); return s * int(m.group(2)) if s else None
    if t in BASIC: return BASIC[t]
    if t in recs: return recs[t][0]
    return None

def struct_externs(src_dir, recs):
    """symbol -> {offset: size} for data declared as structures"""
    out = {}
    for f in glob.glob(os.path.join(src_dir, "**", "*.[ch]"), recursive=True):
        t = re.sub(r"/\*.*?\*/", "", open(f, errors="replace").read(), flags=re.S)
        for decl in re.findall(r"\bextern\s+([^;(]*);", t):
            words = re.sub(r"\b(const|volatile|struct)\b", " ", decl).split(None, 1)
            if len(words) < 2 or words[0] not in recs: continue
            for d in words[1].split(","):
                m = re.match(r"\s*(\**)\s*([A-Za-z_]\w*)\s*((?:\[\w*\])*)", d)
                if not m or m.group(1) or m.group(2) in out: continue
                ty = words[0]
                dims = re.findall(r"\[(\w*)\]", m.group(3))
                n = 1
                for x in dims:
                    n *= int(x, 0) if x and re.match(r"(0x)?[0-9a-fA-F]+$", x) else 64
                u = type_units(ty, recs)
                sz = type_size(ty, recs)
                if not u or not sz: continue
                out[m.group(2)] = {i * sz + o: s for i in range(n) for o, s in u}
    return out

def main():
    data_s, trace_path, defined_path, out_path = sys.argv[1:5]
    units_out = sys.argv[5] if len(sys.argv) > 5 else None
    trace = {}
    given = None                    # a units file (made here before): how each item is written
    if trace_path.endswith("units.txt"):
        given = {}
        for line in open(trace_path):
            a, k = line.split(); given[int(a, 16)] = k
        ctype, sext = {}, {}
    else:
        if os.path.exists(trace_path):
            for line in open(trace_path):
                a, m = line.split(); trace[int(a, 16) | 0x80000000] = int(m)
        ctype = c_types("src")
        recs = record_layouts("src")
        sext = struct_externs("src", recs)
    defined = set(open(defined_path).read().split())
    decided = []

    data = {}           # addr -> byte value
    ptrs = {}           # addr -> symbol expression (a 32-bit pointer)
    labels = {}         # addr -> [label]
    if data_s.endswith(".txt"):
        # the layout (labels and pointers, made from asm/data.s) and the bytes from the ROM:
        # data_s is the layout file; the ROM is next to it in argv as ROM=path
        rom = open(os.environ["CFSC_ROM"], "rb").read()
        for line in open(data_s):
            f = line.split()
            if f[0] == "R":
                lo0, hi0 = int(f[1], 16), int(f[2], 16)
                o = 0x1000 + lo0 - 0x80000400
                for k in range(hi0 - lo0): data[lo0 + k] = rom[o + k]
            elif f[0] == "L":
                labels.setdefault(int(f[1], 16), []).append(f[2])
            elif f[0] == "P":
                a = int(f[1], 16); ptrs[a] = f[2]
                for k in range(4): data[a + k] = 0
    else:
        cur_labels = []
        for line in open(data_s):
            m = re.match(r"^([A-Za-z_][\w]*):", line)
            if m:
                cur_labels.append(m.group(1)); continue
            m = re.match(r"/\* ([0-9A-F]{8}) \*/ \.(word|byte) (.*)", line)
            if not m: continue
            a = int(m.group(1), 16)
            if cur_labels:
                labels.setdefault(a, []).extend(cur_labels); cur_labels = []
            size = 4 if m.group(2) == "word" else 1
            for i, w in enumerate(x.strip() for x in m.group(3).split(",")):
                at = a + size * i
                if w.startswith("0x"):
                    v = int(w, 16)
                    for k in range(size): data[at + k] = (v >> (8 * (size - 1 - k))) & 0xFF
                else:
                    ptrs[at] = w
                    for k in range(4): data[at + k] = 0
    all_labels = {l for ls in labels.values() for l in ls}

    # text: a printable run ending in a 0 byte that starts at a label or that a pointer points to
    # is bytes (the C reads it a character at a time; without this it was written as words). Very
    # short runs that start like a float's high byte ('?', '@', '>', ...) are left alone.
    if given is not None:
        targets = {w.split("+")[0].strip() for w in ptrs.values()}
        starts = [a for a, ls in labels.items() if any(l in targets for l in ls) or True]
        known = c_types("src")         # (a label the C declares as numbers or a pointer is not text)
        for a0 in starts:
            if any(known.get(l, 1) != 1 for l in labels[a0]): continue
            e = a0
            while e in data and (32 <= data[e] < 127 or data[e] in (9, 10, 13)): e += 1   # (tabs and line ends too)
            n = e - a0
            if n == 0 or data.get(e, 1) != 0: continue
            txt = bytes(data[a0 + k] for k in range(n))
            letters = sum(chr(c).isalpha() or chr(c) in " .,!'-:%_" for c in txt)
            if n < 4 and not txt.isalpha(): continue
            if n < 8 and any(c < 32 for c in txt): continue    # (a float can look like 'C\nse')
            if txt[:1] in b"?@>=<;:9876543210" and n < 6: continue
            if letters * 2 < n: continue
            for w in range(a0 & ~3, e + 1, 4):
                if w not in given or given[w] in ("long", "short", "short1"): given[w] = "byte"

    # arrays the C declares as bytes or halves are written so (a table of colours written as
    # words came out with each colour back to front: the lens flares)
    if given is not None:
        changed = 0
        for name, (esize, total) in c_arrays("src").items():
            for a0, ls in labels.items():
                if name not in ls: continue
                want = "byte" if esize == 1 else "short"
                for w in range(a0, a0 + total, 4):
                    if w % 4 or w in ptrs or any(w + k in ptrs for k in range(4)): continue
                    if given.get(w, "long") == "long": given[w] = want; changed += 1
        print(f"port_data: {changed} words of byte/half arrays", file=sys.stderr)

    lo, hi = min(data), max(data) + 1

    # each data label's element size from the C (applies up to the next label)
    region_size = {}
    cur = None
    for a in range(lo, hi):
        if a in labels:
            cur = None
            for l in labels[a]:
                if l in ctype: cur = ctype[l]
        region_size[a] = cur

    unit = {}                           # addr -> basic item size starting there (from C structures)
    for a0, ls in labels.items():
        for l in ls:
            for off, sz in sext.get(l, {}).items():
                unit.setdefault(a0 + off, sz)
    stats = {"long": 0, "short": 0, "byte": 0, "ptr": 0, "nullptr": 0, "mixed": 0}
    missing = set()
    out = ["/* made by tools/port_data.py from asm/data.s - the game's data, little-endian */",
           "#ifdef __sh__", "#define SYM(x) _##x", "#else", "#define SYM(x) x", "#endif",
           ".section .data", ".balign 16"]
    a = lo
    while a < hi:
        for l in labels.get(a, ()):
            out.append(f".global SYM({l})\nSYM({l}):")
        if a in ptrs:
            w = ptrs[a]
            sym = w.split("+")[0].strip()
            if sym in defined or sym in all_labels:
                out.append(f"    .long SYM({w})"); stats["ptr"] += 1
            else:
                out.append(f"    .long 0 /* {w} */"); stats["nullptr"] += 1; missing.add(sym)
            a += 4; continue
        # how far the next label or pointer is (a unit must not run over one)
        def clear(n):
            return all(a + k in data and (k == 0 or (a + k not in labels and a + k not in ptrs)) for k in range(n))
        m = [trace.get(a + i, 0) for i in range(4)]
        # 16-bit reads win over 32-bit ones (structures of halves get copied as words),
        # 32-bit over 8-bit (byte-by-byte copies of word data)
        u4 = [unit.get(a + k) for k in range(4)]
        if a % 4 == 0 and clear(4) and any(u4):
            # the C declares what is here (a structure): that comes first (the trace can show
            # word reads where the compiler merged neighbouring byte or half reads)
            if u4[0] in (4, 8): kind = "long"
            elif u4[0] == 2 or u4[2] == 2: kind = "short"
            elif any(x == 1 for x in u4): kind = "byte"
            else: kind = "long"
        elif a % 4 == 0 and clear(4):
            if any(x & 2 for x in m): kind = "short"
            elif m[0] & 4: kind = "long"
            elif any(x & 1 for x in m): kind = "byte"
            elif unit.get(a) == 4 or unit.get(a) == 8: kind = "long"
            elif unit.get(a) == 2 or unit.get(a + 2) == 2: kind = "short"
            elif any(unit.get(a + k) == 1 for k in range(4)): kind = "byte"
            else: kind = {1: "byte", 2: "short"}.get(region_size.get(a), "long")
            if kind == "short" and any(x & 5 for x in m): stats["mixed"] += 1
        elif a % 2 == 0 and clear(2):
            if (m[0] | m[1]) & 2: kind = "short1"
            elif (m[0] | m[1]) & 1: kind = "byte1"
            elif unit.get(a) == 2: kind = "short1"
            elif unit.get(a) == 1 or unit.get(a + 1) == 1: kind = "byte1"
            else: kind = "short1" if region_size.get(a) == 2 else "byte1"
        else:
            kind = "byte0"
        if given is not None: kind = given.get(a, kind)
        if kind in ("long", "short", "byte") and given is not None and a in given:
            # a label inside a table (the asm names an element): it points into the table
            for k in range(1, 4):
                for l in labels.get(a + k, ()):
                    out.append(f".global SYM({l})\n.set SYM({l}), . + {k}")
        decided.append((a, kind))
        b = [data.get(a + k, 0) for k in range(4)]
        if kind == "long":
            out.append(f"    .long 0x{b[0]:02X}{b[1]:02X}{b[2]:02X}{b[3]:02X}"); n = 4
        elif kind == "short":
            out.append(f"    .short 0x{b[0]:02X}{b[1]:02X}, 0x{b[2]:02X}{b[3]:02X}"); n = 4
        elif kind == "byte":
            out.append("    .byte " + ", ".join(f"0x{x:02X}" for x in b)); n = 4
        elif kind == "short1":
            out.append(f"    .short 0x{b[0]:02X}{b[1]:02X}"); n = 2
        elif kind == "byte1":
            out.append(f"    .byte 0x{b[0]:02X}, 0x{b[1]:02X}"); n = 2
        else:
            out.append(f"    .byte 0x{b[0]:02X}"); n = 1
        stats[kind.rstrip("01")] += 1
        a += n
    open(out_path, "w").write("\n".join(out) + "\n")
    if units_out:
        with open(units_out, "w") as f:
            for a, k in decided:
                if k not in ("long", "byte0"): f.write(f"{a:08X} {k}\n")
    print(f"port_data: 0x{lo:08X}-0x{hi:08X}: {stats}", file=sys.stderr)
    if missing:
        print("  pointers to things the port doesn't have (now 0): " + " ".join(sorted(missing)[:12])
              + (" ..." if len(missing) > 12 else ""), file=sys.stderr)

if __name__ == "__main__":
    main()
