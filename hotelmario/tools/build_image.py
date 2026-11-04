#!/usr/bin/env python3
"""Build the memory image + symbol tables used for decompiling Hotel Mario.

Usage:  python3 build_image.py extracted_dir out_dir

Reads (from the disc):
  cdi_hotel       OS-9 program module (68000 code)
  cdi_hotel.stb   Microware debugger symbol table that shipped on the disc
  cdi_hotel_data  OS-9 data module holding the program's initialised data + fixups

Writes:
  hm_image.bin    code at 0x000000, the program's data area (as at run time) at 0x100000
  syms.txt        "value flags name" for every symbol (code = offset into module,
                  data = offset from register A6, which OS-9 C points 0x8000 past the
                  start of the data area)
  symbols.csv     the same, with absolute addresses in hm_image.bin
  thunks.json     _jmptbl entries (JMP instructions in the data area) -> target function
"""
import json, os, struct, sys

DBASE = 0x100000          # where the data area goes in hm_image.bin
A6 = DBASE + 0x8000       # OS-9 C: A6 = data area + 0x8000

def module_header(m, name):
    if m[:2] != b'\x4a\xfc':
        sys.exit(name + ' is not an OS-9 module')
    size, = struct.unpack('>I', m[4:8])
    return size

def main(src, out):
    os.makedirs(out, exist_ok=True)
    code = open(os.path.join(src, 'cdi_hotel'), 'rb').read()
    module_header(code, 'cdi_hotel')
    data_size, = struct.unpack('>I', code[0x38:0x3c])       # M$Mem: size of data area

    # --- symbol table -------------------------------------------------------
    stb = open(os.path.join(src, 'cdi_hotel.stb'), 'rb').read()
    off, cnt = struct.unpack('>II', stb[0x44:0x4c])
    syms = []
    for k in range(cnt):
        v, fl, no = struct.unpack('>iHI', stb[off + 10 * k:off + 10 * k + 10])
        syms.append((v, fl, stb[no:stb.index(b'\0', no)].decode()))
    with open(os.path.join(out, 'syms.txt'), 'w') as o:
        o.write(''.join('%d %04x %s\n' % s for s in syms))
    with open(os.path.join(out, 'symbols.csv'), 'w') as o:
        o.write('name,kind,address,raw_value\n')
        for v, fl, n in syms:
            kind = 'code' if fl & 4 else 'data'
            o.write('%s,%s,0x%06x,%d\n' % (n, kind, v if fl & 4 else A6 + v, v))

    # --- initialised data + relocations from the data module -----------------
    d = open(os.path.join(src, 'cdi_hotel_data'), 'rb').read()
    module_header(d, 'cdi_hotel_data')
    dest, size = struct.unpack('>II', d[0x44:0x4c])
    area = bytearray(data_size)
    area[dest:dest + size] = d[0x4c:0x4c + size]
    p = 0x4c + size

    def read_list(p):
        refs = []
        while True:
            msw, n = struct.unpack('>HH', d[p:p + 4]); p += 4
            if n == 0:
                return refs, p
            refs += [(msw << 16) | struct.unpack('>H', d[p + 2 * k:p + 2 * k + 2])[0] for k in range(n)]
            p += 2 * n

    code_refs, p = read_list(p)     # pointers into the code (code base is 0 here: nothing to add)
    data_refs, p = read_list(p)     # pointers into the data area: add its base
    for r in data_refs:
        v, = struct.unpack('>I', area[r:r + 4])
        area[r:r + 4] = struct.pack('>I', v + DBASE)

    img = bytearray(DBASE + data_size)
    img[:len(code)] = code
    img[DBASE:] = area
    open(os.path.join(out, 'hm_image.bin'), 'wb').write(img)

    # --- jump table thunks --------------------------------------------------
    names = {v: n for v, fl, n in syms if fl & 4}
    thunks = []
    for r in code_refs:
        if area[r - 2:r] == b'\x4e\xf9':                    # JMP abs.l
            t, = struct.unpack('>I', area[r:r + 4])
            if t:
                thunks.append((DBASE + r - 2, t, names.get(t, 'FUN_%06x' % t)))
    json.dump(thunks, open(os.path.join(out, 'thunks.json'), 'w'), indent=0)

    print('symbols: %d   initialised data: %d bytes   fixups: %d code / %d data   jump table: %d'
          % (len(syms), size, len(code_refs), len(data_refs), len(thunks)))

if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
