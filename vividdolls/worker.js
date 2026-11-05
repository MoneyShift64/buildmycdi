/* worker.js - builds the Vivid Dolls .cdi from the visitor's own arcade ROM, in the browser.
 * The steps are those of the project's build.sh / tools/make_cdi.sh: the game program
 * (1ST_READ.BIN and IP.BIN, from the release build) is prebuilt and holds no game data; the
 * disc gets the ROM itself as VIVIDOLL.ROM, which the game reads at start-up.
 *   message in:  {file: File}               (nus-zsaj-0.u3, a .z64/.n64/.v64, or a .zip of one)
 *   messages out: {log}, {step, frac}, {done: Blob, size}, {error} */
"use strict";
importScripts("../common/disc.js");

var ROM_SHA1  = "747e9420cce23ff82b5922cd91ec43272d03fa4f";   /* the whole 8 MB ROM, .z64 order */
var KEEP      = 0x23B000;                                     /* the part the game uses */
var KEEP_SHA1 = "eead45676f0104545eaba3abccd4e1bdd59db322";

function log(s) { postMessage({ log: s }); }
function step(name, frac) { postMessage({ step: name, frac: frac }); }
function fetchBytes(url) {
    var x = new XMLHttpRequest();
    x.open("GET", url, false);
    x.responseType = "arraybuffer";
    x.send();
    if (x.status !== 200) throw new Error("Couldn't load " + url + " (" + x.status + ")");
    return new Uint8Array(x.response);
}
async function sha1(b) {
    return Array.prototype.map.call(new Uint8Array(await crypto.subtle.digest("SHA-1", b)),
        function (v) { return (v < 16 ? "0" : "") + v.toString(16); }).join("");
}

onmessage = function (ev) {
    build(ev.data.file).catch(function (e) {
        postMessage({ error: e && e.message ? e.message : String(e) });
    });
};

/* the ROM out of a .zip (stored or deflated) */
async function unzip(z) {
    var dv = new DataView(z.buffer, z.byteOffset, z.byteLength), e = -1;
    for (var i = z.length - 22; i >= Math.max(0, z.length - 65600); i--) if (dv.getUint32(i, true) === 0x06054b50) { e = i; break; }
    if (e < 0) throw new Error("This .zip file couldn't be read.");
    var n = dv.getUint16(e + 10, true), p = dv.getUint32(e + 16, true);
    for (var k = 0; k < n; k++) {
        var method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true);
        var nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true);
        var lho = dv.getUint32(p + 42, true), name = new TextDecoder().decode(z.subarray(p + 46, p + 46 + nl));
        p += 46 + nl + xl + cl;
        if (!/\.(u3|z64|n64|v64|rom|bin)$/i.test(name)) continue;
        var start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true), data = z.subarray(start, start + csize);
        log("Found " + name + " in the .zip");
        if (method === 0) return data;
        if (method !== 8) throw new Error("This .zip file uses a compression method the page can't read: please unzip it first.");
        var s = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        return new Uint8Array(await new Response(s).arrayBuffer());
    }
    throw new Error("No Vivid Dolls ROM (nus-zsaj-0.u3) was found in this .zip file.");
}

async function build(file) {
    /* 1. the ROM, in .z64 (big-endian) byte order, checked */
    step("Reading your ROM", 0);
    var rom = new Uint8Array(await file.arrayBuffer()), i, t;
    if (rom[0] === 0x50 && rom[1] === 0x4b) rom = await unzip(rom);
    if (rom.length < KEEP || rom.length % 4) throw new Error("This doesn't look like the Vivid Dolls ROM (wrong size).");
    var head = (rom[0] << 24 | rom[1] << 16 | rom[2] << 8 | rom[3]) >>> 0;
    if (head === 0x37804012) {                       /* the chip dump (.u3) and .v64: bytes swapped in pairs */
        for (i = 0; i + 1 < rom.length; i += 2) { t = rom[i]; rom[i] = rom[i + 1]; rom[i + 1] = t; }
        log("Converted the ROM from chip-dump byte order");
    } else if (head === 0x40123780) {                /* .n64: little-endian words */
        for (i = 0; i + 3 < rom.length; i += 4) { t = rom[i]; rom[i] = rom[i + 3]; rom[i + 3] = t; t = rom[i + 1]; rom[i + 1] = rom[i + 2]; rom[i + 2] = t; }
        log("Converted the ROM from .n64 byte order");
    } else if (head !== 0x80371240) throw new Error("This doesn't look like the Vivid Dolls ROM (nus-zsaj-0.u3).");
    if (rom.length === 8 * 1024 * 1024) {
        var sum = await sha1(rom);
        if (sum !== ROM_SHA1) throw new Error("This ROM isn't Vivid Dolls, or it's a different version. (Its SHA-1 is " + sum + ".)");
    }
    var used = rom.slice(0, KEEP);
    rom = null;
    var usedSum = await sha1(used);
    if (usedSum !== KEEP_SHA1) throw new Error("This ROM isn't Vivid Dolls, or it's a different version. (SHA-1 of its first part: " + usedSum + ".)");
    log("Vivid Dolls: ROM checked; " + KEEP + " bytes go on the disc as VIVIDOLL.ROM");

    /* 2. the disc image, as mkdcdisc lays it out: the prebuilt game program and the ROM */
    step("Making the disc image", 0);
    var ipbin = fetchBytes("game/IP.BIN"), first = fetchBytes("game/1ST_READ.BIN");
    var pieces = MK.makeDisc({ ipbin: ipbin, firstRead: first, files: [["VIVIDOLL.ROM", used]], isoSectors: 316570,
        volume: "VIVID_DOLLS", name: "vivid-dolls.cdi", progress: function (f) { step("Making the disc image", f); } });
    used = null;
    var blob = new Blob(pieces, { type: "application/octet-stream" });
    pieces = null;
    postMessage({ done: blob, size: blob.size });
}
