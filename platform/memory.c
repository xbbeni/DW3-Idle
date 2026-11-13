/* The memory the PSX had at fixed places: the heap and the two overlay areas.
   All of it static, so it sits in the exe's image below 16 MB: the GPU's
   ordering tables keep 24-bit addresses (main.c checks it at startup). */
#include "platform.h"
#include <stdlib.h>
#include <string.h>
#include "../include/native.h"

/* HEAP_BASE..+NATIVE_HEAP_SIZE (the game's heap.h, patched) */
uint32_t HEAP_BASE[NATIVE_HEAP_SIZE / 4];

/* The mode overlays load at OVERLAY_VRAM and the stages at STAGE_VRAM: only
   data now (the code is linked in), but the game still copies the files
   there. The largest PRO (CARDGAME) took 0x23130 bytes on the PSX. */
uint8_t OVERLAY_VRAM[512 * 1024];
uint8_t STAGE_VRAM[512 * 1024];

/* Each overlay's variables (.data and .bss) sit between these markers: the
   mode overlays in .data$dw3m1, the stages and WFIGHTMN/TS in .data$dw3s1
   (build.py's overlay_sections). The linker sorts .data$* by name, so with
   main's (.data$dw3g1, and the platform's SAVED) before them, game_vars_begin
   to ovl_sub_end is all of the game's state (state.c). */
#ifdef __EMSCRIPTEN__
/* The browser build: tools/build_web.py puts them in the segments dw3_g,
   dw3_m and dw3_s, which wasm-ld brackets with __start_/__stop_ symbols. The
   savestates (state.c) still take game_vars_begin..ovl_sub_end, which here
   isn't one range: they are left out of the web build's features. */
extern uint8_t __start_dw3_m[], __stop_dw3_m[], __start_dw3_s[], __stop_dw3_s[];
uint8_t game_vars_begin[1] = {1}, ovl_sub_end[1] = {1};
#define ovl_mode_begin __start_dw3_m
#define ovl_mode_end __stop_dw3_m
#define ovl_sub_begin __start_dw3_s
#undef ovl_sub_end
#define OVL_SUB_END __stop_dw3_s
#else
__attribute__((section(".data$dw3g0"))) uint8_t game_vars_begin[1] = {1};
__attribute__((section(".data$dw3m0"))) uint8_t ovl_mode_begin[1] = {1};
__attribute__((section(".data$dw3m2"))) uint8_t ovl_mode_end[1] = {1};
__attribute__((section(".data$dw3s0"))) uint8_t ovl_sub_begin[1] = {1};
__attribute__((section(".data$dw3s2"))) uint8_t ovl_sub_end[1] = {1};
#define OVL_SUB_END ovl_sub_end
#endif
static uint8_t *s_initial[2];

/* The initial values, before the game runs */
void native_snapshot_overlays(void) {
    s_initial[0] = native_must(malloc(ovl_mode_end - ovl_mode_begin), "the mode overlays' variables");
    memcpy(s_initial[0], ovl_mode_begin, ovl_mode_end - ovl_mode_begin);
    s_initial[1] = native_must(malloc(OVL_SUB_END - ovl_sub_begin), "the stage overlays' variables");
    memcpy(s_initial[1], ovl_sub_begin, OVL_SUB_END - ovl_sub_begin);
}

/* An overlay load: on the PSX the file is copied again, so the variables of
   the area's overlays (only one was there) go back to what the file has */
void native_reset_overlays(int sub) {
    if (getenv("DW3_NORESET") != NULL) { /* debug: is a bug the reset's? */
        return;
    }
    LOGC(LOG_OVL, "ovl: %s variables reset", sub ? "stage" : "mode overlay");
    if (sub) {
        memcpy(ovl_sub_begin, s_initial[1], OVL_SUB_END - ovl_sub_begin);
    } else {
        memcpy(ovl_mode_begin, s_initial[0], ovl_mode_end - ovl_mode_begin);
    }
}
