/*
 * wasm_unpack.c - the ClayFighter: Sculptor's Cut port's tools/mpf_unpack.c, for the browser:
 * the game's own MPF decompressor (src/mpf in the port's repo) built to WebAssembly with no C
 * library.  Build (from the port's repo folder, with clang and wasm-ld):
 *   clang --target=wasm32 -O2 -nostdlib -ffreestanding -w -DTARGET_PORT -I<this folder>/wasm_include -Isrc/include -Isrc/mpf \
 *     -Wl,--no-entry -Wl,--export-dynamic -Wl,--initial-memory=33554432 -Wl,--max-memory=268435456 \
 *     <this file> src/mpf/mpfcomp.c src/mpf/mpfcomp2.c src/mpf/mpfbstrm.c src/mpf/mpfrle_enc.c \
 *     src/mpf/mpfrle_dec.c src/mpf/mpfplat.c -o mpf_unpack.wasm
 * Exports: mem_alloc(n); mem_mark() keeps what is allocated so far (the ROM) and mem_reset()
 * frees everything after it; unpack(in, offset) -> bytes written, at out_ptr().
 */
#include <stdarg.h>
#include "common.h"
#include "mpfcomp.h"

/* a bump allocator over the wasm heap, emptied by mem_reset() before each unpack */
extern unsigned char __heap_base;
static unsigned long sTop, sFloor;
__attribute__((export_name("mem_reset"))) void mem_reset(void) {
    sTop = sFloor ? sFloor : ((unsigned long)&__heap_base + 15) & ~15ul;
}
__attribute__((export_name("mem_mark"))) void mem_mark(void) { sFloor = sTop; }
static void Grow(unsigned long end) {
    unsigned long have = __builtin_wasm_memory_size(0) * 65536ul;
    if (end > have) __builtin_wasm_memory_grow(0, (end - have + 65535) / 65536);
}
void *memset(void *d, int c, size_t n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
void *memcpy(void *d, const void *s, size_t n) { unsigned char *p = d; const unsigned char *q = s; while (n--) *p++ = *q++; return d; }
void *memmove(void *d, const void *s, size_t n) {
    unsigned char *p = d; const unsigned char *q = s;
    if (p < q) while (n--) *p++ = *q++; else { p += n; q += n; while (n--) *--p = *--q; }
    return d;
}
__attribute__((export_name("mem_alloc"))) void *mem_alloc(unsigned long n) {
    unsigned long p;
    if (sTop == 0) mem_reset();
    p = sTop; sTop = (sTop + n + 15) & ~15ul; Grow(sTop);
    return (void *)p;
}
static u8 *sOut;
__attribute__((export_name("out_ptr"))) u8 *out_ptr(void) { return sOut; }
static void *calloc(unsigned long a, unsigned long n) { return memset(mem_alloc(a * n), 0, a * n); }
static void free(void *p) { (void)p; }

#include "common.h"
#include "mpfcomp.h"

void mpFatalError(const char *msg) { (void)msg; __builtin_trap(); }
void mpFatalErrorf(const char *fmt, ...) { (void)fmt; __builtin_trap(); }
void mpDebugPrintf(const char *fmt, ...) { (void)fmt; }
/* the BSS mpfcomp2.c uses (sizes as in the game; pointers get 8 bytes on a 64-bit computer) */
char B_800ECFC0[16] __attribute__((aligned(16)));
char B_800ECFC4[24] __attribute__((aligned(16)));
char B_800ECFD0[2048] __attribute__((aligned(16)));
char B_800ED3D0[16] __attribute__((aligned(16)));
char B_800ED4E0[2080] __attribute__((aligned(16)));
char B_800ED8F0[2064] __attribute__((aligned(16)));
char B_800EDCF8[16] __attribute__((aligned(16)));
char B_800EDCFC[16] __attribute__((aligned(16)));
char B_800EDD00[16] __attribute__((aligned(16)));
char B_800EDD04[16] __attribute__((aligned(16)));
char B_800EDD08[16] __attribute__((aligned(16)));
char B_800EDD0C[16] __attribute__((aligned(16)));
char B_800EDD10[2048] __attribute__((aligned(16)));
char B_800EE110[16] __attribute__((aligned(16)));
char B_800EE220[2080] __attribute__((aligned(16)));
char B_800EE630[2064] __attribute__((aligned(16)));
char B_800EEA38[16] __attribute__((aligned(16)));
char B_800EEA3C[16] __attribute__((aligned(16)));
char B_800EEA40[16] __attribute__((aligned(16)));
char B_800EEA44[16] __attribute__((aligned(16)));
char B_800EEA48[16] __attribute__((aligned(16)));
char B_800EEA4C[16] __attribute__((aligned(16)));
char B_800EEA50[32] __attribute__((aligned(16)));
char B_800EEA60[1096] __attribute__((aligned(16)));
char B_800EEC84[16] __attribute__((aligned(16)));
char B_800EEC88[16] __attribute__((aligned(16)));
char B_800EEC8C[16] __attribute__((aligned(16)));
char B_800EEC90[16] __attribute__((aligned(16)));
char B_800EEC94[16] __attribute__((aligned(16)));
char B_800EEC98[16] __attribute__((aligned(16)));
char B_800EEC9C[16] __attribute__((aligned(16)));
char B_800EECA0[1096] __attribute__((aligned(16)));
char B_800EEEC4[16] __attribute__((aligned(16)));
char B_800EEEC8[16] __attribute__((aligned(16)));
char B_800EEECC[16] __attribute__((aligned(16)));
char B_800EEED0[16] __attribute__((aligned(16)));
char B_800EEED4[16] __attribute__((aligned(16)));
char B_800EEED8[16] __attribute__((aligned(16)));
char gN64MemoryManager[64];
void *mpLinkedMemoryManager_AllocPtr(void *m, u32 n, s32 e) { (void)m; (void)e; return calloc(1, n + 16); }
void mpLinkedMemoryManager_FreePtr(void *m, void *p) { (void)m; free(p); }
void osCreateMesgQueue(void *q, void *m, s32 n) { (void)q; (void)m; (void)n; }
s32 osSendMesg(void *q, void *m, s32 f) { (void)q; (void)m; (void)f; return 0; }
static void Swap(u8 *a, u8 *b, u32 n) { while (n--) { u8 t = *a; *a++ = *b; *b++ = t; } }
void func_800A3C80(void *base, u32 count, u32 size, s32 (*compare)(const void *, const void *)) {
    u8 *b = base, *end, *p, *q;
    u32 gap, gs;
    if (count < 2) return;
    if (count == 2) { if (compare(b, b + size) > 0) Swap(b, b + size, size); return; }
    gap = 1;
    while (gap < count) gap = gap * 2 + 1;
    end = b + count * size;
    for (gap >>= 1; gap != 0; gap >>= 1) {
        gs = gap * size;
        for (p = b + gs; p < end; p += size)
            for (q = p - gs; q >= b && q < end; q -= gs) {
                if (compare(q, q + gs) <= 0) break;
                Swap(q, q + gs, size);
            }
    }
}
void port_yield(void) {}


__attribute__((export_name("unpack"))) s32 unpack(u8 *in, u32 off) {
    u32 raw = READ_BE32(in + off + 8);
    u8 *out = mem_alloc(raw + 64);
    s32 got = mpDataDecompress(out, in + off);
    sOut = out;
    return got;
}
