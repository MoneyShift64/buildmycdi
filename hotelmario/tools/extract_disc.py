#!/usr/bin/env python3
"""Extract every file from a Hotel Mario (CD-i) raw disc image.

Usage:  python3 extract_disc.py hm.bin out_dir

hm.bin is a raw 2352-byte-per-sector image (make one from the .chd with chd2bin).
CD-i discs use the Green Book file system (big-endian, 'CD-I ' disc label at sector 16).

* Ordinary files (programs, data modules, .map index files) are written as plain bytes.
* Real-time files (*.rtf) interleave video, audio and data sectors, and each sector's
  subheader says what it is, so they are written as raw 2352-byte sectors (.rtf.raw)
  for the later conversion tools.
"""
import os, struct, sys

SECTOR = 2352

def main(img_path, out_dir):
    f = open(img_path, 'rb')

    def raw(lba):
        f.seek(lba * SECTOR)
        return f.read(SECTOR)

    def read_file(lba, size):
        out = bytearray()
        for k in range((size + 2047) // 2048):
            out += raw(lba + k)[24:24 + 2048]
        return bytes(out[:size])

    label = raw(16)[24:24 + 2048]
    if label[1:6] != b'CD-I ':
        sys.exit('No CD-i disc label at sector 16 - is this a raw 2352-byte image?')
    print('Disc:', label[40:72].decode('latin1').strip())

    pt_size = struct.unpack('>I', label[136:140])[0]
    pt_lba = struct.unpack('>I', label[148:152])[0]
    pt = read_file(pt_lba, pt_size)
    dirs, i = [], 0
    while i < len(pt):
        nl = pt[i]
        addr = struct.unpack('>I', pt[i + 2:i + 6])[0]
        parent = struct.unpack('>H', pt[i + 6:i + 8])[0]
        dirs.append((pt[i + 8:i + 8 + nl].decode('latin1'), addr, parent))
        i += 8 + nl + (nl & 1)

    def dir_path(k):
        name, _, parent = dirs[k]
        return '' if k == 0 else os.path.join(dir_path(parent - 1), name)

    os.makedirs(out_dir, exist_ok=True)
    listing = []
    for k, (_, dlba, _) in enumerate(dirs):
        rel = dir_path(k)
        first = raw(dlba)[24:24 + 2048]
        dsize = struct.unpack('>I', first[14:18])[0]      # '.' entry gives the directory size
        d = read_file(dlba, dsize)
        j = 0
        while j < len(d):
            if d[j] == 0:                                   # records never cross a sector
                j = (j // 2048 + 1) * 2048
                continue
            rl = d[j]
            lba = struct.unpack('>I', d[j + 6:j + 10])[0]
            size = struct.unpack('>I', d[j + 14:j + 18])[0]
            nl = d[j + 32]
            name = d[j + 33:j + 33 + nl].decode('latin1')
            j += rl
            if name in ('\x00', '\x01'):
                continue
            os.makedirs(os.path.join(out_dir, rel), exist_ok=True)
            path = os.path.join(out_dir, rel, name)
            if name.endswith('.rtf'):
                with open(path + '.raw', 'wb') as o:
                    for s in range(size // 2048):
                        o.write(raw(lba + s))
            else:
                open(path, 'wb').write(read_file(lba, size))
            listing.append('%-24s lba %7d  size %10d' % (os.path.join(rel, name), lba, size))
    open(os.path.join(out_dir, 'file_list.txt'), 'w').write('\n'.join(listing) + '\n')
    print('Extracted %d files to %s' % (len(listing), out_dir))

if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
