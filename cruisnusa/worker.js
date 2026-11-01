/* worker.js - builds the Cruis'n USA .cdi from the visitor's own N64 ROM, in the browser.
 * The steps are those of the project's dc/game/build.sh. The game program (compiled from
 * the release, cruisn-game.elf) is prebuilt with its four data segments left empty: they
 * are made here from the ROM by the project's own tools/port_data_apply.py and put in.
 *   message in:  {file: File}               (the ROM: .z64, .n64 or .v64, or a .zip of one)
 *   messages out: {log}, {step, frac}, {done: Blob, size}, {error} */
"use strict";
importScripts("../common/disc.js");

var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.1/full/";
var ROM_SHA1 = "71bb3d8850b6a4a294aeca2abad1f936e4f85f0f";
var TOOLS = ["crlib.py", "port_assets.py", "port_data_apply.py", "port_realnames.py", "port_sound.py",
             "midi_to_sseq.py", "midiparse.py"];
var RECIPES = ["seg_main.recipe", "seg_ovl1.recipe", "seg_ovl2.recipe", "seg_ovl3.recipe"];

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
        throw new Error('This ROM ("' + title + '") isn\'t Cruis\'n USA (USA) (Rev 1), the version the port is made from. ' +
                        "(Its SHA-1 is " + sum + ".)");
    }
    log("Cruis'n USA (USA) (Rev 1): ROM checked");

    /* 2. the game's data from the ROM: the project's own Python tools */
    step("Starting Python", 0);
    importScripts(PYODIDE + "pyodide.js");
    var py = await loadPyodide({ indexURL: PYODIDE });
    py.setStdout({ batched: function (s) { log(s); } });
    py.setStderr({ batched: function (s) { log(s); } });
    var FS = py.FS;
    ["/cr", "/cr/tools", "/cr/port", "/cr/port/music", "/cr/dc", "/cr/dc/game", "/cr/dc/game/gen", "/cr/dc/game/cd"].forEach(function (d) { FS.mkdir(d); });
    FS.writeFile("/cr/baserom.z64", rom);
    TOOLS.forEach(function (f) { FS.writeFile("/cr/tools/" + f, fetchBytes("tools/" + f)); });
    RECIPES.forEach(function (f) { FS.writeFile("/cr/dc/game/gen/" + f, fetchBytes("gen/" + f)); });
    FS.writeFile("/cr/port/music/jingle_bells.mid", fetchBytes("port/music/jingle_bells.mid"));
    py.runPython("import sys, os\nos.chdir('/cr/dc/game')\nsys.path.insert(0, '/cr/tools')");
    var runs = [["Making the game's data segments", "port_data_apply", ["gen"]],
                ["Converting the graphics and tracks", "port_assets", ["cd/ASSETS.BIN"]],
                ["Converting the sound and music", "port_sound", ["../../baserom.z64", "cd/SOUND.BIN"]]];
    for (i = 0; i < runs.length; i++) {
        step(runs[i][0], i / runs.length);
        await new Promise(function (r) { setTimeout(r, 0); });
        py.globals.set("ARGS", py.toPy(["x"].concat(runs[i][2])));
        py.runPython("import runpy\nsys.argv = list(ARGS)\nrunpy.run_path('/cr/tools/" + runs[i][1] + ".py', run_name='__main__')");
    }

    /* 3. the game program: the segments into the prebuilt program, then scrambled */
    step("Putting the game program together", 0);
    var prog = fetchBytes("game/program.bin"), info = JSON.parse(new TextDecoder().decode(fetchBytes("game/program.json")));
    Object.keys(info.segments).forEach(function (name) {
        var at = info.segments[name][0], len = info.segments[name][1];
        var seg = FS.readFile("/cr/dc/game/gen/seg_" + name + ".bin");
        if (seg.length !== len) throw new Error("seg_" + name + ".bin is " + seg.length + " bytes, expected " + len);
        prog.set(seg, at);
    });
    var first = MK.scramble(prog);
    var files = [["ASSETS.BIN", FS.readFile("/cr/dc/game/cd/ASSETS.BIN")], ["SOUND.BIN", FS.readFile("/cr/dc/game/cd/SOUND.BIN")]];

    /* 4. the disc image, as mkdcdisc lays it out */
    step("Making the disc image", 0);
    var pieces = MK.makeDisc({ ipbin: fetchBytes("game/IP.BIN"), firstRead: first, files: files, isoSectors: 316208,
        volume: "CRUISN_USA", name: "cruisn-game.cdi", progress: function (f) { step("Making the disc image", f); } });
    var blob = new Blob(pieces, { type: "application/octet-stream" });
    pieces = null;
    postMessage({ done: blob, size: blob.size });
}
