#!/usr/bin/env python3
"""Convert Hotel Mario disc data into ready-to-use Dreamcast files (build time).

usage: make_dc_assets.py <extracted_dir> <out_dir> [--runner tools/sprite_runner]

Everything is converted here so the Dreamcast only copies bytes:
  * pictures -> 8-bit paletted PowerVR textures, already twiddled, power-of-two size
  * colour tables -> 128 RGB entries
  * Mario's compiled sprites -> one paletted atlas + frame table + slot table

Files (all little-endian):
  *.tex   'HMT2' u16 n_parts, u16 used_w, then per part: u16 w, h, y0, rows + w*h bytes of
          twiddled PAL8 (pictures over 256 lines are split so no memory goes on padding)
  *.pal   'HMPL' u16 count, u16 0, then count*3 bytes RGB
  title_H_SS_m / title_H_SS_l / stage_H_SS (.tex + .pal) for hotel H, stage SS
  doors_H_SS.tex  the stage's door/lift tile sheet (sprite_gfx, 384 x 169, stage palette)
  stage_H_SS.inf  'HMSI' 35 x u16 door grid (stage_info+0x12, floor-major), u32 time limit,
                  5 x 6 s16 colour cycling (first, last, speed, count, pos, active)
                  then u32 random seed (stage_info+4, copied to gds unk_826/unk_82a)
  room_H_SS.room  'HMRM' u16 n_objects, n_objects x 16-byte object names (module entries),
                  u16 n_actors, per actor: u16 object index (0xffff = none), 38 x s16 =
                  actor_t offsets 0x04..0x4f as stored on the disc
  obj_NAME.tex/.frm  atlas of an object's frames, in the order of its frames[] table
  stages.txt      one 'H_SS' line per stage
  mario.frm  'HMFR' u16 n_frames, u16 n_slots, n_frames x (u16 x,y,w,h, s16 ox,oy),
             n_slots x u16 frame index
  sprites.pal  plane A colours in play (loading_clut = intro record 5) for every object
  power.pal    power-up colour cycling (60 mushroom, 100 fire flower entries)
  hud.tex/.frm the score bar pictures (digits, MARIO, LUIGI, x, TIME, coin, clock, STAGE)
  fx.tex/.frm  fireball (slots 1-6) and kill star (8)
  music_H_R.xa CD-i ADPCM music, one channel ('HMXA' u32 groups, 60-byte groups)
  sfx_H_SS.xa  the stage's 16 sound effects ('HMSF' 17 x u32 group offsets, groups)
  players.pal  4 sets x 10 RGB (Mario, Mario fire, Luigi, Luigi fire) for colours 0x0b-0x14
"""
import glob, os, re, struct, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dump_sprites as D

TRANSPARENT = 0xff          # palette index used for "no pixel" in sprite textures

def spread(v):
    s = 0
    for b in range(10):
        s |= ((v >> b) & 1) << (2 * b)
    return s

def twiddle_pal8(pixels, w, h):
    """Power-of-two PAL8 texture in PowerVR twiddled order (y in the low bit). A non-square
    texture is a row (or column) of square twiddled blocks."""
    assert w & (w - 1) == 0 and h & (h - 1) == 0
    m = min(w, h)
    lm = m.bit_length() - 1
    out = bytearray(w * h)
    sp = [spread(i) for i in range(m)]
    for y in range(h):
        row = y * w
        for x in range(w):
            i = sp[y & (m - 1)] | (sp[x & (m - 1)] << 1)
            i += ((x >> lm) if w > h else (y >> lm)) << (2 * lm)
            out[i] = pixels[row + x]
    return bytes(out)

def pow2(n):
    p = 8
    while p < n:
        p *= 2
    return p

def write_tex(path, pixels, used_w, used_h, size=None, fill=0):
    """'HMT2' u16 n_parts, u16 used_w; per part u16 w, h, y0, rows + twiddled PAL8 data.
    Pictures taller than 256 lines are split (512x256 + 512x32 for 384x280) so no
    texture memory is wasted on padding."""
    tw = pow2(used_w)
    parts, y0 = [], 0
    while y0 < used_h:
        rows = min(256 if tw >= 256 else tw, used_h - y0)
        th = pow2(rows)
        canvas = bytearray([fill]) * (tw * th)
        for y in range(rows):
            canvas[y * tw:y * tw + used_w] = pixels[(y0 + y) * used_w:(y0 + y + 1) * used_w]
        parts.append((tw, th, y0, rows, twiddle_pal8(canvas, tw, th)))
        y0 += rows
    with open(path, 'wb') as f:
        f.write(b'HMT2' + struct.pack('<2H', len(parts), used_w))
        for tw_, th_, y0_, rows_, data in parts:
            f.write(struct.pack('<4H', tw_, th_, y0_, rows_) + data)

def write_pal(path, rgb_list):
    with open(path, 'wb') as f:
        f.write(b'HMPL' + struct.pack('<2H', len(rgb_list), 0))
        for r, g, b in rgb_list:
            f.write(bytes((r, g, b)))

def av_record(av_path, sector_index):
    """(palette bytes, pixel bytes) of the picture record starting at sector_index."""
    r = open(av_path, 'rb').read()
    pal, px, k = None, bytearray(), sector_index
    while True:
        s = r[k * 2352:(k + 1) * 2352]
        sm = s[18]
        if sm & 0x08:
            pal = s[24:24 + 384]
        elif sm & 0x02:
            px += s[24:24 + 2324]
        k += 1
        if sm & 0x01:
            return pal, bytes(px)

def map_records(map_path):
    """Start sectors of every record listed in a cdi_*.map module."""
    m = open(map_path, 'rb').read()
    n, = struct.unpack('>H', m[0x44:0x46])
    out, o = [], 0x4c
    for _ in range(n):
        if o + 12 > len(m):
            break
        out.append(struct.unpack('>I', m[o:o + 4])[0])
        o += 12
    return out

def stage_header(dat_path, stage, upper=False):
    """stage_info (0x94 bytes): dat record stage*4 holds the stage module, then
    (from the next 2048-byte boundary) 0x40 bytes and stage_info - as cd_dat() loads it."""
    hotel = int(os.path.basename(dat_path)[1])
    recs = map_records(os.path.join(os.path.dirname(dat_path), 'cdi_L%d_dat.map' % hotel))
    r = open(dat_path, 'rb').read()
    data, k = bytearray(), recs[stage * 4 + (2 if upper else 0)]
    while k * 2352 < len(r):
        s = r[k * 2352:(k + 1) * 2352]
        sm = s[18]
        if sm & 0x08:
            data += s[24:24 + (2324 if sm & 0x20 else 2048)]
        k += 1
        if sm & 0x01:
            break
    msize, = struct.unpack('>I', data[4:8])
    rest = data[((msize + 2047) // 2048) * 2048:]
    return rest[0x40:0x40 + 0x94]

def stage_record(dat_path, stage, upper=False):
    hotel = int(os.path.basename(dat_path)[1])
    recs = map_records(os.path.join(os.path.dirname(dat_path), 'cdi_L%d_dat.map' % hotel))
    r = open(dat_path, 'rb').read()
    data, k = bytearray(), recs[stage * 4 + (2 if upper else 0)]
    while k * 2352 < len(r):
        s = r[k * 2352:(k + 1) * 2352]
        sm = s[18]
        if sm & 0x08:
            data += s[24:24 + (2324 if sm & 0x20 else 2048)]
        k += 1
        if sm & 0x01:
            break
    msize, = struct.unpack('>I', data[4:8])
    return bytes(data[:msize]), bytes(data[((msize + 2047) // 2048) * 2048:])

# ---------------------------------------------------------------- sound
# CD-i ADPCM (level B, stereo, 4 bit, 37.8 kHz): a 2304-byte sector holds 18 sound
# groups of 128 bytes: 16 bytes of unit parameters (unit u at byte 4 + u), then 28
# words of 4 bytes; byte b of word t holds sample t of units 2b (low nibble) and
# 2b+1 (high nibble). In stereo the even units are the left channel, the odd ones the
# right. Hotel Mario keeps its music in the left channel and mixes its sound effects
# into the right one (cd_mix ORs them into the music sectors as they stream in).
# The port's .xa files keep one channel: per group 4 parameter bytes then 28 x 16-bit
# words (sample t of the 4 units in nibbles 0..3), 60 bytes.

def xa_left(group):
    params = bytes(group[4 + 2 * k] for k in range(4))
    out = bytearray(params)
    for t in range(28):
        w = group[16 + 4 * t:20 + 4 * t]
        v = 0
        for k in range(4):
            v |= (w[k] & 15) << (4 * k)
        out += struct.pack('<H', v)
    return bytes(out)

def sfx_group(chunk):
    """One 64-byte sound effect chunk (cd_mix): 8 parameter bytes (units 1,3,5,7 =
    bytes 1,3,5,7) and 14 words; word j gives sample 2j (high nibbles) and 2j+1 (low
    nibbles) of units 1,3,5,7 (byte b -> unit 2b+1)."""
    out = bytearray(chunk[1 + 2 * k] for k in range(4))
    for j in range(14):
        w = chunk[8 + 4 * j:12 + 4 * j]
        hi = lo = 0
        for k in range(4):
            hi |= (w[k] >> 4) << (4 * k)
            lo |= (w[k] & 15) << (4 * k)
        out += struct.pack('<2H', hi, lo)
    return bytes(out)

def audio_record(path, start):
    """The audio sectors of one record (it is one channel of the interleaved file)."""
    r = open(path, 'rb').read()
    k, groups = start, bytearray()
    chan = None
    while k * 2352 < len(r):
        s = r[k * 2352:(k + 1) * 2352]
        if s[18] & 0x04:
            if chan is None:
                chan = s[17]
            if s[17] == chan:
                for gi in range(18):
                    groups += xa_left(s[24 + 128 * gi:24 + 128 * (gi + 1)])
                if s[18] & 0x01:
                    break
        k += 1
    return bytes(groups)

def write_room(path, dat_path, stage, upper=False):
    module, rest = stage_record(dat_path, stage, upper)
    room = rest[0x40 + 0x94:]
    names = [n for _, n in D.entries(module)]
    n, = struct.unpack('>h', room[0:2])
    ids = struct.unpack('>25h', room[2:52])
    type_to_obj = {t: i for i, t in enumerate(ids[:len(names)]) if t}
    with open(path, 'wb') as f:
        f.write(b'HMRM' + struct.pack('<H', len(names)))
        for nm in names:
            f.write(nm.encode('latin1')[:15].ljust(16, b'\0'))
        f.write(struct.pack('<H', n))
        for i in range(n):
            a = room[0x34 + i * 0x274:0x34 + i * 0x274 + 0x50]
            words = struct.unpack('>38h', a[4:0x50])
            f.write(struct.pack('<H', type_to_obj.get(words[0], 0xffff)))
            f.write(struct.pack('<38h', *words))

def object_frames(module):
    """{object name: [code offsets of frames[0], frames[1], ...]} read from the init code:
    movea.l #off,A0 ; lea (PC,A0.l),A0 ; move.l A0,(0x50+4N,A2)"""
    ents = D.entries(module)
    out = {}
    for i in range(0x30, len(module) - 14, 2):
        if module[i:i + 2] == b'\x20\x7c' and module[i + 6:i + 8] == b'\x41\xfb' and module[i + 10:i + 12] == b'\x25\x48':
            d16, = struct.unpack('>h', module[i + 12:i + 14])
            if not (0x50 <= d16 < 0x150 and d16 % 4 == 0):
                continue
            off, = struct.unpack('>i', module[i + 2:i + 6])
            t = (i + 8) + off + struct.unpack('b', module[i + 9:i + 10])[0]
            if module[t:t + 4] != D.SPR_SIG:
                continue
            owner = None
            for eo, en in ents:
                if eo <= i:
                    owner = en
            lst = out.setdefault(owner, {})
            lst.setdefault((d16 - 0x50) // 4, t)
    return {k: [v[j] for j in range(max(v) + 1)] if all(j in v for j in range(max(v) + 1)) else None
            for k, v in out.items()}

def write_atlas(path_tex, path_frm, frames, offs, size=256):
    order = sorted(set(offs), key=lambda o: -frames[o][1])
    place, x, y, shelf = {}, 0, 0, 0
    for o in order:
        w, h = frames[o][0], frames[o][1]
        if x + w > size:
            x, y, shelf = 0, y + shelf + 1, 0
        place[o] = (x, y)
        x += w + 1
        shelf = max(shelf, h)
    if y + shelf > size:
        return write_atlas(path_tex, path_frm, frames, offs, size * 2)
    atlas = bytearray([TRANSPARENT]) * (size * size)
    for o in set(offs):
        w, h, ox, oy, px = frames[o]
        ax, ay = place[o]
        for j in range(h):
            for i in range(w):
                v = px[j * w + i]
                if v != 0xffff and v & 0x7f:    # plane A: colour 0 is see-through (scoring's
                    atlas[(ay + j) * size + ax + i] = v & 0x7f   # blank digit writes 0s)
    write_tex(path_tex, bytes(atlas), size, size, size, TRANSPARENT)
    uniq = sorted(set(offs))
    with open(path_frm, 'wb') as f:
        f.write(b'HMFR' + struct.pack('<2H', len(uniq), len(offs)))
        for o in uniq:
            w, h, ox, oy, _ = frames[o]
            f.write(struct.pack('<4H2h', place[o][0], place[o][1], w, h, ox, oy))
        for o in offs:
            f.write(struct.pack('<H', uniq.index(o)))

PORTED_OBJECTS = tuple(re.findall(r'^X\((\w+)\)', open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dc', 'obj_list.h')).read(), re.M))     # objects the Dreamcast port has so far

def main():
    args = sys.argv[1:]
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sprite_runner')
    if '--runner' in args:
        k = args.index('--runner'); runner = args[k + 1]; del args[k:k + 2]
    src, out = args
    os.makedirs(out, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    img = open(os.path.join(here, '..', 'work', 'hm_image.bin'), 'rb').read()

    # every hotel and stage: per stage the av stream has 8 records; record 1 is the
    # "MARIO STAGE n" card, 2 the "LUIGI STAGE n" card, 3 the stage picture (plane B)
    index = []
    for av in sorted(glob.glob(os.path.join(src, 'L*_av.rtf.raw'))):
        hotel = int(os.path.basename(av)[1])
        recs = map_records(os.path.join(src, 'cdi_L%d_av.map' % hotel))
        dat = os.path.join(src, 'L%d_dat.rtf.raw' % hotel)
        for stage in range(len(recs) // 8):
            tag = '%d_%02d' % (hotel, stage + 1)
            for name, rec in (('title_%s_m' % tag, 1), ('title_%s_l' % tag, 2), ('stage_%s' % tag, 3)):
                pal, px = av_record(av, recs[stage * 8 + rec])    # map sectors count from the file start
                write_tex(os.path.join(out, name + '.tex'), px[:384 * 280], 384, 280, 512, 0)
                write_pal(os.path.join(out, name + '.pal'), [tuple(pal[3 * i:3 * i + 3]) for i in range(128)])
            # door sheet (sprite_gfx): record 4, a 384-wide sheet of 32x31 tiles, stage palette
            _, sheet = av_record(av, recs[stage * 8 + 4])
            rows = len(sheet) // 384
            write_tex(os.path.join(out, 'doors_%s.tex' % tag), sheet[:384 * rows], 384, rows, 512, 0)
            # stage info: door grid (5 floors x 7 columns), time limit, colour cycling, seed, start floor/column
            si = stage_header(dat, stage)
            with open(os.path.join(out, 'stage_%s.inf' % tag), 'wb') as f:
                f.write(b'HMSI')
                f.write(struct.pack('<35H', *struct.unpack('>35H', si[0x12:0x12 + 70])))
                f.write(struct.pack('<I', struct.unpack('>I', si[8:12])[0]))
                for i in range(5):
                    f.write(struct.pack('<6h', *struct.unpack('>6h', si[0x58 + 12 * i:0x58 + 12 * i + 12])))
                f.write(struct.pack('<I', struct.unpack('>I', si[4:8])[0]))   # random seed
                f.write(struct.pack('<2h', *struct.unpack('>2h', si[0xe:0x12])))  # Mario's start floor, column (dead_init)
            # room table: the stage's actors and which module object each one is
            write_room(os.path.join(out, 'room_%s.room' % tag), dat, stage)
            index.append(tag)
    with open(os.path.join(out, 'stages.txt'), 'w') as f:
        f.write('\n'.join(index) + '\n')

    # sound: music records 4-7 (the stage tunes; 4 is the boss stage's), 8 (lost),
    # 9 / 10 (stage cleared / hotel cleared) of each hotel's Ln_am.rtf, and each stage's
    # 16 sound effects (av record stage*8+6, offsets in the dat record's first 0x40 bytes)
    for am in sorted(glob.glob(os.path.join(src, 'L*_am.rtf.raw'))):
        hotel = int(os.path.basename(am)[1])
        arecs = map_records(os.path.join(src, 'cdi_L%d_am.map' % hotel))
        for rec in range(4, 11):
            if rec >= len(arecs):
                continue
            data = audio_record(am, arecs[rec])
            with open(os.path.join(out, 'music_%d_%d.xa' % (hotel, rec)), 'wb') as f:
                f.write(b'HMXA' + struct.pack('<I', len(data) // 60) + data)
    for av in sorted(glob.glob(os.path.join(src, 'L*_av.rtf.raw'))):
        hotel = int(os.path.basename(av)[1])
        recs = map_records(os.path.join(src, 'cdi_L%d_av.map' % hotel))
        dat = os.path.join(src, 'L%d_dat.rtf.raw' % hotel)
        r = open(av, 'rb').read()
        for stage in range(len(recs) // 8):
            k, bg = recs[stage * 8 + 6], bytearray()
            while k * 2352 < len(r):
                sct = r[k * 2352:(k + 1) * 2352]
                bg += sct[24:24 + 2324]          # (form 2: the effects run on across the whole 2324 bytes)
                k += 1
                if sct[18] & 0x01:
                    break
            _, rest = stage_record(dat, stage)
            ends = struct.unpack('>16I', rest[:0x40])
            starts = (0,) + ends[:15]
            groups, offs = bytearray(), []
            for a, b in zip(starts, ends):
                offs.append(len(groups) // 60)
                for c in range(a, min(b, len(bg)) - 63, 64):
                    groups += sfx_group(bg[c:c + 64])
            offs.append(len(groups) // 60)
            with open(os.path.join(out, 'sfx_%d_%02d.xa' % (hotel, stage + 1)), 'wb') as f:
                f.write(b'HMSF' + struct.pack('<17I', *offs) + groups)

    # player colours (planeB_Colors: 4 sets x 10 x int2 RGB)
    sets = []
    for s in range(4):
        for i in range(10):
            r, g, b = struct.unpack('>HHH', img[D.PLANEB_COLORS + s * 0x3c + 6 * i:D.PLANEB_COLORS + s * 0x3c + 6 * i + 6])
            sets.append((r & 255, g & 255, b & 255))
    write_pal(os.path.join(out, 'players.pal'), sets)

    # power-up colour cycling for plane B colours 0x10-0x14 (power_cycle): 12 sets of 5 for
    # the mushroom, then 10 sets of 5 for the fire flower per player (Mario, Luigi)
    pw = []
    for base, n in ((D.SHROOM_COLORS, 12 * 5), (D.FIRE_COLORS, 2 * 10 * 5)):
        for i in range(n):
            r, g, b = struct.unpack('>HHH', img[base + 6 * i:base + 6 * i + 6])
            pw.append((r & 255, g & 255, b & 255))
    write_pal(os.path.join(out, 'power.pal'), pw)

    # the objects' palette: the intro module copies intro record 5's colour table into
    # gds->loading_clut, and the_game sets plane A to it for play (set_up_clut fct_a)
    irecs = map_records(os.path.join(src, 'cdi_intro.map'))
    spal, _ = av_record(os.path.join(src, 'intro.rtf.raw'), irecs[5])
    write_pal(os.path.join(out, 'sprites.pal'), [tuple(spal[3 * i:3 * i + 3]) for i in range(128)])

    # Mario: run ms_init for the slot table, draw every frame, pack an atlas
    intro = D.data_payloads(os.path.join(src, 'intro.rtf.raw'))
    mario = dict(D.modules_in(intro))['mario_set_sub.o']
    tmp = '/tmp/hm_assets'
    os.makedirs(tmp, exist_ok=True)
    open(os.path.join(tmp, 'mario.o'), 'wb').write(mario)
    res = subprocess.run([runner, os.path.join(tmp, 'mario.o'), 'init', '0x100'],
                         capture_output=True, text=True).stdout.split('\n')[1:]
    slot_addrs = [int(l.split()[1], 16) - 0x100000 for l in res if l.strip()][1:]
    slot_addrs = [a for a in slot_addrs if a > 0]
    uniq = sorted(set(slot_addrs))
    frames = D.run_frames(runner, mario, uniq, tmp)
    # shelf-pack into 256x256
    order = sorted(uniq, key=lambda o: -frames[o][1])
    place, x, y, shelf = {}, 0, 0, 0
    for o in order:
        w, h = frames[o][0], frames[o][1]
        if x + w > 256:
            x, y, shelf = 0, y + shelf + 1, 0
        place[o] = (x, y)
        x += w + 1
        shelf = max(shelf, h)
    assert y + shelf <= 256, 'atlas overflow'
    atlas = bytearray([TRANSPARENT]) * (256 * 256)
    for o in uniq:
        w, h, ox, oy, px = frames[o]
        ax, ay = place[o]
        for j in range(h):
            for i in range(w):
                v = px[j * w + i]
                if v != 0xffff:
                    atlas[(ay + j) * 256 + ax + i] = v & 0x7f
    write_tex(os.path.join(out, 'mario.tex'), bytes(atlas), 256, 256, 256, TRANSPARENT)
    with open(os.path.join(out, 'mario.frm'), 'wb') as f:
        f.write(b'HMFR' + struct.pack('<2H', len(uniq), len(slot_addrs)))
        for o in uniq:
            w, h, ox, oy, _ = frames[o]
            f.write(struct.pack('<4H2h', place[o][0], place[o][1], w, h, ox, oy))
        for a in slot_addrs:
            f.write(struct.pack('<H', uniq.index(a)))
    # the score bar (scoring): compiled sprites in the main program, plane A colours.
    # slots: 0-9 digits (scoring_number_y_N), 10 blank, 11 MARIO, 12 LUIGI, 13 x,
    # 14 TIME, 15 coin, 16 big clock, 17 STAGE
    hud = [0x14662, 0x146fe, 0x1479a, 0x14836, 0x148d2, 0x1496e, 0x14a0a, 0x14aa6, 0x14b42,
           0x14bde, 0x14c7a, 0x14d16, 0x14f10, 0x150b6, 0x15152, 0x152ea, 0x153c4, 0x155be]
    hfr = D.run_frames(runner, img[:0x100000], hud, tmp)
    write_atlas(os.path.join(out, 'hud.tex'), os.path.join(out, 'hud.frm'), hfr, hud, 128)
    # fireball (sm_f_ball_set_clear, _0.._5) and kill star (kill_star_set_clear, _0)
    fx = [0x15de6, 0x157d4, 0x158dc, 0x159ee, 0x15ad8, 0x15bee, 0x15d00, 0x14446, 0x1428e]
    ffr = D.run_frames(runner, img[:0x100000], fx, tmp)
    write_atlas(os.path.join(out, 'fx.tex'), os.path.join(out, 'fx.frm'), ffr, fx, 64)
    # ported objects: atlas from the first module that has each one
    done = set()
    dats = sorted(glob.glob(os.path.join(src, 'L*_dat.rtf.raw')))
    dats = [d for d in dats if '/L0_' not in d] + [d for d in dats if '/L0_' in d]   # unused hotel 0 last
    for dat in dats:
        hotel = int(os.path.basename(dat)[1])
        nst = len(map_records(os.path.join(src, 'cdi_L%d_dat.map' % hotel))) // 4
        for stage in range(nst):
            for upper in ([False, True] if hotel >= 7 else [False]):
                module, _ = stage_record(dat, stage, upper)
                for name, offs in object_frames(module).items():
                    if name in PORTED_OBJECTS and name not in done and offs:
                        open(os.path.join(tmp, 'obj.o'), 'wb').write(module)
                        fr = D.run_frames(runner, module, sorted(set(offs)), tmp)
                        if all(o in fr for o in offs):
                            write_atlas(os.path.join(out, 'obj_%s.tex' % name),
                                        os.path.join(out, 'obj_%s.frm' % name), fr, offs,
                                        512 if name == 'bowser' else 256)
                            done.add(name)
                            print('object %s: %d frames (from L%d stage %d%s)' %
                                  (name, len(offs), hotel, stage + 1, ' upper' if upper else ''))

    missing = set(PORTED_OBJECTS) - done
    if missing:
        raise ValueError('Missing object atlases: %s' % sorted(missing))
    from make_stage_extensions import main as make_stage_extensions
    make_stage_extensions(src, out)
    from make_ui_assets import main as make_ui_assets
    make_ui_assets(src, out)

    print('assets: %d stages (title cards + stage picture + colour cycling each), %d Mario frames / %d slots -> %s'
          % (len(index), len(uniq), len(slot_addrs), out))

if __name__ == '__main__':
    main()
