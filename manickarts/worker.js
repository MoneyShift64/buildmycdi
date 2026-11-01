/* worker.js - builds the Manic Karts .cdi from the visitor's own CD image, in the browser.
 * The steps are those of the project's build.sh; the game program itself (1ST_READ.BIN and
 * IP.BIN, from the release build) is prebuilt, so nothing needs installing.
 *   message in:  {files: [File...]}          (the .cue and its .bin file(s))
 *   messages out: {log}, {step, frac}, {done: Blob, size}, {error} */
"use strict";
importScripts("mk.js");

var PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.1/full/";
var KEXE_SHA1 = "134bc3e474f2c5b5cb065d7773a442b7cbfdffbb";
/* the usual rip of the PC CD-ROM, for when only the .bin is given */
var KNOWN_CUE = { size: 623207088, cue: 'FILE "MANICKARTS.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n' +
    "  TRACK 02 AUDIO\n    INDEX 01 09:17:33\n  TRACK 03 AUDIO\n    INDEX 01 15:36:39\n  TRACK 04 AUDIO\n    INDEX 01 21:17:14\n" +
    "  TRACK 05 AUDIO\n    INDEX 01 26:59:14\n  TRACK 06 AUDIO\n    INDEX 01 34:51:06\n  TRACK 07 AUDIO\n    INDEX 01 38:38:18\n" +
    "  TRACK 08 AUDIO\n    INDEX 01 43:33:06\n  TRACK 09 AUDIO\n    INDEX 01 48:20:72\n  TRACK 10 AUDIO\n    INDEX 01 53:55:16\n" };
var TOOLS = ["build_assets.py", "build_front.py", "build_race.py", "build_vq.py"];
var MODS = ["RAWALLS1@0,92.png", "RAWALLS2@0,28.png"];

function log(s) { postMessage({ log: s }); }
function step(name, frac) { postMessage({ step: name, frac: frac }); }
var reader = new FileReaderSync();
function fetchBytes(url) {
    var x = new XMLHttpRequest();
    x.open("GET", url, false);
    x.responseType = "arraybuffer";
    x.send();
    if (x.status !== 200) throw new Error("Couldn't load " + url + " (" + x.status + ")");
    return new Uint8Array(x.response);
}

onmessage = function (ev) {
    if (ev.data.pyodide) PYODIDE = ev.data.pyodide;
    build(ev.data.files).catch(function (e) {
        postMessage({ error: e && e.message ? e.message : String(e) });
    });
};

async function build(list) {
    var byName = {}, cueFile = null, i;
    list.forEach(function (f) {
        byName[f.name.toLowerCase()] = f;
        if (/\.cue$/i.test(f.name)) cueFile = f;
    });
    var cueText, bins = list.filter(function (f) { return /\.(bin|img)$/i.test(f.name); });
    if (cueFile) cueText = reader.readAsText(cueFile);
    else if (bins.length === 1 && bins[0].size === KNOWN_CUE.size) {
        cueText = KNOWN_CUE.cue.replace("MANICKARTS.bin", bins[0].name);
        log("No .cue given: using the standard layout of the Manic Karts CD.");
    } else throw new Error("Please choose your CD image's .cue file together with its .bin file(s).");
    var tracks = MK.parseCue(cueText);
    tracks.forEach(function (t) {
        if (!t.file) throw new Error("The .cue file doesn't name its .bin file.");
        t.blob = byName[t.file.toLowerCase()] || (bins.length === 1 ? bins[0] : null);
        if (!t.blob) throw new Error('The .cue file names "' + t.file + '": please choose that file too.');
    });
    function read(blob, off, len) { return new Uint8Array(reader.readAsArrayBuffer(blob.slice(off, off + len))); }

    /* 1. the game's files off the CD image */
    step("Reading your CD image", 0);
    var data = tracks[0];
    var mode = data.type, ssize = mode === "MODE1/2048" ? 2048 : 2352, doff = mode === "MODE2/2352" ? 24 : 16;
    if (!/^MODE/.test(mode)) throw new Error("Track 1 of this CD image isn't a data track.");
    var iso = MK.isoRoot(read, data.blob, data.start, ssize, doff);
    if (!iso) throw new Error("Track 1 of this CD image isn't an ISO 9660 data track.");
    if (!iso["K.EXE"] || !iso["KART.DAT"]) throw new Error("K.EXE and KART.DAT aren't on this CD: is it Manic Karts for the PC?");
    var exe = MK.isoFile(iso, "K.EXE"), dat = MK.isoFile(iso, "KART.DAT");
    var sha = Array.prototype.map.call(new Uint8Array(await crypto.subtle.digest("SHA-1", exe)),
        function (b) { return (b < 16 ? "0" : "") + b.toString(16); }).join("");
    log("K.EXE " + exe.length + " bytes, KART.DAT " + dat.length + " bytes");
    if (sha !== KEXE_SHA1) log("Warning: this K.EXE isn't the version the port was made from (the PC CD-ROM release); the disc may not work.");
    step("Unpacking KART.DAT", 0);
    var kart = MK.unkart(dat, exe, function (f) { step("Unpacking KART.DAT", f); });
    dat = null;
    log(Object.keys(kart).length + " files unpacked");

    /* 2. the music: the CD's audio tracks as ADPCM */
    var out = [];
    var audio = tracks.filter(function (t) { return t.type === "AUDIO" && t.start >= 0; });
    var totalBytes = 0, doneBytes = 0;
    var ends = audio.map(function (t) {
        var k = tracks.indexOf(t), nxt = tracks[k + 1];
        var end = nxt && nxt.blob === t.blob && nxt.start >= 0 ? nxt.start : t.blob.size;
        totalBytes += end - t.start;
        return end;
    });
    audio.forEach(function (t, k) {
        var enc = new MK.MusicEncoder(), end = ends[k], CH = 4 << 20;
        for (var p = t.start; p < end; p += CH) {
            enc.add(read(t.blob, p, Math.min(CH, end - p)));
            doneBytes += Math.min(CH, end - p);
            step("Converting the music", doneBytes / totalBytes);
        }
        var name = "mus" + (t.num < 10 ? "0" : "") + t.num + ".adp";
        out.push([name, enc.finish()]);
        log("Music: track " + t.num + ", " + Math.round((end - t.start) / 176400) + " seconds");
    });
    if (!audio.length) log("Warning: this CD image has no audio tracks, so the game will have no music.");

    /* 3. the game's data converted for the Dreamcast: the project's own Python tools */
    step("Starting Python", 0);
    importScripts(PYODIDE + "pyodide.js");
    var py = await loadPyodide({ indexURL: PYODIDE });
    py.setStdout({ batched: function (s) { log(s); } });
    py.setStderr({ batched: function (s) { log(s); } });
    var FS = py.FS;
    ["/mk", "/mk/tools", "/mk/mods", "/mk/extracted", "/mk/extracted/KART", "/mk/data"].forEach(function (d) { FS.mkdir(d); });
    TOOLS.forEach(function (t) { FS.writeFile("/mk/tools/" + t, fetchBytes("tools/" + t)); });
    MODS.forEach(function (m) { FS.writeFile("/mk/mods/" + m, fetchBytes("mods/" + encodeURIComponent(m))); });
    FS.writeFile("/mk/extracted/K.EXE", exe);
    Object.keys(kart).forEach(function (n) { FS.writeFile("/mk/extracted/KART/" + n, kart[n]); });
    kart = null;
    var vqDone = 0;
    self.mkVQ = function (idx, pal) {          /* build_vq.py's vq_texture, in JavaScript (much faster) */
        var r = MK.vqTexture(idx, pal);
        vqDone++;
        step("Compressing the karts", Math.min(1, vqDone / 32));
        return r;
    };
    py.runPython("import sys, os\nsys.path.insert(0, '/mk/tools')\nos.chdir('/mk')\nsys.setrecursionlimit(5000)");
    var runs = [["Converting the tracks", "build_assets"], ["Converting the menus", "build_front"], ["Converting the race tables", "build_race"]];
    for (i = 0; i < runs.length; i++) {
        step(runs[i][0], 0);
        await new Promise(function (r) { setTimeout(r, 0); });
        py.runPython("import runpy\nrunpy.run_path('/mk/tools/" + runs[i][1] + ".py', run_name='__main__')");
    }
    step("Compressing the karts", 0);
    py.runPython([
        "import build_vq",
        "from js import mkVQ",
        "from pyodide.ffi import to_js",
        "def _vq(idx, pal):",
        "    r = mkVQ(to_js(bytes(idx)), to_js(bytes([x for c in pal[:256] for x in c[:3]])))",
        "    return bytes(r.data.to_py()), r.err",
        "build_vq.vq_texture = _vq",
        "build_vq.main()"].join("\n"));
    FS.readdir("/mk/data").forEach(function (n) {
        if (n === "." || n === "..") return;
        out.push([n, FS.readFile("/mk/data/" + n)]);
    });
    log(out.length + " files for the disc");

    /* 4. the disc image: the prebuilt game program and the data, as mkdcdisc lays it out */
    step("Making the disc image", 0);
    var ipbin = fetchBytes("game/IP.BIN"), first = fetchBytes("game/1ST_READ.BIN");
    var pieces = MK.makeDisc({ ipbin: ipbin, firstRead: first, files: out, isoSectors: 316570,
        name: "manic-karts.cdi", progress: function (f) { step("Making the disc image", f); } });
    out = null;
    var blob = new Blob(pieces, { type: "application/octet-stream" });
    pieces = null;
    postMessage({ done: blob, size: blob.size });
}
