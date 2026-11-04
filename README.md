# Build My CDI

The website at [buildmycdi.cloud](https://buildmycdi.cloud): Dreamcast ports of classic games, built from
the visitor's own copy of the original game. The page reads their files, converts them and makes the
Dreamcast disc image (.cdi) entirely in their browser. Nothing is uploaded, and nothing needs installing.

No game data is included here.

| Folder | Game |
|---|---|
| `manickarts/` | Manic Karts (needs the PC CD-ROM as a BIN/CUE image) |
| `cruisnusa/` | Cruis'n USA (needs the N64 ROM, USA Rev 1) |
| `hotelmario/` | Hotel Mario (needs a raw Philips CD-i BIN image with 2352 bytes per sector) |

Each game's folder holds its page, its worker (the build steps), the prebuilt Dreamcast program (`game/`)
and the project's own conversion tools (`tools/`, run in the browser by [Pyodide](https://pyodide.org)).
`common/disc.js` does the rest: reading CD images, unpacking, music, kart compression, scrambling
1ST_READ.BIN and writing the disc image, laid out as [mkdcdisc](https://gitlab.com/simulant/mkdcdisc) does.

Cruis'n USA's prebuilt program has the four data segments its build makes from the ROM left empty
(`game/program.json` says where); the page makes them from the visitor's ROM and puts them in.

The [Hotel Mario builder](https://buildmycdi.cloud/hotelmario/) extracts the original disc files and
converts the stages, graphics, audio and cutscenes in the browser. It uses the Musashi 68000 emulator
compiled to WebAssembly to extract sprites. The finished download is `hotel-mario-dreamcst.cdi`.
