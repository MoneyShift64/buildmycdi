"use strict";
importScripts("../common/disc.js", "tools/sprite_runner.js");

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.1/full/";
const OUTPUT = "hotel-mario-dreamcst.cdi";
const TOOLS = [
    "extract_disc", "build_image", "make_dc_assets", "dump_sprites",
    "make_stage_extensions", "make_ui_assets", "make_frontend_assets", "make_movie_assets"
];
const log = message => postMessage({ log: message });
const step = (name, frac = 0) => postMessage({ step: name, frac });

async function fetchBytes(path) {
    const response = await fetch(path);
    if (!response.ok) throw new Error("Couldn't load " + path + " (" + response.status + ")");
    return new Uint8Array(await response.arrayBuffer());
}

async function build(files, pyodideUrl) {
    if (files.length !== 1) throw new Error("Choose one raw Hotel Mario CD-i BIN image.");
    const file = files[0];
    const label = await file.slice(16 * 2352 + 25, 16 * 2352 + 30).text();
    if (file.size % 2352 || label !== "CD-I ") {
        throw new Error("Choose a raw CD-i image with 2352 bytes per sector.");
    }

    step("Starting the conversion tools");
    importScripts(pyodideUrl + "pyodide.js");
    const py = await loadPyodide({ indexURL: pyodideUrl });
    await py.loadPackage(["numpy", "pillow"]);
    py.setStdout({ batched: log });
    py.setStderr({ batched: log });
    const fs = py.FS;
    for (const path of ["/input", "/hm", "/hm/tools", "/hm/dc", "/hm/data"]) fs.mkdir(path);
    if (fs.filesystems.WORKERFS) {
        fs.mount(fs.filesystems.WORKERFS, { blobs: [{ name: "disc.bin", data: file }] }, "/input");
    } else {
        fs.writeFile("/input/disc.bin", new Uint8Array(await file.arrayBuffer()), { canOwn: true });
    }
    await Promise.all(TOOLS.map(async name => {
        const path = "tools/" + name + ".py";
        fs.writeFile("/hm/" + path, await fetchBytes(path));
    }));
    fs.writeFile("/hm/dc/obj_list.h", await fetchBytes("dc/obj_list.h"));

    let output = [];
    const runner = await createSpriteRunner({
        noInitialRun: true,
        wasmBinary: await fetchBytes("tools/sprite_runner.wasm"),
        print: line => output.push(line),
        printErr: log
    });
    py.globals.set("run_sprite", (module, args, cwd) => {
        output = [];
        runner.FS.writeFile("/module.bin", fs.readFile(module));
        const stack = runner.stackSave();
        try {
            const status = runner.callMain(["/module.bin", ...args]);
            if (status) throw new Error("Sprite conversion failed (exit " + status + ")");
            for (const name of runner.FS.readdir("/")) {
                if (!/^spr_[0-9a-f]+\.bin$/.test(name)) continue;
                fs.writeFile(cwd + "/" + name, runner.FS.readFile("/" + name));
                runner.FS.unlink("/" + name);
            }
        } finally {
            runner.stackRestore(stack);
            runner.FS.unlink("/module.bin");
        }
        return output.join("\n") + "\n";
    });
    py.runPython(`
import os, sys
from types import SimpleNamespace
from pyodide.ffi import to_js
sys.path.insert(0, "/hm/tools")
os.chdir("/hm")
from extract_disc import main as extract
from build_image import main as build_image
import dump_sprites, make_dc_assets
from make_movie_assets import main as make_movies

def run_sprite_command(argv, cwd=None, capture_output=True, text=False):
    result = run_sprite(argv[1], to_js(argv[2:]), cwd or os.getcwd())
    return SimpleNamespace(stdout=result if text else result.encode())

for tool in (dump_sprites, make_dc_assets):
    tool.subprocess = SimpleNamespace(run=run_sprite_command)
`);
    step("Reading your CD-i image");
    py.runPython('extract("/input/disc.bin", "/hm/extracted")');
    if (fs.filesystems.WORKERFS) fs.unmount("/input");
    else fs.unlink("/input/disc.bin");

    step("Converting stages, graphics and music");
    py.runPython(`
build_image("/hm/extracted", "/hm/work")
sys.argv = ["make_dc_assets.py", "/hm/extracted", "/hm/data"]
make_dc_assets.main()
`);
    step("Converting cutscenes");
    py.runPython('make_movies("/hm/extracted", "/hm/data", "/hm/previews")');
    py.runPython(`
import shutil
for path in ("extracted", "work", "previews", "/tmp/hm_assets"):
    shutil.rmtree(path)
`);

    step("Making the disc image");
    const [ipbin, firstRead] = await Promise.all([
        fetchBytes("game/IP.BIN"), fetchBytes("game/1ST_READ.BIN")
    ]);
    const assets = fs.readdir("/hm/data").filter(name => name !== "." && name !== "..")
        .map(name => {
            const path = "/hm/data/" + name;
            const bytes = fs.readFile(path);
            fs.unlink(path);
            return [name, bytes];
        });
    const pieces = MK.makeDisc({
        ipbin, firstRead, files: assets, isoSectors: 316570, dir: "DATA",
        volume: "HOTEL MARIO", name: OUTPUT, progress: frac => step("Making the disc image", frac)
    });
    const blob = new Blob(pieces, { type: "application/octet-stream" });
    postMessage({ done: blob, size: blob.size });
}

onmessage = event => {
    build(event.data.files, event.data.pyodide || PYODIDE).catch(error => {
        postMessage({ error: error.message || String(error) });
    });
};
