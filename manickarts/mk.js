/* mk.js - the parts of the Manic Karts build that run as JavaScript (in the page's worker,
 * or under Node for testing). Ports of the project's own tools:
 *   cue / CD image reading       tools/extract_cd.py and tools/mkmusic.c
 *   unkart                       tools/unkart.c (KART.DAT, LZHUF)
 *   music                        tools/mkmusic.c (the CD's audio as AICA ADPCM)
 *   vqTexture                    tools/build_vq.py (the karts, VQ compressed)
 *   makeDisc                     what mkdcdisc does: the ISO 9660 data track, written as
 *                                CD-XA Mode 2 Form 1 sectors, in a DiscJuggler (.cdi) image
 * Every function here is synchronous. File access goes through read(file, offset, length),
 * which returns a Uint8Array. */
"use strict";

var MK = (function () {

/* ---------------------------------------------------------------- the CD image */

/* a .cue sheet -> [{num, type ("AUDIO" / "MODE1/2352" ...), file, start (byte in that file)}] */
function parseCue(text) {
    var tracks = [], file = null, cur = null;
    text.split(/\r?\n/).forEach(function (line) {
        var m;
        if ((m = line.match(/^\s*FILE\s+"([^"]+)"/i)) || (m = line.match(/^\s*FILE\s+(\S+)/i))) {
            file = m[1].replace(/^.*[\\/]/, "");
        } else if ((m = line.match(/^\s*TRACK\s+(\d+)\s+(\S+)/i))) {
            cur = { num: parseInt(m[1], 10), type: m[2].toUpperCase(), file: file, start: -1 };
            tracks.push(cur);
        } else if (cur && (m = line.match(/^\s*INDEX\s+01\s+(\d+):(\d+):(\d+)/i))) {
            cur.start = ((parseInt(m[1], 10) * 60 + parseInt(m[2], 10)) * 75 + parseInt(m[3], 10)) * 2352;
        }
    });
    return tracks;
}

/* the data track's ISO 9660 file system: {name: {lba, size}} for the files in its root */
function isoRoot(read, file, base, sectorSize, dataOffset) {
    function sectors(lba, n) {
        var out = new Uint8Array(n * 2048);
        if (sectorSize === 2048) return read(file, base + lba * 2048, n * 2048);
        var raw = read(file, base + lba * sectorSize, n * sectorSize);
        for (var i = 0; i < n; i++) out.set(raw.subarray(i * sectorSize + dataOffset, i * sectorSize + dataOffset + 2048), i * 2048);
        return out;
    }
    var pvd = sectors(16, 1);
    if (String.fromCharCode(pvd[1], pvd[2], pvd[3], pvd[4], pvd[5]) !== "CD001") return null;
    var dv = new DataView(pvd.buffer, pvd.byteOffset);
    var lba = dv.getUint32(156 + 2, true), size = dv.getUint32(156 + 10, true);
    var d = sectors(lba, Math.ceil(size / 2048)), files = {}, o = 0;
    while (o < d.length) {
        var n = d[o];
        if (n === 0) { o = (Math.floor(o / 2048) + 1) * 2048; continue; }
        var e = new DataView(d.buffer, d.byteOffset + o), name = "";
        for (var i = 0; i < d[o + 32]; i++) name += String.fromCharCode(d[o + 33 + i]);
        name = name.split(";")[0].toUpperCase();
        files[name] = { lba: e.getUint32(2, true), size: e.getUint32(10, true) };
        o += n;
    }
    files._sectors = sectors;
    return files;
}

function isoFile(iso, name) {
    var f = iso[name], out = new Uint8Array(f.size), at = 0, lba = f.lba;
    while (at < f.size) {
        var n = Math.min(256, Math.ceil((f.size - at) / 2048));
        var s = iso._sectors(lba, n);
        out.set(s.subarray(0, Math.min(s.length, f.size - at)), at);
        at += s.length; lba += n;
    }
    return out;
}

/* ---------------------------------------------------------------- unkart (KART.DAT) */

function unkart(dat, exe, progress) {
    var N = 1024, F = 24, TH = 2, NCH = 256 - TH + F, T = NCH * 2 - 1, R = T - 1, MAXF = 0x8000;
    var freq = new Uint32Array(T + 1), prnt = new Int32Array(T + NCH), son = new Int32Array(T);
    var dcode = null, dlen = null, i, j;
    for (i = 0; i + 512 < exe.length; i++) {
        if (exe[i] === 0 && exe[i + 31] === 0 && exe[i + 32] === 1 && exe[i + 255] === 63 &&
            exe[i + 0x128] === 3 && exe[i + 0x127] === 63) {
            dcode = exe.slice(i, i + 256); dlen = exe.slice(i + 0x128, i + 0x128 + 256); break;
        }
    }
    if (!dcode) throw new Error("K.EXE: the unpacking tables weren't found");
    var dp = 0, bitbuf = 0, bitlen = 0, datlen = dat.length;
    function getbit() {
        if (!bitlen) { bitbuf = dp < datlen ? dat[dp] : 0; dp++; bitlen = 8; }
        bitlen--; return (bitbuf >> bitlen) & 1;
    }
    function getbyte() { var v = 0; for (var k = 0; k < 8; k++) v = (v << 1) | getbit(); return v; }
    function start() {
        var a, b; bitlen = 0;
        for (a = 0; a < NCH; a++) { freq[a] = 1; son[a] = a + T; prnt[a + T] = a; }
        a = 0; b = NCH;
        while (b <= R) { freq[b] = freq[a] + freq[a + 1]; son[b] = a; prnt[a] = prnt[a + 1] = b; a += 2; b++; }
        freq[T] = 0xffff; prnt[R] = 0;
    }
    function reconst() {
        var a, b, k, f, l;
        for (a = b = 0; a < T; a++) if (son[a] >= T) { freq[b] = (freq[a] + 1) >>> 1; son[b] = son[a]; b++; }
        for (a = 0, b = NCH; b < T; a += 2, b++) {
            k = a + 1; f = freq[b] = freq[a] + freq[k];
            for (k = b - 1; f < freq[k]; k--);
            k++; l = b - k;
            freq.copyWithin(k + 1, k, k + l); freq[k] = f;
            son.copyWithin(k + 1, k, k + l); son[k] = a;
        }
        for (a = 0; a < T; a++) { if ((k = son[a]) >= T) prnt[k] = a; else prnt[k] = prnt[k + 1] = a; }
    }
    function update(c) {
        var a, b, l, k;
        if (freq[R] === MAXF) reconst();
        c = prnt[c + T];
        do {
            k = ++freq[c];
            if (k > freq[l = c + 1]) {
                while (k > freq[++l]);
                l--;
                freq[c] = freq[l]; freq[l] = k;
                a = son[c]; prnt[a] = l; if (a < T) prnt[a + 1] = l;
                b = son[l]; son[l] = a; prnt[b] = c; if (b < T) prnt[b + 1] = c;
                son[c] = b; c = l;
            }
        } while ((c = prnt[c]) !== 0);
    }
    function dchar() { var c = son[R]; while (c < T) { c += getbit(); c = son[c]; } c -= T; update(c); return c; }
    function dpos() {
        var a = getbyte(), c = dcode[a] << 6, b = dlen[a] - 2;
        while (b-- > 0) a = (a << 1) + getbit();
        return c | (a & 0x3f);
    }
    var n = (dat[1] | dat[2] << 8) ^ 0x4321, files = {}, ring = new Uint8Array(N);
    dp = 3 + 18 * n;
    for (var idx = 0; idx < n; idx++) {
        var b0 = 3 + 18 * idx, name = "";
        for (j = 0; j < 14; j++) { var ch = dat[b0 + j] ^ ((idx + 1) & 255); if (!ch) break; name += String.fromCharCode(ch); }
        var sz = ((dat[b0 + 14] | dat[b0 + 15] << 8 | dat[b0 + 16] << 16 | dat[b0 + 17] << 24) ^ ((0xcccccccc - (idx + 1)) >>> 0)) >>> 0;
        start(); ring.fill(32);
        var r = N - F, o = 0, out = new Uint8Array(sz);
        while (o < sz) {
            var c = dchar();
            if (c < 256) { out[o++] = c; ring[r] = c; r = (r + 1) & (N - 1); }
            else {
                var at = (r - dpos() - 1) & (N - 1), m = c - 255 + TH;
                for (var k = 0; k < m; k++) {
                    var c2 = ring[(at + k) & (N - 1)];
                    if (o < sz) out[o++] = c2;
                    ring[r] = c2; r = (r + 1) & (N - 1);
                }
            }
        }
        files[name] = out;
        if (progress && idx % 25 === 0) progress(idx / n);
    }
    return files;
}

/* ---------------------------------------------------------------- the music */

/* one CD audio track (16-bit stereo, 44100 Hz) -> mono AICA 4-bit ADPCM, padded to 2 KB */
function MusicEncoder() {
    var history = 0, step = 127, nibble = 0, byte = 0, out = [], cur = new Uint8Array(1 << 20), n = 0;
    var table = [230, 230, 230, 230, 307, 409, 512, 614];
    function put(v) {
        cur[n++] = v;
        if (n === cur.length) { out.push(cur); cur = new Uint8Array(1 << 20); n = 0; }
    }
    function encode(sample) {
        var d = (sample & -8) - history;
        var a = Math.trunc((Math.abs(d) << 16) / (step << 14));   /* (as the C: the int can wrap negative, */
        if (a < 0) a = 7;                                         /*  then is clamped as unsigned) */
        if (a > 7) a = 7;
        if (d < 0) a |= 8;
        if (!nibble) byte = a & 15; else put(byte | (a << 4));
        nibble ^= 1;
        /* ymz_step */
        var delta = a & 7, diff = ((1 + (delta << 1)) * step) >> 3, nv = history;
        var nstep = (table[delta] * step) >> 8;
        if (diff > 32767) diff = 32767;
        if (a & 8) nv -= diff; else nv += diff;
        step = nstep > 24576 ? 24576 : nstep < 127 ? 127 : nstep;
        history = nv > 32767 ? 32767 : nv < -32768 ? -32768 : nv;
    }
    this.add = function (bytes) {            /* whole 4-byte frames */
        for (var i = 0; i + 4 <= bytes.length; i += 4) {
            var l = (bytes[i] | bytes[i + 1] << 8) << 16 >> 16, r = (bytes[i + 2] | bytes[i + 3] << 8) << 16 >> 16;
            encode(((l + r) / 2) | 0);
        }
    };
    this.finish = function () {
        if (nibble) put(byte);
        var total = out.length * cur.length + n;
        while (total % 2048) { put(0x08); total++; }
        var res = new Uint8Array(total), at = 0;
        out.forEach(function (c) { res.set(c, at); at += c.length; });
        res.set(cur.subarray(0, n), at);
        return res;
    };
}

/* ---------------------------------------------------------------- the karts (VQ) */

var SPREAD = new Uint32Array(1024);
for (var s = 0; s < 1024; s++) { var v = 0; for (var bit = 0; bit < 10; bit++) if (s & (1 << bit)) v |= 1 << (2 * bit); SPREAD[s] = v; }

/* idx: 1024x1024 colour numbers (0 = see-through); pal: 256 x (r, g b) ->
 * {data: 2048 bytes of codebook + 512x512 twiddled block numbers, err: rms error}.
 * The same calculation as build_vq.py's vq_texture, step for step. */
function vqTexture(idx, pal) {
    var W = 1024, H = 1024, BW = W / 2, BH = H / 2;
    var lut = new Float64Array(256 * 4);
    for (var c = 1; c < 256; c++) { lut[c * 4] = 510; lut[c * 4 + 1] = pal[c * 3]; lut[c * 4 + 2] = pal[c * 3 + 1]; lut[c * 4 + 3] = pal[c * 3 + 2]; }
    var keyOf = new Map(), count = [], order = new Int32Array(BW * BH), tuples = [];
    for (var by = 0; by < BH; by++) {
        var r0 = 2 * by * W, r1 = (2 * by + 1) * W;
        for (var bx = 0; bx < BW; bx++) {
            var a0 = idx[r0 + 2 * bx], a1 = idx[r1 + 2 * bx], a2 = idx[r0 + 2 * bx + 1], a3 = idx[r1 + 2 * bx + 1];
            var key = (a0 | a1 << 8 | a2 << 16 | a3 << 24) >>> 0, k = keyOf.get(key);
            if (k === undefined) { k = count.length; keyOf.set(key, k); count.push(0); tuples.push(a0, a1, a2, a3); }
            count[k]++;
            order[by * BW + bx] = k;
        }
    }
    var nk = count.length, vec = new Float64Array(nk * 16), d, i, j;
    for (var k = 0; k < nk; k++)
        for (var t = 0; t < 4; t++) for (d = 0; d < 4; d++) vec[k * 16 + t * 4 + d] = lut[tuples[k * 4 + t] * 4 + d];

    function stats(members) {
        var wsum = 0, mean = new Float64Array(16), vr = new Float64Array(16), m, kk, w;
        for (m = 0; m < members.length; m++) wsum += count[members[m]];
        for (m = 0; m < members.length; m++) { kk = members[m] * 16; w = count[members[m]]; for (d = 0; d < 16; d++) mean[d] += vec[kk + d] * w; }
        for (d = 0; d < 16; d++) mean[d] /= wsum;
        for (m = 0; m < members.length; m++) {
            kk = members[m] * 16; w = count[members[m]];
            for (d = 0; d < 16; d++) { var x = vec[kk + d] - mean[d]; vr[d] += w * (x * x); }
        }
        var tot = 0; for (d = 0; d < 16; d++) tot += vr[d];
        return [tot, members, mean, vr];
    }
    var all = []; for (k = 0; k < nk; k++) all.push(k);
    var clusters = [stats(all)];
    while (clusters.length < 256) {
        clusters.sort(function (p, q) { return p[0] - q[0]; });
        var top = clusters[clusters.length - 1];
        if (top[0] <= 0 || top[1].length < 2) break;
        clusters.pop();
        var vr = top[3], dd = 0;
        for (d = 1; d < 16; d++) if (vr[d] > vr[dd]) dd = d;
        var mem = top[1].slice().sort(function (p, q) { return vec[p * 16 + dd] - vec[q * 16 + dd]; });
        var tot = 0; for (i = 0; i < mem.length; i++) tot += count[mem[i]];
        var half = tot / 2, acc = 0, cut = 1;
        for (i = 0; i < mem.length; i++) {
            acc += count[mem[i]];
            if (acc >= half) { cut = Math.max(1, Math.min(mem.length - 1, i + 1)); break; }
        }
        clusters.push(stats(mem.slice(0, cut)));
        clusters.push(stats(mem.slice(cut)));
    }
    var nc = clusters.length, means = new Float64Array(nc * 16), code = new Int32Array(nk);
    for (i = 0; i < nc; i++) {
        means.set(clusters[i][2], i * 16);
        var ms = clusters[i][1]; for (j = 0; j < ms.length; j++) code[ms[j]] = i;
    }
    var NEAR = Math.min(24, nc), near = new Int32Array(nc * NEAR), dist = new Float64Array(nc), ids = new Array(nc);
    for (var round = 0; round < 10; round++) {
        for (i = 0; i < nc; i++) {
            for (j = 0; j < nc; j++) {
                var s2 = 0; for (d = 0; d < 16; d++) { var q = means[i * 16 + d] - means[j * 16 + d]; s2 += q * q; }
                dist[j] = s2; ids[j] = j;
            }
            ids.sort(function (p, q2) { return dist[p] - dist[q2]; });
            for (j = 0; j < NEAR; j++) near[i * NEAR + j] = ids[j];
        }
        for (k = 0; k < nk; k++) {
            var x0 = k * 16, best = code[k], bd = -1, nb = code[k] * NEAR;
            for (j = 0; j < NEAR; j++) {
                var cj = near[nb + j], m0 = cj * 16, s3 = 0;
                for (d = 0; d < 16; d++) {
                    var tt = vec[x0 + d] - means[m0 + d];
                    s3 += tt * tt;
                    if (bd >= 0 && s3 >= bd) break;
                }
                if (bd < 0 || s3 < bd) { best = cj; bd = s3; }
            }
            code[k] = best;
        }
        var sums = new Float64Array(nc * 16), wts = new Float64Array(nc);
        for (k = 0; k < nk; k++) {
            var ci = code[k], w = count[k];
            wts[ci] += w;
            for (d = 0; d < 16; d++) sums[ci * 16 + d] += vec[k * 16 + d] * w;
        }
        for (i = 0; i < nc; i++) if (wts[i]) for (d = 0; d < 16; d++) means[i * 16 + d] = sums[i * 16 + d] / wts[i];
    }
    var err = 0;
    for (k = 0; k < nk; k++) {
        var e = 0; for (d = 0; d < 16; d++) { var z = vec[k * 16 + d] - means[code[k] * 16 + d]; e += z * z; }
        err += count[k] * e;
    }
    var outb = new Uint8Array(2048 + BW * BH);
    function clamp(x) { return Math.trunc(Math.min(255, Math.max(0, x))); }
    for (i = 0; i < nc; i++) for (t = 0; t < 4; t++) {
        var o = i * 16 + t * 4, val = 0;
        if (means[o] >= 255) val = 0x8000 | (clamp(means[o + 1]) >> 3) << 10 | (clamp(means[o + 2]) >> 3) << 5 | (clamp(means[o + 3]) >> 3);
        outb[(i * 4 + t) * 2] = val & 255; outb[(i * 4 + t) * 2 + 1] = val >> 8;
    }
    for (var y = 0; y < BH; y++) for (var x2 = 0; x2 < BW; x2++) outb[2048 + (SPREAD[y] | SPREAD[x2] << 1)] = code[order[y * BW + x2]];
    return { data: outb, err: Math.sqrt(err / (BW * BH * 16)) };
}

/* ---------------------------------------------------------------- the disc */

/* CD-XA Mode 2 Form 1 sectors, as a .cdi stores them: 2336 bytes (subheader, 2048 of data,
 * EDC, ECC), without the sync and header */
var ECC_F = new Uint8Array(256), ECC_B = new Uint8Array(256), EDC = new Uint32Array(256);
for (var e1 = 0; e1 < 256; e1++) {
    var f1 = (e1 << 1) ^ (e1 & 0x80 ? 0x11d : 0);
    ECC_F[e1] = f1; ECC_B[e1 ^ f1] = e1;
    var ed = e1; for (var b1 = 0; b1 < 8; b1++) ed = (ed >>> 1) ^ (ed & 1 ? 0xd8018001 : 0);
    EDC[e1] = ed >>> 0;
}
var SEC = new Uint8Array(2352);
function eccBlock(majorCount, minorCount, majorMult, minorInc, dest) {
    var size = majorCount * minorCount;
    for (var major = 0; major < majorCount; major++) {
        var index = (major >> 1) * majorMult + (major & 1), a = 0, b = 0;
        for (var minor = 0; minor < minorCount; minor++) {
            var t = SEC[12 + index];
            index += minorInc; if (index >= size) index -= size;
            a ^= t; b ^= t; a = ECC_F[a];
        }
        a = ECC_B[ECC_F[a] ^ b];
        SEC[dest + major] = a; SEC[dest + major + majorCount] = a ^ b;
    }
}
/* data (up to 2048 bytes) -> its 2336-byte sector at out[at] */
function form1Sector(data, out, at) {
    SEC.fill(0, 12, 2352);
    SEC[18] = 9; SEC[22] = 9;                               /* subheader 00 00 09 00 x2: data, end of record */
    SEC.set(data, 24);
    var edc = 0;
    for (var i = 16; i < 16 + 2056; i++) edc = (edc >>> 8) ^ EDC[(edc ^ SEC[i]) & 255];
    SEC[2072] = edc & 255; SEC[2073] = (edc >>> 8) & 255; SEC[2074] = (edc >>> 16) & 255; SEC[2075] = edc >>> 24;
    eccBlock(86, 24, 2, 86, 2076);                          /* P (header counted as zero, Mode 2) */
    eccBlock(52, 43, 86, 88, 2248);                         /* Q */
    out.set(SEC.subarray(16), at);
}

/* the gap sectors around the data track, as mkdcdisc writes them */
var GAP = new Uint8Array(2336);
GAP.set([0, 0, 0x20, 0, 0, 0, 0x20, 0]); GAP.set([0x3f, 0x13, 0xb0, 0xbe], 2332);

function both32(dv, o, v) { dv.setUint32(o, v, true); dv.setUint32(o + 4, v, false); }
function both16(dv, o, v) { dv.setUint16(o, v, true); dv.setUint16(o + 2, v, false); }
function isoDate7(dv, o, d) {
    dv.setUint8(o, d.getUTCFullYear() - 1900); dv.setUint8(o + 1, d.getUTCMonth() + 1); dv.setUint8(o + 2, d.getUTCDate());
    dv.setUint8(o + 3, d.getUTCHours()); dv.setUint8(o + 4, d.getUTCMinutes()); dv.setUint8(o + 5, d.getUTCSeconds()); dv.setUint8(o + 6, 0);
}
function isoDate17(u8, o, d) {
    var s = d.toISOString().replace(/[-:T]/g, "").slice(0, 14) + "00";
    for (var i = 0; i < 16; i++) u8[o + i] = s.charCodeAt(i);
    u8[o + 16] = 0;
}
function putStr(u8, o, s, len) { for (var i = 0; i < len; i++) u8[o + i] = i < s.length ? s.charCodeAt(i) : 32; }

function dirRecord(name, lba, size, isDir, date) {
    var nl = name.length, len = 33 + nl + (nl % 2 === 0 ? 1 : 0), r = new Uint8Array(len), dv = new DataView(r.buffer);
    r[0] = len; both32(dv, 2, lba); both32(dv, 10, size); isoDate7(dv, 18, date);
    r[25] = isDir ? 2 : 0; both16(dv, 28, 1); r[32] = nl;
    for (var i = 0; i < nl; i++) r[33 + i] = name.charCodeAt(i);
    return r;
}
/* records -> a directory's sectors (no record crosses a sector boundary) */
function packDir(records) {
    var secs = [new Uint8Array(2048)], at = 0;
    records.forEach(function (r) {
        if (at + r.length > 2048) { secs.push(new Uint8Array(2048)); at = 0; }
        secs[secs.length - 1].set(r, at); at += r.length;
    });
    return secs;
}

/* opts: {ipbin (32 KB), firstRead (1ST_READ.BIN), files: [[name, Uint8Array]] (data/),
 *        volume, isoSectors (the data track's length, the files at its end, as mkdcdisc),
 *        progress(fraction)}
 * -> an array of Uint8Array pieces, the .cdi in order */
function makeDisc(opts) {
    var START = 11702, date = new Date(), vol = opts.volume || "MANIC_KARTS";
    var files = opts.files.map(function (f) { return [f[0].toUpperCase() + ";1", f[1]]; });
    files.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0; });
    function nsec(n) { return Math.ceil(n / 2048); }
    /* the directory first, to know its length */
    var dataRecs = function (dirLba, dirLen, rootLba) {
        var recs = [dirRecord("\0", dirLba, dirLen, true, date), dirRecord("\x01", rootLba, 2048, true, date)];
        files.forEach(function (f) { recs.push(dirRecord(f[0], f.lba, f[1].length, false, date)); });
        return recs;
    };
    files.forEach(function (f) { f.lba = 0; });
    var dirSecs = packDir(dataRecs(0, 0, 0)).length;
    var used = 0; files.forEach(function (f) { used += nsec(f[1].length); });
    var fr = opts.firstRead, frSecs = nsec(fr.length);
    var head = 21 + dirSecs, total = Math.max(opts.isoSectors || 0, head + frSecs + used);
    /* the files at the end of the track (mkdcdisc pads the disc so they are on its outer
     * edge, where a real drive reads fastest) */
    var at = total - used - frSecs, frLba = at;
    at += frSecs;
    files.forEach(function (f) { f.lba = START + at; at += nsec(f[1].length); });
    var rootLba = START + 20, dataLba = START + 21;
    var dataDir = packDir(dataRecs(dataLba, dirSecs * 2048, rootLba));
    var rootDir = packDir([dirRecord("\0", rootLba, 2048, true, date), dirRecord("\x01", rootLba, 2048, true, date),
                           dirRecord("1ST_READ.BIN;1", START + frLba, fr.length, false, date),
                           dirRecord("DATA", dataLba, dirSecs * 2048, true, date)]);
    /* path tables */
    function pathTable(be) {
        var t = new Uint8Array(2048), dv = new DataView(t.buffer);
        t[0] = 1; dv.setUint32(2, rootLba, !be); dv.setUint16(6, 1, !be); t[8] = 0;
        t[10] = 4; dv.setUint32(12, dataLba, !be); dv.setUint16(16, 1, !be); putStr(t, 18, "DATA", 4);
        return t;
    }
    var pvd = new Uint8Array(2048), dv = new DataView(pvd.buffer);
    pvd[0] = 1; putStr(pvd, 1, "CD001", 5); pvd[6] = 1;
    putStr(pvd, 8, "", 32); putStr(pvd, 40, vol, 32);
    both32(dv, 80, total); both16(dv, 120, 1); both16(dv, 124, 1); both16(dv, 128, 2048);
    both32(dv, 132, 22); dv.setUint32(140, START + 18, true); dv.setUint32(148, START + 19, false);
    pvd.set(dirRecord("\0", rootLba, 2048, true, date), 156);
    putStr(pvd, 190, "", 128); putStr(pvd, 318, "", 128); putStr(pvd, 446, "BUILDMYCDI.CLOUD", 128); putStr(pvd, 574, "", 128);
    putStr(pvd, 702, "", 37); putStr(pvd, 739, "", 37); putStr(pvd, 776, "", 37);
    isoDate17(pvd, 813, date); isoDate17(pvd, 830, date);
    putStr(pvd, 847, "0000000000000000", 16); putStr(pvd, 864, "0000000000000000", 16);
    pvd[881] = 1;
    var term = new Uint8Array(2048); term[0] = 255; putStr(term, 1, "CD001", 5); term[6] = 1;

    /* the sectors, in pieces */
    var pieces = [], done = 0, todo = total;
    function encode(bytes, nSectors) {          /* bytes -> nSectors sectors */
        var CH = 512;
        for (var s0 = 0; s0 < nSectors; s0 += CH) {
            var n = Math.min(CH, nSectors - s0), out = new Uint8Array(n * 2336);
            for (var i = 0; i < n; i++) {
                var o = (s0 + i) * 2048;
                form1Sector(o < bytes.length ? bytes.subarray(o, Math.min(o + 2048, bytes.length)) : new Uint8Array(0), out, i * 2336);
            }
            pieces.push(out);
            done += n;
            if (opts.progress) opts.progress(done / todo);
        }
    }
    var zero = new Uint8Array(2336 * 256), zs = new Uint8Array(0);
    for (var z = 0; z < 256; z++) form1Sector(zs, zero, z * 2336);
    function blank(n) {
        while (n > 0) { var k = Math.min(256, n); pieces.push(k === 256 ? zero : zero.subarray(0, k * 2336)); n -= k; done += k; }
        if (opts.progress) opts.progress(done / todo);
    }
    /* session 1: one blank audio track (150 + 302 sectors of 2352), session 2: the data */
    pieces.push(new Uint8Array(452 * 2352));
    var gaps = new Uint8Array(150 * 2336); for (var g = 0; g < 150; g++) gaps.set(GAP, g * 2336);
    pieces.push(gaps);
    encode(opts.ipbin, 16);
    var meta = new Uint8Array(2048 * (head - 16)), mo = 0;
    [pvd, term, pathTable(false), pathTable(true)].concat(rootDir, dataDir).forEach(function (s) { meta.set(s, mo); mo += 2048; });
    encode(meta, head - 16);
    blank(frLba - head);
    encode(fr, frSecs);
    files.forEach(function (f) { encode(f[1], nsec(f[1].length)); });
    pieces.push(gaps.subarray(0, 2 * 2336));
    pieces.push(cdiHeader(total, opts.name || "manic-karts.cdi"));
    return pieces;
}

/* the .cdi's description of its sessions and tracks, as mkdcdisc's write_cdi_header */
function cdiHeader(isoSectors, filename) {
    var b = [], u8 = function (v) { b.push(v & 255); }, u16 = function (v) { u8(v); u8(v >> 8); },
        u32 = function (v) { u16(v & 0xffff); u16(v >>> 16); }, raw = function (a) { a.forEach(u8); },
        zeros = function (n) { for (var i = 0; i < n; i++) b.push(0); };
    var marker = [0, 0, 1, 0, 0, 0, 255, 255, 255, 255], fname = [];
    for (var i = 0; i < 32; i++) fname.push(i < filename.length ? filename.charCodeAt(i) : 0);   /* (strncpy: zero padded) */
    function trackHeader() {
        raw(marker); raw(marker); raw([0xab, 0, 0x10]); u8(2); u8(32); raw(fname);
        zeros(11); u32(2); u32(0); u32(0x80000000); u32(360000); u32(0x980000);
    }
    var tracks = [
        { session: 0, pregap: 150, length: 302, mode: 0, lba: 0, audio: true },
        { session: 1, pregap: 150, length: isoSectors + 2, mode: 2, lba: 11702, audio: false }];
    u8(2);                                                  /* sessions */
    tracks.forEach(function (t) {
        u8(0); u16(1); u32(0);                              /* session header: 1 track */
        trackHeader();
        u16(2); u32(t.pregap); u32(t.length); zeros(6); u32(t.mode); u32(0); u32(t.session); u32(0);
        u32(t.lba); u32(t.length + t.pregap); zeros(16); u32(t.audio ? 2 : 1); u32(t.audio ? 0 : 4);
        u8(0); u32(t.length + t.pregap); u32(0); zeros(12); u32(0); u8(0);
        raw([255, 255, 255, 255, 255, 255, 255, 255]); u32(1); u32(0x80); u32(2); u32(0x10); u32(44100);
        zeros(42); u32(0xffffffff); zeros(12); u8(t.mode); zeros(5); u8(0); u8(0); u32(t.lba);
    });
    var end = 452 + 11250 + (150 + isoSectors + 2) + 6750;   /* (sessions with their lead-in/out, as mkdcdisc counts them) */
    u8(0); u16(0); u32(0); trackHeader();
    u32(end); u8(32); raw(fname); u8(0); u32(1); u32(1); zeros(13); u32(0); u8(0); u32(0); u32(0); zeros(3); u32(0x80000006);
    var len = b.length + 4;
    u32(len);
    return new Uint8Array(b);
}

return { parseCue: parseCue, isoRoot: isoRoot, isoFile: isoFile, unkart: unkart, MusicEncoder: MusicEncoder,
         vqTexture: vqTexture, makeDisc: makeDisc, cdiHeader: cdiHeader, form1Sector: form1Sector };
})();

if (typeof module !== "undefined") module.exports = MK;
