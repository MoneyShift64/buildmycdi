/* Keep the sprite runner reusable within one WebAssembly instance. */
#define main sprite_runner_main
#include "sprite_runner.c"
#undef main

int main(int argc, char **argv) {
    int status = sprite_runner_main(argc, argv);
    free(written);
    free(mem);
    written = NULL;
    mem = NULL;
    return status;
}
