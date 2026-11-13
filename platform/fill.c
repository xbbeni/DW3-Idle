/* The game's own tables, put back from the player's files: the build zeroed
   them in the exe and listed where each comes from in <exe>.fill
   (tools/asset_scan.py). Runs first thing, before anything reads or writes
   the game's variables. Separate from platform.h: windows.h has its own RECT. */
#include <SDL3/SDL.h>
#include <stdio.h>
#include <string.h>

void native_log(const char *fmt, ...);

#ifdef __EMSCRIPTEN__
#include <stdint.h>

int native_web_fetch(const char *path); /* web.c */

/* The browser build: tools/web_fill.py zeroed the game's tables in dw3.wasm
   and listed them in dw3.fill (next to the page, in the virtual file
   system's root) by address, which in WebAssembly is the pointer itself */
int native_fill(const char *data) {
    char src[64], open[64] = "", file[1100];
    unsigned long want, addr, off, count, crc = 0, len;
    FILE *fill, *f = NULL;
    int ok = 1;

    fill = fopen("/dw3.fill", "r");
    if (fill == NULL || fscanf(fill, "crc %lx %lu", &want, &count) != 2) {
        native_log("no /dw3.fill: it comes with the page");
        return 0;
    }
    while (ok && fscanf(fill, "%lx %lu %63s %lu", &addr, &len, src, &off) == 4) {
        uint8_t *at = (uint8_t *)(uintptr_t)addr;

        if (strcmp(src, open) != 0) {
            if (f != NULL) {
                fclose(f);
            }
            snprintf(file, sizeof file, "%s/%s", data, src);
            native_web_fetch(file);
            f = fopen(file, "rb");
            strcpy(open, src);
        }
        ok = f != NULL && fseek(f, (long)off, SEEK_SET) == 0 && fread(at, 1, len, f) == len;
        crc = SDL_crc32((Uint32)crc, at, len);
        count--;
    }
    if (f != NULL) {
        fclose(f);
    }
    fclose(fill);
    if (!ok || count != 0 || crc != want) {
        native_log("the game's tables from %s don't match (%s): extract the disc again", data, ok ? "checksum" : src);
        return 0;
    }
    return 1;
}
#else
#include <windows.h>

int native_fill(const char *data) {
    char path[MAX_PATH + 8], src[64], open[64] = "", file[1100];
    unsigned long want, rva, off, count, crc = 0, len;
    uint8_t *base = (uint8_t *)GetModuleHandleA(NULL);
    FILE *fill, *f = NULL;
    int ok = 1;

    GetModuleFileNameA(NULL, path, MAX_PATH);
    strcpy(strrchr(path, '.'), ".fill");
    fill = fopen(path, "r");
    if (fill == NULL || fscanf(fill, "crc %lx %lu", &want, &count) != 2) {
        native_log("no %s: it comes with the exe", path);
        return 0;
    }
    while (ok && fscanf(fill, "%lx %lu %63s %lu", &rva, &len, src, &off) == 4) {
        DWORD old;

        if (strcmp(src, open) != 0) {
            if (f != NULL) {
                fclose(f);
            }
            snprintf(file, sizeof file, "%s/%s", data, src);
            f = fopen(file, "rb");
            strcpy(open, src);
        }
        VirtualProtect(base + rva, len, PAGE_READWRITE, &old);
        ok = f != NULL && fseek(f, (long)off, SEEK_SET) == 0 && fread(base + rva, 1, len, f) == len;
        VirtualProtect(base + rva, len, old, &old);
        crc = SDL_crc32((Uint32)crc, base + rva, len);
        count--;
    }
    if (f != NULL) {
        fclose(f);
    }
    fclose(fill);
    if (!ok || count != 0 || crc != want) {
        native_log("the game's tables from %s don't match (%s): extract the disc again", data, ok ? "checksum" : src);
        return 0;
    }
    return 1;
}
#endif
