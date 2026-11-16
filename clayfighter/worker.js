/* worker.js - builds the ClayFighter: Sculptor's Cut .cdi from the visitor's own N64 ROM, in the
 * browser. The steps are those of the port's build.sh. The game program (the release build's
 * build/cfsc.elf) is prebuilt with the game's data left as zeros: the data is made here from the
 * ROM by the port's own tools/port_data.py and put in by tools/data_apply.py (tools/data/
 * c_info.json is what port_data.py reads from the port's C sources, saved from the same build).
 * The graphics and sound (CFSC.DAT, SOUND.DAT) are made by the port's tools/port_assets.py and
 * port_sound.py, with the game's own MPF decompressor (the port's tools/mpf_unpack.c) as
 * WebAssembly (tools/wasm_unpack.c). The result is the same, byte for byte, as build.sh's.
 *   message in:  {file: File}               (the ROM: .z64, .n64 or .v64, or a .zip of one)
 *   messages out: {log}, {step, frac}, {done: Blob, size}, {error} */
"use strict";
if (typeof MK === "undefined") importScripts("../common/disc.js");

var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.1/full/";
var ROM_SHA1 = "28697fba130ded056bf7c497deaa3e154cce8022";
var TOOLS = ["port_assets.py", "port_sound.py", "port_data.py", "data_apply.py"];
var DATA = ["data_layout.txt", "data_units.txt", "asset_trace.txt", "chardef_layout.txt", "arenadef_layout.txt", "c_info.json"];

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
    if (ev.data.pyodide) PYODIDE = ev.data.pyodide;
    build(ev.data.file).catch(function (e) {
        if (e && e.stack) log(String(e.stack));            /* (where it went wrong, in Details) */
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
        if (!/\.(z64|n64|v64|rom|bin)$/i.test(name)) continue;
        var start = lho + 30 + dv.getUint16(lho + 26, true) + dv.getUint16(lho + 28, true), data = z.subarray(start, start + csize);
        log("Found " + name + " in the .zip");
        if (method === 0) return data;
        if (method !== 8) throw new Error("This .zip file uses a compression method the page can't read: please unzip it first.");
        var s = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
        return new Uint8Array(await new Response(s).arrayBuffer());
    }
    throw new Error("No N64 ROM (.z64, .n64 or .v64) was found in this .zip file.");
}

/* the game's MPF decompressor (WebAssembly); the ROM is kept in its memory */
async function unpacker(rom) {
    var w = (await WebAssembly.instantiate(fetchBytes("tools/mpf_unpack.wasm"), {})).instance.exports;
    w.mem_reset();
    var romAt = w.mem_alloc(rom.length + 16);
    new Uint8Array(w.memory.buffer, romAt, rom.length).set(rom);
    w.mem_mark();
    return function (data, off) {
        w.mem_reset();
        var at = romAt;
        if (data) {                                  /* (a block unpacked before: unpacked again) */
            var b = data.getBuffer("u8");
            try {
                at = w.mem_alloc(b.data.length + 16);
                new Uint8Array(w.memory.buffer, at, b.data.length).set(b.data);
            } finally { b.release(); }
        }
        var got;
        try { got = w.unpack(at, off); } catch (e) { throw new Error("The game's data couldn't be unpacked (at " + off + ")."); }
        return new Uint8Array(w.memory.buffer, w.out_ptr(), got).slice();
    };
}

async function build(file) {
    /* 1. the ROM, in .z64 (big-endian) byte order, checked */
    step("Reading your ROM", 0);
    var rom = new Uint8Array(await file.arrayBuffer()), i, t;
    if (rom[0] === 0x50 && rom[1] === 0x4b) rom = await unzip(rom);
    var head = (rom[0] << 24 | rom[1] << 16 | rom[2] << 8 | rom[3]) >>> 0;
    if (head === 0x37804012) {                       /* .v64: bytes swapped in pairs */
        for (i = 0; i + 1 < rom.length; i += 2) { t = rom[i]; rom[i] = rom[i + 1]; rom[i + 1] = t; }
        log("Converted the ROM from .v64 byte order");
    } else if (head === 0x40123780) {                /* .n64: little-endian words */
        for (i = 0; i + 3 < rom.length; i += 4) { t = rom[i]; rom[i] = rom[i + 3]; rom[i + 3] = t; t = rom[i + 1]; rom[i + 1] = rom[i + 2]; rom[i + 2] = t; }
        log("Converted the ROM from .n64 byte order");
    } else if (head !== 0x80371240) throw new Error("This doesn't look like a Nintendo 64 ROM.");
    var sum = await sha1(rom);
    if (sum !== ROM_SHA1) {
        var title = new TextDecoder().decode(rom.subarray(0x20, 0x34)).trim();
        throw new Error('This ROM ("' + title + '") isn\'t ClayFighter: Sculptor\'s Cut (USA), the version the port is made from. ' +
                        "(Its SHA-1 is " + sum + ".)");
    }
    log("ClayFighter: Sculptor's Cut (USA): ROM checked");

    /* 2. the game's data from the ROM: the port's own Python tools */
    step("Starting Python", 0);
    log("Loading Python from " + PYODIDE);
    if (typeof loadPyodide === "undefined") importScripts(PYODIDE + "pyodide.js");
    var py = await loadPyodide({ indexURL: PYODIDE });
    log("Python started");
    py.setStdout({ batched: function (s) { log(s); } });
    py.setStderr({ batched: function (s) { log(s); } });
    var FS = py.FS;
    ["/cf", "/cf/tools", "/cf/tools/data", "/cf/build", "/cf/build/cd"].forEach(function (d) { FS.mkdir(d); });
    FS.writeFile("/cf/baserom.us.z64", rom);
    TOOLS.forEach(function (f) { FS.writeFile("/cf/tools/" + f, fetchBytes("tools/" + f)); });
    DATA.forEach(function (f) { FS.writeFile("/cf/tools/data/" + f, fetchBytes("tools/data/" + f)); });
    FS.writeFile("/cf/build/defined.txt", fetchBytes("game/defined.txt"));
    py.globals.set("cf_unpack", await unpacker(rom));
    log("Tools loaded");
    rom = null;
    py.runPython([
        "import sys, os, runpy",
        "os.chdir('/cf')",
        "sys.path.insert(0, '/cf/tools')",
        "os.environ['CFSC_LAYOUT'] = 'tools/data/data_layout.txt'",
        "os.environ['CFSC_ROM'] = 'baserom.us.z64'",
        "import json, port_assets, port_sound, port_data",
        "# (port_data.py reads which data the game's C declares as numbers and as byte arrays from the",
        "#  port's src/ folder; tools/data/c_info.json is what it read there for this build)",
        "C_INFO = json.load(open('tools/data/c_info.json'))",
        "port_data.c_types = lambda src: C_INFO['c_types']",
        "port_data.c_arrays = lambda src: C_INFO['c_arrays']",
        "class Unpacker:",
        "    def __init__(self, tool): self.rom = None",
        "    def unpack(self, data, off):",
        "        return cf_unpack(None if data is self.rom else memoryview(bytes(data)), off).to_bytes()",
        "port_assets.Unpacker = Unpacker"].join("\n"));
    var runs = [["Making the game's data", "port_data.main()",
                 ["tools/data/data_layout.txt", "tools/data/data_units.txt", "build/defined.txt", "build/data.S"]],
                ["Converting the graphics", "port_assets.main()",
                 ["baserom.us.z64", "tools/data/asset_trace.txt", "-", "build/cd/CFSC.DAT"]],
                ["Converting the sound and music", "port_sound.main()",
                 ["baserom.us.z64", "-", "build/cd/SOUND.DAT"]]];
    for (i = 0; i < runs.length; i++) {
        step(runs[i][0], i / runs.length);
        await new Promise(function (r) { setTimeout(r, 0); });
        py.globals.set("ARGS", py.toPy(["x"].concat(runs[i][2])));
        py.runPython("sys.argv = list(ARGS)\n" + runs[i][1]);
    }

    /* 3. the game program: the data into the prebuilt program, then scrambled */
    step("Putting the game program together", 0);
    var info = JSON.parse(new TextDecoder().decode(fetchBytes("game/program.json")));
    FS.writeFile("/cf/build/program.bin", fetchBytes("game/program.bin"));
    py.globals.set("ARGS", py.toPy(["x", "build/data.S", "build/program.bin", String(info.data[0])]));
    py.runPython("sys.argv = list(ARGS)\nrunpy.run_path('tools/data_apply.py', run_name='__main__')");
    var first = MK.scramble(FS.readFile("/cf/build/program.bin"));
    var files = [["CFSC.DAT", FS.readFile("/cf/build/cd/CFSC.DAT")], ["SOUND.DAT", FS.readFile("/cf/build/cd/SOUND.DAT")]];

    /* 4. the disc image, as mkdcdisc lays it out */
    step("Making the disc image", 0);
    var pieces = MK.makeDisc({ ipbin: fetchBytes("game/IP.BIN"), firstRead: first, files: files, isoSectors: 316570,
        volume: "CLAYFIGHTER_SC", name: "clayfighter-sc.cdi", progress: function (f) { step("Making the disc image", f); } });
    var blob = new Blob(pieces, { type: "application/octet-stream" });
    pieces = null;
    postMessage({ done: blob, size: blob.size });
}
