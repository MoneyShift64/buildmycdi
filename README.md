# Build My CDI

The website at [buildmycdi.cloud](https://buildmycdi.cloud): Dreamcast ports of classic games, built from
the visitor's own copy of the original game. The page reads their files, converts them and makes the
Dreamcast disc image (.cdi) entirely in their browser. Nothing is uploaded, and nothing needs installing.

No game data is included here.

| Folder | Game |
|---|---|
| `manickarts/` | Manic Karts (needs the PC CD-ROM as a BIN/CUE image) |

Each game's folder holds its page, the prebuilt Dreamcast program (`game/`), the project's own conversion
tools (`tools/`, run in the browser by [Pyodide](https://pyodide.org)) and `mk.js`, which does the rest
(reading the CD image, unpacking, music, kart compression and writing the disc image, laid out as
[mkdcdisc](https://gitlab.com/simulant/mkdcdisc) does).
