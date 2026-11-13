# The browser build

`tools/build_web.py` builds the port for the browser with Emscripten
(WebAssembly, WebGL 2, SDL3's Emscripten port) into `build-web/`. GitHub
Actions runs it on every push (`.github/workflows/web.yml`).

It is not playable yet. Done so far:

- The game's C compiles to WebAssembly with build.py's IR rewrites (returns
  widened, the R3000's divisions); the stages' symbols are prefixed on the IR
  instead of with objcopy.
- The shaders get a GLSL ES 3.00 header in place of `#version 330 core`, and
  the context asked for is OpenGL ES 3.0 (WebGL 2).
- extract.c's Windows calls (wide paths, free space) have web versions.

To do, before anything is published:

1. **The game's tables out of the wasm.** The exe has them zeroed and filled
   from the player's disc (tools/asset_scan.py, platform/fill.c). The web
   build keeps them for now, so no build is uploaded or published until it
   does the same.
2. **The main loop.** The game never returns from psx_main; the build uses
   ASYNCIFY, and VSync's wait (platform/etc.c) has to yield to the browser
   (emscripten_sleep) instead of SDL_DelayPrecise.
3. **The overlays' variables.** build.py puts them in COFF sections
   (`.data$dw3m1`...) that platform/memory.c resets on each overlay load;
   WebAssembly has no such sections, so they need another way to be found.
4. **Files.** The disc image chosen in the page, extracted into IndexedDB
   (IDBFS) as `data/`, and the memory card saved there.
5. **The page.** A file picker for the disc, the canvas, and the sound
   started on the first click (browsers require it).
6. crash.c (Windows crash reports) is left out.
