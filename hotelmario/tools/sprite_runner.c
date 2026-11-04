/* sprite_runner - run Hotel Mario's compiled-sprite routines in a 68000 emulator.
 *
 * Hotel Mario stores sprite frames as 68000 code that writes pixels straight
 * into a 384-byte-wide CLUT screen. This tool loads an OS-9 module, then for
 * each requested routine calls it with D0 = an address in a blank screen and
 * records which bytes it wrote.
 *
 * usage: sprite_runner module.o init <obj_size>
 *            -> runs the module's first entry point as init(obj, 0) and prints
 *               the obj's 32-bit words (the frame routine pointers it fills in)
 *        sprite_runner module.o draw <offset> [<offset>...]
 *            -> for each code offset writes  spr_<offset>.bin :
 *               int16 w, int16 h, int16 ox, int16 oy, then w*h pixels (0xffff = untouched)
 *        sprite_runner module.o dis <offset> <count>   -> disassemble
 *
 * build: cc -O2 -I Musashi -o sprite_runner sprite_runner.c Musashi/m68kcpu.c
 *          Musashi/m68kops.c Musashi/m68kdasm.c Musashi/softfloat/softfloat.c -lm
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "m68k.h"

#define MEM_SIZE   0x1000000
#define MOD_BASE   0x00100000           /* where the module is loaded */
#define DATA_BASE  0x00400000           /* fake static data area (A6 - 0x8000) */
#define OBJ_BASE   0x00500000           /* object passed to init */
#define SCR_BASE   0x00800000           /* fake screen */
#define SCR_W      384
#define SCR_H      1024
#define SCR_ORG_X  192                  /* where (0,0) of the sprite goes */
#define SCR_ORG_Y  512
#define STACK_TOP  0x00f00000
#define RET_MAGIC  0x00fff000           /* return address: stop when reached */

static uint8_t *mem;
static uint8_t *written;                /* 1 per screen byte that was written */
static int done;

static int in_scr(unsigned a) { return a >= SCR_BASE && a < SCR_BASE + SCR_W * SCR_H; }
unsigned int m68k_read_memory_8(unsigned int a)  { a &= 0xffffff; return mem[a]; }
unsigned int m68k_read_memory_16(unsigned int a) { a &= 0xffffff; return (mem[a] << 8) | mem[(a + 1) & 0xffffff]; }
unsigned int m68k_read_memory_32(unsigned int a) { return (m68k_read_memory_16(a) << 16) | m68k_read_memory_16(a + 2); }
unsigned int m68k_read_disassembler_16(unsigned int a) { return m68k_read_memory_16(a); }
unsigned int m68k_read_disassembler_32(unsigned int a) { return m68k_read_memory_32(a); }
void m68k_write_memory_8(unsigned int a, unsigned int v) {
    a &= 0xffffff; mem[a] = v;
    if (in_scr(a)) written[a - SCR_BASE] = 1;
}
void m68k_write_memory_16(unsigned int a, unsigned int v) { m68k_write_memory_8(a, v >> 8); m68k_write_memory_8(a + 1, v); }
void m68k_write_memory_32(unsigned int a, unsigned int v) { m68k_write_memory_16(a, v >> 16); m68k_write_memory_16(a + 2, v); }

static long load(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }
    long n = fread(mem + MOD_BASE, 1, MEM_SIZE - MOD_BASE, f);
    fclose(f);
    return n;
}

/* call code at MOD_BASE+off with D0, D1; returns D0 or -1 if it ran away */
static long call(unsigned off, unsigned d0, unsigned d1) {
    m68k_pulse_reset();
    unsigned sp = STACK_TOP - 4;
    m68k_write_memory_32(sp, RET_MAGIC);
    m68k_set_reg(M68K_REG_SP, sp);
    m68k_set_reg(M68K_REG_A7, sp);
    m68k_set_reg(M68K_REG_A6, DATA_BASE + 0x8000);
    m68k_set_reg(M68K_REG_D0, d0);
    m68k_set_reg(M68K_REG_D1, d1);
    m68k_set_reg(M68K_REG_A0, d0);
    m68k_set_reg(M68K_REG_PC, MOD_BASE + off);
    m68k_set_reg(M68K_REG_SR, 0x2700);
    for (int i = 0; i < 20000; i++) {
        m68k_execute(200);
        unsigned pc = m68k_get_reg(NULL, M68K_REG_PC);
        if ((pc & 0xffffff) == RET_MAGIC) return m68k_get_reg(NULL, M68K_REG_D0);
    }
    return -1;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "see source for usage\n"); return 1; }
    mem = calloc(MEM_SIZE, 1);
    written = calloc(SCR_W * SCR_H, 1);
    long n = load(argv[1]);
    /* RTS at the magic return address so execution there is harmless */
    mem[RET_MAGIC] = 0x60; mem[RET_MAGIC + 1] = 0xfe;      /* bra.s * */
    m68k_init();
    m68k_set_cpu_type(M68K_CPU_TYPE_68000);

    if (!strcmp(argv[2], "dis")) {
        unsigned pc = MOD_BASE + strtoul(argv[3], 0, 0);
        int cnt = atoi(argv[4]);
        char buf[256];
        while (cnt--) {
            int l = m68k_disassemble(buf, pc, M68K_CPU_TYPE_68000);
            printf("%06x  %s\n", pc - MOD_BASE, buf);
            pc += l;
        }
        return 0;
    }
    if (!strcmp(argv[2], "init")) {
        /* OS-9 subroutine module: execution offset at header 0x30 points at
         * a table: [name offset][entry offset] ... ; call the first entry */
        unsigned exe = m68k_read_memory_32(MOD_BASE + 0x30);
        unsigned entry = argc > 4 ? strtoul(argv[4], 0, 0) : m68k_read_memory_32(MOD_BASE + exe + 4);
        int words = strtol(argv[3], 0, 0) / 4;
        long r = call(entry, OBJ_BASE, 0);
        printf("init@%x -> %ld\n", entry, r);
        for (int i = 0; i < words; i++) {
            unsigned v = m68k_read_memory_32(OBJ_BASE + 4 * i);
            printf("%d %08x\n", i * 4, v);
        }
        return 0;
    }
    if (!strcmp(argv[2], "draw")) {
        for (int k = 3; k < argc; k++) {
            unsigned off = strtoul(argv[k], 0, 0);
            memset(mem + SCR_BASE, 0, SCR_W * SCR_H);
            memset(written, 0, SCR_W * SCR_H);
            long r = call(off, SCR_BASE + SCR_ORG_Y * SCR_W + SCR_ORG_X, 0);
            int x0 = SCR_W, y0 = SCR_H, x1 = -1, y1 = -1;
            for (int y = 0; y < SCR_H; y++)
                for (int x = 0; x < SCR_W; x++)
                    if (written[y * SCR_W + x]) {
                        if (x < x0) x0 = x; if (x > x1) x1 = x;
                        if (y < y0) y0 = y; if (y > y1) y1 = y;
                    }
            char name[64];
            snprintf(name, sizeof name, "spr_%06x.bin", off);
            if (r == -1 || x1 < 0) { printf("%x: %s\n", off, r == -1 ? "ran away" : "nothing drawn"); continue; }
            FILE *o = fopen(name, "wb");
            if (!o) { perror(name); return 1; }
            int16_t hdr[4] = { x1 - x0 + 1, y1 - y0 + 1, x0 - SCR_ORG_X, y0 - SCR_ORG_Y };
            fwrite(hdr, 2, 4, o);
            for (int y = y0; y <= y1; y++)
                for (int x = x0; x <= x1; x++) {
                    uint16_t v = written[y * SCR_W + x] ? mem[SCR_BASE + y * SCR_W + x] : 0xffff;
                    fwrite(&v, 2, 1, o);
                }
            fclose(o);
            printf("%x: %dx%d at (%d,%d)\n", off, hdr[0], hdr[1], hdr[2], hdr[3]);
        }
        return 0;
    }
    return 1;
}
