/* for wasm_unpack.c: the few C library functions the MPF code uses (defined in wasm_unpack.c) */
#include <stddef.h>
void *memset(void *d, int c, size_t n);
void *memcpy(void *d, const void *s, size_t n);
void *memmove(void *d, const void *s, size_t n);
