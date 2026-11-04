#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
musashi=${1:?Pass the path to a Musashi tree with generated m68kops.c}
emcc -O2 -I"$musashi" tools/browser_runner.c "$musashi/m68kcpu.c" \
    "$musashi/m68kops.c" "$musashi/m68kdasm.c" "$musashi/softfloat/softfloat.c" \
    -sMODULARIZE -sEXPORT_NAME=createSpriteRunner -sALLOW_MEMORY_GROWTH \
    -sEXPORTED_RUNTIME_METHODS=FS,callMain,stackSave,stackRestore -o tools/sprite_runner.js
