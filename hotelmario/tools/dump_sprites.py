#!/usr/bin/env python3
"""Dump every sprite in Hotel Mario (CD-i) to PNG.

Hotel Mario's sprites are "compiled": each frame is a 68000 routine that writes its
pixels straight into a 384-byte-wide screen. This script finds those routines in
mario_set_sub.o (Mario) and in every stage module (Ln_sNN_sub.o / _subT.o, which hold
the enemies and objects), runs each one in a 68000 emulator (sprite_runner) and saves
what it draws, coloured with the stage's sprite palette.

usage: dump_sprites.py <extracted_dir> <out_dir> [--runner path/to/sprite_runner]

Output:
  out_dir/mario/<variant>/frame_XX.png   variants: mario, mario_fire, luigi, luigi_fire
  out_dir/objects/<name>/<name>_XX.png   one folder per object (goomba, koopa, ...)
  ... and a contact_sheet.png (3x size) in every folder.
"""
import glob, hashlib, os, re, struct, subprocess, sys, tempfile
from PIL import Image, ImageDraw

SPR_SIG = b'\x2f\x09\x22\x40'          # move.l a1,-(sp) ; movea.l d0,a1
PLANEB_COLORS = 0x1018ec               # in work/hm_image.bin (int2 R,G,B x 10 x 4 sets)
SHROOM_COLORS = 0x1019dc               # int2 R,G,B x 5 x 12 sets
FIRE_COLORS = 0x101b44                 # int2 R,G,B x 5 x 10 sets x 2 players

def rtf_sectors(path):
    r = open(path, 'rb').read()
    for k in range(len(r) // 2352):
        yield r[k * 2352:(k + 1) * 2352]

def data_payloads(path):
    """Concatenated payload of the data sectors (where the code modules live)."""
    out = bytearray()
    for s in rtf_sectors(path):
        sm = s[18]
        if sm & 0x08 and not sm & 0x20:
            out += s[24:24 + 2048]
    return bytes(out)

def modules_in(blob):
    for m in re.finditer(b'\x4a\xfc\x00\x01', blob):
        o = m.start()
        size, = struct.unpack('>I', blob[o + 4:o + 8])
        noff, = struct.unpack('>I', blob[o + 12:o + 16])
        if 0 < noff < size < 0x100000:
            yield blob[o + noff:blob.find(b'\0', o + noff)].decode('latin1'), blob[o:o + size]

def entries(mod):
    exe, = struct.unpack('>I', mod[0x30:0x34])
    out, o = [], exe
    while o + 8 <= len(mod):
        no, co = struct.unpack('>II', mod[o:o + 8])
        if no == 0 or no >= len(mod) or co >= len(mod):
            break
        out.append((co, mod[no:mod.index(b'\0', no)].decode('latin1')))
        o += 8
    return sorted(out)

def stage_palettes(av_path):
    """4th colour table of each stage group = palette of the sprite plane."""
    pals, group = [], []
    for s in rtf_sectors(av_path):
        sm = s[18]
        if sm & 0x08 and not sm & 0x20:
            group.append(s[24:24 + 384])
        if sm & 0x04:                   # the sound clip ends each stage's group
            if len(group) >= 4:
                pals.append(group[3])
            elif group:
                pals.append(group[-1])
            group = []
    return pals

def palette(raw, player_set=None, img=None):
    pal = [tuple(raw[3 * i:3 * i + 3]) for i in range(128)]
    if player_set is not None:
        base = PLANEB_COLORS + player_set * 0x3c
        for i in range(10):
            r, g, b = struct.unpack('>HHH', img[base + 6 * i:base + 6 * i + 6])
            pal[0x0b + i] = (r & 255, g & 255, b & 255)
    return pal

def run_frames(runner, mod_bytes, offsets, tmp):
    mp = os.path.join(tmp, 'mod.o')
    open(mp, 'wb').write(mod_bytes)
    for f in glob.glob(os.path.join(tmp, 'spr_*.bin')):
        os.remove(f)
    frames = {}
    for i in range(0, len(offsets), 200):
        subprocess.run([runner, mp, 'draw'] + ['0x%x' % o for o in offsets[i:i + 200]],
                       cwd=tmp, capture_output=True)
    for o in offsets:
        p = os.path.join(tmp, 'spr_%06x.bin' % o)
        if os.path.exists(p):
            raw = open(p, 'rb').read()
            w, h, ox, oy = struct.unpack('<hhhh', raw[:8])
            frames[o] = (w, h, ox, oy, struct.unpack('<%dH' % (w * h), raw[8:8 + 2 * w * h]))
    return frames

def to_png(fr, pal):
    w, h, ox, oy, px = fr
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    im.putdata([(pal[v & 0x7f] + (255,)) if v != 0xffff else (0, 0, 0, 0) for v in px])
    return im

def contact_sheet(ims, path, cols=10, scale=3):
    if not ims:
        return
    cw = max(i.size[0] for _, i in ims) + 6
    ch = max(i.size[1] for _, i in ims) + 6
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new('RGBA', (min(cols, len(ims)) * cw, rows * (ch + 10)), (64, 96, 128, 255))
    d = ImageDraw.Draw(sheet)
    for k, (label, im) in enumerate(ims):
        x, y = (k % cols) * cw, (k // cols) * (ch + 10)
        sheet.alpha_composite(im, (x + 3, y + 3 + ch - 6 - im.size[1]))
        d.text((x + 2, y + ch - 1), label, fill=(255, 255, 255, 255))
    sheet = sheet.resize((sheet.size[0] * scale, sheet.size[1] * scale), Image.NEAREST)
    sheet.save(path)

def main():
    args = sys.argv[1:]
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sprite_runner')
    if '--runner' in args:
        k = args.index('--runner'); runner = args[k + 1]; del args[k:k + 2]
    src, out = args
    here = os.path.dirname(os.path.abspath(__file__))
    img = open(os.path.join(here, '..', 'work', 'hm_image.bin'), 'rb').read()
    tmp = tempfile.mkdtemp()

    # palettes per hotel/stage
    pals = {}
    for av in sorted(glob.glob(os.path.join(src, 'L*_av.rtf.raw'))):
        hotel = int(os.path.basename(av)[1])
        pals[hotel] = stage_palettes(av)
    default_pal = pals[1][0]

    # ---- Mario / Luigi -------------------------------------------------------
    intro = data_payloads(os.path.join(src, 'intro.rtf.raw'))
    mario = dict(modules_in(intro))['mario_set_sub.o']
    offs = sorted(m.start() for m in re.finditer(SPR_SIG, mario))
    frames = run_frames(runner, mario, offs, tmp)
    frames = {o: f for o, f in frames.items()
              if not (len(set(v for v in f[4] if v != 0xffff)) == 1 and f[0] * f[1] > 150)}
    for pset, name in enumerate(('mario', 'mario_fire', 'luigi', 'luigi_fire')):
        d = os.path.join(out, 'mario', name)
        os.makedirs(d, exist_ok=True)
        pal = palette(default_pal, pset, img)
        ims = []
        for k, o in enumerate(sorted(frames)):
            im = to_png(frames[o], pal)
            im.save(os.path.join(d, 'frame_%02d.png' % k))
            ims.append(('%02d' % k, im))
        contact_sheet(ims, os.path.join(d, 'contact_sheet.png'))
    print('mario: %d frames x 4 colour sets' % len(frames))

    # ---- stage objects -------------------------------------------------------
    seen, objects = set(), {}
    for dat in sorted(glob.glob(os.path.join(src, 'L*_dat.rtf.raw'))):
        hotel = int(os.path.basename(dat)[1])
        for mname, mod in modules_in(data_payloads(dat)):
            m = re.match(r'L(\d)_s(\d\d)_sub', mname)
            stage = int(m.group(2)) - 1 if m else 0
            plist = pals.get(hotel) or [default_pal]
            pal = palette(plist[min(stage, len(plist) - 1)], 0, img)
            ents = entries(mod)
            offs = sorted(x.start() for x in re.finditer(SPR_SIG, mod))
            frames = run_frames(runner, mod, offs, tmp)
            for o, fr in sorted(frames.items()):
                used = set(v for v in fr[4] if v != 0xffff)
                if len(used) == 1 and fr[0] * fr[1] > 150:
                    continue                    # single-colour silhouette = erase mask, not a sprite
                owner = 'unknown'
                for eo, en in ents:
                    if eo <= o:
                        owner = en
                h = hashlib.md5(repr(fr[:2] + fr[4:]).encode()).hexdigest()
                if (owner, h) in seen:
                    continue
                seen.add((owner, h))
                objects.setdefault(owner, []).append((to_png(fr, pal), mname))
    total = 0
    for name, lst in sorted(objects.items()):
        d = os.path.join(out, 'objects', re.sub(r'[^A-Za-z0-9_]', '_', name))
        os.makedirs(d, exist_ok=True)
        ims = []
        for k, (im, mname) in enumerate(lst):
            im.save(os.path.join(d, '%s_%03d.png' % (name, k)))
            ims.append(('%03d' % k, im))
        contact_sheet(ims, os.path.join(d, 'contact_sheet.png'))
        total += len(lst)
        print('%-12s %4d frames' % (name, len(lst)))
    print('objects: %d kinds, %d unique frames' % (len(objects), total))

if __name__ == '__main__':
    main()
