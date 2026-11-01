#!/usr/bin/env python3
"""build_vq.py - every kart at full size, in the PowerVR's compressed (VQ) format.

The karts' pictures at full size take 512 KB of video memory each (8 bits a texel),
too much for all eight. VQ (the PowerVR's own compression, drawn directly) keeps 256
blocks of 2x2 ARGB1555 texels and one byte for each 2x2 block of the picture: a
1024x1024 texture holding two karts (64 views of 128x64 each, one kart above the
other) takes 2 KB + 256 KB. Four of them carry all eight karts: about 1 MB in all,
less than the player's full-size kart and the others at half size took before.

  data/XX_kq{E|H}{p}.tex   karts 2p (top half) and 2p+1 (bottom half), 'E' (50/100cc)
                           or 'H' (150/250cc) set: "MKTR", format TEX_VQ1555 | twiddled;
                           2048 bytes of codebook (256 x 4 texels, each block's texels in
                           twiddled order), then the 512x512 block numbers, twiddled

Plain Python, nothing to install: about 10 seconds a texture, a few minutes for all of
them (once). Usage: python3 tools/build_vq.py"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_assets as b

TEX_VQ1555 = 4


def argb1555(c):
    r, g, b_ = c[:3]
    return 0x8000 | (r >> 3) << 10 | (g >> 3) << 5 | (b_ >> 3)


def vq_texture(idx, pal):
    """idx: 1024x1024 colour numbers (0 = see-through) -> (the texture file's texels, rms
    error). The distinct 2x2 blocks are split by a weighted median cut (the cluster with the
    most error split on its widest channel) into 256, then refined by k-means"""
    W = H = 1024
    lut = [(0.0, 0.0, 0.0, 0.0)] + [(510.0, float(c[0]), float(c[1]), float(c[2])) for c in pal[1:256]]
    count, order = {}, []
    for by in range(H // 2):
        r0, r1 = idx[2 * by * W:(2 * by + 1) * W], idx[(2 * by + 1) * W:(2 * by + 2) * W]
        for bx in range(W // 2):
            k = (r0[2 * bx], r1[2 * bx], r0[2 * bx + 1], r1[2 * bx + 1])   # (twiddled order)
            order.append(k)
            count[k] = count.get(k, 0) + 1
    keys = list(count)
    vec = {k: lut[k[0]] + lut[k[1]] + lut[k[2]] + lut[k[3]] for k in keys}

    def stats(members):
        wsum = float(sum(count[k] for k in members))
        mean = [sum(vec[k][d] * count[k] for k in members) / wsum for d in range(16)]
        var = [sum(count[k] * (vec[k][d] - mean[d]) ** 2 for k in members) for d in range(16)]
        return mean, var

    clusters = []
    m, v = stats(keys)
    clusters.append([sum(v), keys, m, v])
    while len(clusters) < 256:
        clusters.sort(key=lambda c: c[0])
        err, members, mean, var = clusters[-1]
        if err <= 0 or len(members) < 2:
            break
        clusters.pop()
        d = max(range(16), key=lambda i: var[i])
        members = sorted(members, key=lambda k: vec[k][d])
        half, acc, cut = sum(count[k] for k in members) / 2.0, 0, 1
        for i, k in enumerate(members):
            acc += count[k]
            if acc >= half:
                cut = max(1, min(len(members) - 1, i + 1))
                break
        for part in (members[:cut], members[cut:]):
            pm, pv = stats(part)
            clusters.append([sum(pv), part, pm, pv])
    # then rounds of k-means, each block trying only the 24 codes nearest its own
    # (what keeps it quick in plain Python)
    means = [c[2] for c in clusters]
    code = {}
    for ci, c in enumerate(clusters):
        for k in c[1]:
            code[k] = ci
    for _ in range(10):
        near = []
        for a in means:
            ds = sorted(range(len(means)), key=lambda j: sum((a[d] - means[j][d]) ** 2 for d in range(16)))
            near.append(ds[:24])
        for k in keys:
            x = vec[k]
            best, bd = code[k], None
            for j in near[code[k]]:
                m = means[j]
                dd = 0.0
                for d in range(16):
                    t = x[d] - m[d]
                    dd += t * t
                    if bd is not None and dd >= bd:
                        break
                if bd is None or dd < bd:
                    best, bd = j, dd
            code[k] = best
        sums = [[0.0] * 16 for _ in means]
        wts = [0] * len(means)
        for k in keys:
            ci, w, x = code[k], count[k], vec[k]
            wts[ci] += w
            sm = sums[ci]
            for d in range(16):
                sm[d] += x[d] * w
        means = [[sm[d] / wts[ci] for d in range(16)] if wts[ci] else means[ci] for ci, sm in enumerate(sums)]
    err = sum(count[k] * sum((vec[k][d] - means[code[k]][d]) ** 2 for d in range(16)) for k in keys)
    book = bytearray()
    for mean in means:
        for t in range(4):
            al, r, g, b_ = mean[t * 4:t * 4 + 4]
            book += struct.pack("<H", 0 if al < 255.0 else argb1555((int(min(255, max(0, r))), int(min(255, max(0, g))),
                                                                      int(min(255, max(0, b_))))))
    book += bytes(2048 - len(book))
    codes = [code[k] for k in order]
    tw = b.twiddle(W // 2, H // 2, codes)
    return bytes(book) + bytes(tw), (err / (len(order) * 16)) ** 0.5


def main():
    tracks = b.track_table()
    done = set()
    for t in tracks:
        theme = t["floors"][0][:2]
        if theme in done:
            continue
        done.add(theme)
        pal = b.load_pal(os.path.join(b.KART, t["pal"]))
        for kind, prefix in (("E", t["karts"]), ("H", t["karts"].replace("ENK", "HNK"))):
            if not os.path.exists(os.path.join(b.KART, prefix + "0.DAT")):
                continue
            for p in range(4):
                top = b.views_atlas(b.kart_views(os.path.join(b.KART, "%s%d.DAT" % (prefix, p)), 0))
                bot = b.views_atlas(b.kart_views(os.path.join(b.KART, "%s%d.DAT" % (prefix, p)), 1))
                data, err = vq_texture(top + bot, pal)
                path = os.path.join(b.OUT, "%s_kq%s%d.tex" % (theme, kind, p))
                with open(path, "wb") as f:
                    f.write(b"MKTR" + struct.pack("<4H", 1024, 1024, TEX_VQ1555 | b.TEX_TWIDDLED, 0))
                    f.write(b.rle(data))
                print("%s  (error %.1f)" % (path, err))


if __name__ == "__main__":
    main()
